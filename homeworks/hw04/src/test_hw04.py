"""проверки генерации, сохранения результатов, cfg и конфликтов."""
from copy import deepcopy
import itertools
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from flow import analyze, build_cfg, interference, liveness
from ir import compile_source, constant, format_ir, generate, ref
from optimize import PASSES, optimize
from runtime import ExecutionError, compare, execute
from checker import check

ROOT = Path(__file__).resolve().parents[1]


class HW04Tests(unittest.TestCase):
    def compile(self, source):
        result = compile_source(source)
        self.assertEqual(result["errors"], [])
        return result["ir"]

    def verify(self, source, inputs, expected, function="main"):
        before = self.compile(source)
        # все наборы проходов проверяются независимо, включая отсутствие оптимизаций.
        for count in range(len(PASSES) + 1):
            for passes in itertools.combinations(PASSES, count):
                after = optimize(before, passes)
                for args, result in zip(inputs, expected):
                    with self.subTest(passes=passes, args=args):
                        self.assertEqual(execute(before, function, args), result)
                        self.assertEqual(execute(after, function, args), result)
        return before, optimize(before)

    def test_no_temp_name_collision(self):
        self.verify("main(t0:int) returns r:int {r=t0+1;}", [[3]], [[4]])
        self.verify("increment(a:int) returns r:int {r=a+1;}", [[2]], [[3]], "increment")

    def test_arithmetic_expected(self):
        self.verify("main(a:int,b:int) returns r:int {r=-(a+2)*b/3;}",
                    [[4, 5], [-7, 2], [0, -2]], [[-10], [3], [1]])

    def test_int32_and_division(self):
        for expression, expected in (("2147483647+1", -2147483648),
                                     ("-2147483648/-1", -2147483648),
                                     ("4294967295", -1), ("-7/3", -2),
                                     ("7/-3", -2), ("-7/-3", 2),
                                     ("65536*65536", 0)):
            self.verify(f"main() returns r:int {{r={expression};}}", [[]], [[expected]])

    def test_diamond(self):
        self.verify("main(a:int) returns r:int uses x:int {x=1;if(a>0){x=4;}else{x=7;}r=x;}",
                    [[-1], [0], [5]], [[7], [7], [4]])

    def test_loop_and_nested_if(self):
        self.verify((ROOT / "examples/control.funny").read_text(),
                    [[-2], [0], [1], [2], [3], [4], [8]],
                    [[0], [0], [0], [1], [3], [5], [13]])

    def test_short_circuit(self):
        for condition, expected in (("false and 1/0>0", 2), ("true or 1/0>0", 1),
                                    ("false -> 1/0>0", 1), ("true and 3>2", 1),
                                    ("false or 3>2", 1), ("true -> 3<2", 2),
                                    ("not (false or 1<2)", 2)):
            self.verify(f"main() returns r:int {{if({condition}){{r=1;}}else{{r=2;}}}}",
                        [[]], [[expected]])

    def test_forward_recursion(self):
        self.verify("main(n:int) returns r:int {r=fact(n);} "
                    "fact(n:int) returns r:int {if(n<2){r=1;}else{r=n*fact(n-1);}}",
                    [[0], [1], [5]], [[1], [1], [120]])

    def test_mutual_recursion(self):
        self.verify("main(n:int) returns r:int {if(n==0){r=0;}else{r=other(n-1);}} "
                    "other(n:int) returns r:int {if(n==0){r=1;}else{r=main(n-1);}}",
                    [[0], [1], [8], [9]], [[0], [1], [0], [1]])

    def test_array_alias_independence(self):
        self.verify("main(a:int[]) returns r:int uses b:int[],c:int[] {"
                    "b=a;c=b;b[0]=99;r=a[0]+c[0]+b[0];}", [[[2, 3]]], [[103]])

    def test_argument_order(self):
        before = self.compile("f(x:int,y:int) returns r:int {r=x+y;} "
                              "main(a:int[]) returns r:int {r=f(1/0,a[0]);}")
        for program in (before, optimize(before)):
            with self.assertRaisesRegex(ExecutionError, "Деление на ноль"):
                execute(program, "main", [[]])

    def test_errors_preserved_when_result_dead(self):
        cases = (("x=1/a;", "int", [0], "Деление на ноль"),
                 ("x=a[0];", "int[]", [[]], "Индекс вне границ массива"),
                 ("b=a;b[-1]=2;", "int[]", [[1]], "Индекс вне границ массива"),
                 ("x=bad(a);", "int", [0], "Деление на ноль"))
        for statement, type_name, args, error in cases:
            before = self.compile("bad(a:int) returns r:int {r=1/a;} "
                                  f"main(a:{type_name}) returns r:int uses x:int,b:int[] "
                                  f"{{{statement}r=7;}}")
            for count in range(len(PASSES) + 1):
                for passes in itertools.combinations(PASSES, count):
                    after = optimize(before, passes)
                    for program in (before, after):
                        with self.assertRaisesRegex(ExecutionError, error):
                            execute(program, "main", args)
                    self.assertTrue(compare(before, after, "main", args)["equal"])

    def test_does_not_simplify_zero_division(self):
        for expression, expected in (("0/a", 0), ("a/a", 1)):
            source = f"main(a:int) returns r:int {{r={expression};}}"
            before = self.compile(source)
            self.verify(source, [[2]], [[expected]])
            for program in (before, optimize(before)):
                with self.assertRaisesRegex(ExecutionError, "Деление на ноль"):
                    execute(program, "main", [0])

    def test_cse_invalidates_changed_operand(self):
        self.verify("main(a:int,b:int) returns r:int uses x:int,y:int,z:int {"
                    "x=a;y=x+b;x=x+1;z=x+b;r=y+z;}", [[2, 3]], [[11]])

    def test_constants_do_not_cross_branches(self):
        self.verify("main(a:int) returns r:int uses x:int {"
                    "x=2;if(a>0){x=3;}else{x=4;}r=x+1;}", [[-1], [1]], [[5], [4]])

    def test_nested_logical_merges(self):
        self.verify("main(a:int,b:int) returns r:int {"
                    "if((a>0 and b>0) or (a<0 and b<0)){r=1;}else{r=0;}}",
                    [[1, 1], [-1, -1], [-1, 1], [0, 0]], [[1], [1], [0], [0]])

    def test_diagnostics(self):
        cases = (("main() returns r:int {r=@;}", "lexical", "@"),
                 ("main() returns r:int {r=;}", "syntax", "Ожидалось выражение"),
                 ("main() returns r:int {r=missing;}", "semantic", "Неизвестное имя"),
                 ("main() returns r:int {if(forall(x:int | x==x)){r=1;}else{r=0;}}",
                  "ir", "Квантор в исполняемом выражении"),
                 ((ROOT / "examples/bad_types.funny").read_text(), "semantic", "только для чтения"))
        for source, phase, message in cases:
            with self.subTest(phase=phase):
                result = compile_source(source)
                self.assertIsNone(result["ir"])
                self.assertEqual(result["errors"][0]["phase"], phase)
                self.assertIn(message, "\n".join(error["message"] for error in result["errors"]))
                self.assertIn("line", result["errors"][0]["pos"])

    def test_annotated_ast_api(self):
        checked = check("main(a:int) returns r:int { r=a*2+1; }")
        ir = generate(checked["ast"])
        code = ir["functions"][0]["code"]
        self.assertEqual([i.get("operator") for i in code if i["op"] == "binary"], ["*", "+"])
        self.assertEqual(ir["functions"][0]["types"]["%t0"], "int")
        self.assertEqual(execute(ir, "main", [4]), [9])

    def test_tuple_and_array_copy(self):
        source = (ROOT / "examples/calls_arrays.funny").read_text()
        supplied = [[4, 8]]
        self.verify(source, [supplied, [[-1]]], [[11], [0]])
        self.verify((ROOT / "examples/arithmetic.funny").read_text(),
                    [[2, 3], [-2, 9], [2147483647, 1]], [[10], [14], [0]])
        for program in (self.compile(source), optimize(self.compile(source))):
            with self.assertRaisesRegex(ExecutionError, "Индекс вне границ массива"):
                execute(program, "main", [[]])
        execute(self.compile(source), "main", supplied)
        self.assertEqual(supplied, [[4, 8]])

    def test_const_algebra_dce_effect(self):
        before = self.compile("main(a:int) returns r:int uses c:int,dead:int "
                              "{c=2+3;dead=c*9;r=a+c*0;}")
        after = optimize(before)
        self.assertLess(analyze(after)["main"]["metrics"]["instructions"],
                        analyze(before)["main"]["metrics"]["instructions"])
        self.assertFalse(any(i["op"] == "binary" for i in after["functions"][0]["code"]))
        self.assertEqual(execute(after, "main", [23]), [23])

    def test_cse_effect(self):
        before = self.compile("main(a:int,b:int) returns r:int {r=(a+b)*(a+b);}")
        after = optimize(before, ["cse", "dce"])
        operators = [i.get("operator") for i in after["functions"][0]["code"]]
        self.assertEqual(operators.count("+"), 1)
        self.assertEqual(execute(after, "main", [3, 4]), [49])

    def test_copy_propagation_removes_redundant_copies(self):
        before = self.compile((ROOT / "examples/arithmetic.funny").read_text())
        after = optimize(before)
        code = after["functions"][0]["code"]
        self.assertFalse(any(ins["op"] == "copy" for ins in code))
        self.assertEqual(analyze(after)["main"]["metrics"]["instructions"], 3)
        for args, expected in (([2, 3], [10]), ([-2, 9], [14]), ([2147483647, 1], [0])):
            self.assertEqual(execute(after, "main", args), expected)
        without_copy = optimize(before, [name for name in PASSES if name != "copy"])
        self.assertTrue(any(ins["dest"] == ["%t6"] for ins in without_copy["functions"][0]["code"]))
        self.assertEqual(optimize(after), after)

    def test_copy_propagation_invalidates_sources_and_targets(self):
        sources = (
            ("x=a;if(a==0){x=b;}y=x;z=y;x=x+1;r=y+z+x;", [[0, 2], [3, 8]], [[7], [10]]),
            ("x=a;if(a==0){x=b;}y=x;z=y;y=y+1;r=z+y;", [[0, 2], [3, 8]], [[5], [7]]),
            ("x=a;y=x;z=y;x=b;r=y+z+x;", [[2, 9], [-3, 5]], [[13], [-1]]),
        )
        for body, inputs, expected in sources:
            self.verify("main(a:int,b:int) returns r:int uses x:int,y:int,z:int {" + body + "}",
                        inputs, expected)

    def test_copy_propagation_stays_within_block(self):
        self.verify("main(a:int,b:int) returns r:int uses y:int {"
                    "y=a;if(b>0){y=b;}r=y;}", [[2, 9], [2, -9]], [[9], [2]])

    def test_copy_propagation_preserves_array_values(self):
        self.verify("main(a:int[]) returns r:int uses b:int[],c:int[] {"
                    "b=a;if(length(a)>0){b=a;}c=b;b[0]=9;r=a[0]+b[0]+c[0];}",
                    [[[2]], [[-3]]], [[13], [3]])

    def test_cse_after_copy_source_overwrite(self):
        before = self.compile("main(a:int) returns r:int uses x:int,y:int,z:int {r=a;}")
        # прямая запись результата операции в исходную переменную также допустима в ir.
        before["functions"][0]["code"] = [
            {"op": "binary", "operator": "+", "dest": ["y"], "args": [ref("a"), constant(1)]},
            {"op": "copy", "dest": ["x"], "args": [ref("y")]},
            {"op": "binary", "operator": "+", "dest": ["y"], "args": [ref("x"), constant(1)]},
            {"op": "binary", "operator": "+", "dest": ["z"], "args": [ref("y"), constant(1)]},
            {"op": "return", "dest": [], "args": [ref("z")]},
        ]
        for passes in (("copy", "cse"), ("copy", "cse", "dce"), PASSES):
            after = optimize(before, passes)
            self.assertEqual(execute(after, "main", [4]), [7])
            self.assertEqual(optimize(after, passes), after)

    def test_loop_live_values_not_deleted(self):
        before = self.compile("main(n:int) returns r:int uses i:int {r=0;i=0;"
                              "while(i<n){r=r+i;i=i+1;}}")
        after = optimize(before)
        self.assertEqual(execute(after, "main", [5]), [10])
        blocks = liveness(after["functions"][0])
        self.assertTrue(any({"i", "n", "r"} <= b["live_in"] for b in blocks))

    def test_cfg_edges_and_boundaries(self):
        program = self.compile("main(a:int) returns r:int {if(a>0){r=1;}else{r=2;}}")
        blocks = build_cfg(program["functions"][0])
        self.assertEqual([b["successors"] for b in blocks], [[1, 2], [3], [3], []])
        self.assertEqual(blocks[3]["predecessors"], [1, 2])
        self.assertEqual(sum(b["end"]-b["start"] for b in blocks),
                         len(program["functions"][0]["code"]))

    def test_interference_known_graph(self):
        program = self.compile("main(a:int,b:int) returns r:int {r=(a+1)*(b+2);}")
        graph = interference(program["functions"][0])
        self.assertEqual(graph["edges"], {"%t0": ["%t1"], "%t1": ["%t0"], "%t2": []})
        self.assertEqual(graph["color_count"], 2)
        self.assertEqual(graph["colors"]["%t2"], 0)

    def test_tuple_results_conflict(self):
        program = self.compile("pair(a:int) returns x:int,y:int {x=a;y=a+1;} "
                               "main(a:int) returns r:int uses x:int,y:int {x,y=pair(a);r=x+y;}")
        graph = interference(program["functions"][1])
        self.assertIn("%t1", graph["edges"]["%t0"])

    def test_coloring_valid_and_deterministic(self):
        for path in (ROOT / "examples").glob("*.funny"):
            if path.name.startswith("bad"):
                continue
            for program in (self.compile(path.read_text()), optimize(self.compile(path.read_text()))):
                for function in program["functions"]:
                    graph = interference(function)
                    self.assertEqual(graph, interference(function))
                    for name, neighbors in graph["edges"].items():
                        for other in neighbors:
                            self.assertIn(name, graph["edges"][other])
                            self.assertNotEqual(graph["colors"][name], graph["colors"][other])

    def test_spec_annotations_preserved(self):
        before = self.compile((ROOT / "examples/contracts.funny").read_text())
        specs = [i for i in before["functions"][0]["code"] if i["op"] == "spec"]
        self.assertEqual([i["role"] for i in specs],
                         ["requires", "invariant", "assume", "assert", "ensures"])
        self.assertEqual(specs, [i for i in optimize(before)["functions"][0]["code"] if i["op"] == "spec"])
        self.assertEqual(execute(optimize(before), "main", [[9]]), [9])
        text = format_ir(before)
        self.assertIn("forall(j:int | (j == j))", text)
        self.assertIn("spec ensures (r == a[0])", text)
        self.assertNotIn("(AST", text)

    def test_pass_selection_and_no_mutation(self):
        before = self.compile("main() returns r:int {r=2+3;}")
        snapshot = deepcopy(before)
        self.assertEqual(optimize(before, []), before)
        after = optimize(before)
        self.assertEqual(before, snapshot)
        self.assertEqual(optimize(after), after)
        with self.assertRaises(ValueError):
            optimize(before, ["unknown"])

    def test_execution_errors_and_limits(self):
        program = self.compile("main(a:int) returns r:int {r=a;while(true){r=r+1;}}")
        for arguments in ([], [True], [[1]]):
            with self.assertRaises(ExecutionError):
                execute(program, "main", arguments)
        self.assertTrue(compare(program, optimize(program), "main", [0], 30)["inconclusive"])
        with self.assertRaisesRegex(ExecutionError, "Неизвестная функция"):
            execute(program, "missing", [0])
        with self.assertRaisesRegex(ExecutionError, "неинициализированной"):
            execute(self.compile("main() returns r:int {assert true;}"), "main")

    def test_generated_expression_results(self):
        rng = random.Random(404)
        # независимый эталон: обычная арифметика python на малых значениях без переполнения.
        for _ in range(50):
            a, b, c, d = [rng.randint(-10, 10) for _ in range(4)]
            source = f"main(a:int,b:int) returns r:int {{r=(a+{abs(c)})*(b-{abs(d)});}}"
            before = self.compile(source)
            expected = [(a+abs(c))*(b-abs(d))]
            self.assertEqual(execute(before, "main", [a,b]), expected)
            self.assertEqual(execute(optimize(before), "main", [a,b]), expected)

    def test_cli_export_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as temporary:
            command = [sys.executable, str(ROOT / "src/main.py"), "ir", "--file",
                       str(ROOT / "examples/arithmetic.funny"), "--format", "json", "--out-dir", temporary]
            process = subprocess.run(command, cwd=temporary, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertIn("analysis", result)
            output = Path(temporary)
            restored = json.loads((output / "after.json").read_text())
            self.assertEqual(execute(restored, "main", [2,3]), [10])
            self.assertIn("digraph cfg", (output / "after_0_cfg.dot").read_text())
            self.assertIn("graph interference", (output / "after_0_conflicts.dot").read_text())
            self.assertIn("МЕТРИКИ", (output / "analysis.txt").read_text())

    def test_cli_readable_text_and_json_modes(self):
        base = [sys.executable, str(ROOT / "src/main.py")]
        source = "main(a:int) returns r:int {r=a+0;}"

        def call(command, *options):
            process = subprocess.run(base + [command, "--text", source, *options],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            return process.stdout

        text = call("ir")
        for phrase in ("IR ДО ОПТИМИЗАЦИЙ", "IR ПОСЛЕ ОПТИМИЗАЦИЙ", "МЕТРИКИ",
                       "живые на входе", "конфликтует с", "цвет"):
            self.assertIn(phrase, text)
        self.assertNotIn('"live_in"', text)
        data = json.loads(call("ir", "--format", "json", "--no-opt"))
        self.assertEqual(data["before"], data["after"])
        self.assertIn("Результат: [3]", call("run", "--args", "[3]"))
        self.assertEqual(json.loads(call("run", "--args", "[3]", "--format", "json")),
                         {"result": [3]})
        self.assertIn("Совпало: 2 из 2", call("verify", "--inputs", "[[1],[3]]"))
        self.assertTrue(json.loads(call("verify", "--inputs", "[[1]]", "--format", "json"))[0]["equal"])
        copied = json.loads(call("ir", "--passes", "copy,dce", "--format", "json"))["after"]
        self.assertFalse(any(ins["op"] == "copy" for ins in copied["functions"][0]["code"]))

    def test_cli_invalid_verification_entry(self):
        base = [sys.executable, str(ROOT / "src/main.py"), "verify", "--text",
                "main(a:int) returns r:int {r=a;}"]
        for options in (["--function", "missing", "--inputs", "[[1]]"],
                        ["--inputs", "[[]]"], ["--inputs", "[[true]]"],
                        ["--inputs", "[[1]]", "--max-steps", "0"]):
            process = subprocess.run(base + options, capture_output=True, text=True)
            self.assertEqual(process.returncode, 1)
            self.assertIn("Ошибка:", process.stderr)
            self.assertEqual(process.stdout, "")

    def test_cli_run_verify_failures(self):
        base = [sys.executable, str(ROOT / "src/main.py")]
        cases = [( ["run", "--text", "main(a:int) returns r:int {r=a+1;}", "--args", "[3]"], 0),
                 ( ["verify", "--text", "main(a:int) returns r:int {r=a+1;}", "--inputs", "[[0],[3]]"], 0),
                 ( ["run", "--text", "main() returns r:int {r=1/0;}"], 1),
                 ( ["ir", "--text", "main() returns r:int {r=x;}"], 1),
                 ( ["ir", "--text", "main() returns r:int {r=1;}", "--passes", "unknown"], 2),
                 ( ["run", "--text", "main() returns r:int {r=1;}", "--args", "{}"], 1),
                 ( ["verify", "--text", "main() returns r:int {r=1;}", "--inputs", "[]"], 1)]
        for args, status in cases:
            process = subprocess.run(base + args, capture_output=True, text=True)
            self.assertEqual(process.returncode, status, process.stderr)
            if status:
                self.assertNotIn("Traceback", process.stderr)


if __name__ == "__main__":
    unittest.main()
