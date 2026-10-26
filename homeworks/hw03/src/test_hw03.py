"""проверки семантики, аннотаций, вывода типов и cli."""
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from checker import check
from parser import parse


ROOT = Path(__file__).resolve().parents[1]
EXPRESSIONS = {"Int", "Bool", "Name", "Unary", "Binary", "Call", "Index",
               "ArrayUpdate", "Quantifier"}


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


class SemanticTests(unittest.TestCase):
    def good(self, source):
        result = check(source)
        self.assertEqual(result["errors"], [])
        self.assertIsNotNone(result["ast"])
        for node in walk(result["ast"]):
            if node.get("kind") in EXPRESSIONS:
                self.assertIn("type", node)
                self.assertNotIn(node["type"], (None, "error"))
        return result["ast"]["functions"][0]

    def bad(self, source, *messages):
        result = check(source)
        self.assertTrue(result["errors"])
        self.assertTrue(all(e["phase"] == "semantic" for e in result["errors"]), result)
        text = "\n".join(e["message"] for e in result["errors"])
        for message in messages:
            self.assertIn(message, text)
        for error in result["errors"]:
            self.assertGreaterEqual(error["pos"]["line"], 1)
            self.assertGreaterEqual(error["pos"]["col"], 1)
        return result

    def test_cases(self):
        for case in json.loads((ROOT / "cases.json").read_text(encoding="utf-8")):
            with self.subTest(case=case["name"]):
                if case["errors"]:
                    self.bad(case["source"], *case["errors"])
                else:
                    self.good(case["source"])

    def test_examples(self):
        for path in sorted((ROOT / "examples").glob("*.funny")):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                if path.name.startswith("good_"):
                    self.good(source)
                else:
                    self.bad(source, "Неизвестное имя", "только для чтения", "Условие if")

    def test_exact_annotations(self):
        function = self.good("f(a:int[]) returns r:int uses b, x { b = a; x = b[0]; r = x + length(b); }")
        self.assertEqual(function["signature"], {"params": ["int[]"], "returns": ["int"]})
        self.assertEqual([d["type"] for d in function["locals"]], ["int[]", "int"])
        add = function["body"]["statements"][2]["value"]
        self.assertEqual((add["type"], add["left"]["type"], add["right"]["type"]), ("int", "int", "int"))

    def test_tuple_annotations_and_inference(self):
        function = self.good("f(a:int[]) returns r:int uses x, b { x,b = pair(a); r = x; } "
                             "pair(a:int[]) returns n:int, b:int[] { n = 1; b = a; }")
        self.assertEqual([d["type"] for d in function["locals"]], ["int", "int[]"])
        self.assertEqual(function["body"]["statements"][0]["value"]["type"], ["int", "int[]"])

    def test_tuple_count_and_types(self):
        suffix = " pair(a:int[]) returns n:int,b:int[] { n = 1; b = a; }"
        self.bad("f(a:int[]) returns r:int uses x:int,y:int,z:int { x,y,z = pair(a); r=0; }" + suffix,
                 "получателей 3, возвращаемых значений 2")
        self.bad("f(a:int[]) returns r:int uses x:int,y:int { x,y = pair(a); r=0; }" + suffix,
                 "ожидался int, получен int[]")

    def test_multi_return_in_expression(self):
        suffix = " pair() returns x:int,y:int { x=1; y=2; }"
        self.bad("f() returns r:int { r = pair() + 1; }" + suffix, "нужно кортежное присваивание")
        self.bad("f() returns r:int { r = pair(); }" + suffix, "нужно кортежное присваивание")

    def test_duplicate_tuple_target(self):
        self.bad("f() returns r:int { r,r = pair(); } pair() returns a:int,b:int { a=1; b=2; }",
                 "повторяется в кортежном присваивании")

    def test_mutual_recursion_and_function_isolation(self):
        self.good("f(n:int) returns r:int { r=g(n); } g(n:int) returns r:int { r=f(n); }")
        self.bad("f() returns r:int uses x:int { x=1; r=x; } g() returns r:int { r=x; }",
                 "Неизвестное имя 'x'")

    def test_function_and_variable_names(self):
        self.bad("f() returns r:int { r=f; }", "обозначает функцию, а не переменную")
        self.bad("f() returns r:int uses f:int { r=f(); }", "обозначает переменную, а не функцию")
        self.good("f() returns r:int uses f:int { f=1; r=f; }")

    def test_duplicate_roles(self):
        self.bad("f(a:int,a:int) returns r:int { r=0; }", "Повторное объявление")
        self.bad("f(a:int) returns r:int uses a:int { r=0; }", "Повторное объявление")
        self.bad("f() returns r:int,r:int { r=0; }", "Повторное объявление")
        self.bad("f() returns r:int uses x:int,x:int { r=0; }", "Повторное объявление")

    def test_contract_scopes_and_quantifiers(self):
        self.good("f(a:int[]) requires forall(i:int | i>=0) returns r:int "
                  "ensures exists(i:int | i==r) { r=length(a); }")
        self.bad("f() returns r:int { assert forall(i:int | exists(i:int | i==0)); r=0; }",
                 "Имя квантора 'i' уже объявлено")
        self.good("f() returns r:int { assert forall(i:int | i==0); assert exists(i:int | i==1); r=0; }")

    def test_all_predicate_contexts(self):
        result = self.bad("f() requires 1 returns r:int ensures 2 { "
                          "while (3) invariant 4 { r=1; } assert 5; assume 6; }",
                          "Предусловие", "Постусловие", "Условие while", "Инвариант", "assert", "assume")
        self.assertEqual(len(result["errors"]), 6)

    def test_comparisons_require_integers(self):
        for op in ("==", "!=", "<", ">", "<=", ">="):
            with self.subTest(op=op):
                self.good(f"f(x:int) returns r:int {{ assert x {op} 0; r=0; }}")
                self.bad(f"f(a:int[]) returns r:int {{ assert a {op} a; r=0; }}", "ожидался int")
        self.bad("f() returns r:int { assert true == false; r=0; }", "ожидался int")

    def test_bad_unary_and_logic(self):
        self.bad("f() returns r:int { r=-true; assert not 1; assert 1 and 2; assert 1 or 2; assert 1 -> 2; }",
                 "Операция '-'", "Операция 'not'", "операнд 'and'", "операнд 'or'", "операнд '->'")

    def test_array_checks(self):
        self.bad("f() returns r:int uses a:int { r=a[0]; a[true]=false; }",
                 "Чтение элемента", "Обновление массива", "Индекс массива", "Элемент массива")

    def test_failed_assignment_does_not_infer(self):
        function = self.bad("f(a:int[]) returns r:int uses x { x=1+true; x=a; r=length(x); }",
                            "Правый операнд")['ast']['functions'][0]
        self.assertEqual(function["locals"][0]["type"], "int[]")
        self.assertEqual(len(check("f() returns r:int uses x { x=length(1); r=0; }")["errors"]), 2)

    def test_failed_update_does_not_infer(self):
        result = self.bad("f() returns r:int uses a { a[true]=1; a=2; r=a; }", "Индекс массива")
        self.assertEqual(result["ast"]["functions"][0]["locals"][0]["type"], "int")

    def test_no_retroactive_inference(self):
        self.bad("f() returns r:int uses x,y { x=y; y=1; r=0; }",
                 "Тип переменной 'y' ещё не выведен", "Не удалось вывести тип локальной переменной 'x'")

    def test_branch_types_are_consistent(self):
        self.good("f() returns r:int uses x { if (true) { x=1; } else { x=2; } r=x; }")
        self.bad("f(a:int[]) returns r:int uses x { if (true) { x=1; } else { x=a; } r=0; }",
                 "ожидался int, получен int[]")

    def test_no_definite_assignment_analysis(self):
        self.good("f() returns r:int uses x:int { r=x; }")
        self.good("f() returns r:int uses x { while (false) { x=1; } r=x; }")

    def test_error_recovery_and_cascade_suppression(self):
        result = self.bad("f() returns r:int { r=absent + 1; r=missing(unknown); if (r) { r=0; } }",
                          "Неизвестное имя 'absent'", "Неизвестное имя 'unknown'", "Неизвестная функция", "Условие if")
        self.assertEqual(len(result["errors"]), 4)
        self.assertEqual(result["ast"]["functions"][0]["body"]["statements"][0]["value"]["type"], "error")
        result = self.bad("f() returns r:int { missing[0]=1; r=0; }", "Неизвестное имя 'missing'")
        self.assertEqual(len(result["errors"]), 1)

    def test_exact_error_position_crlf(self):
        source = "f() returns r:int {\r\n    r = missing;\r\n}"
        error = self.bad(source, "Неизвестное имя")["errors"][0]
        self.assertEqual(error["pos"], {"offset": source.index("missing"), "line": 2, "col": 9})

    def test_lexical_and_syntax_failures(self):
        for source, phase in [("@", "lexical"), ("f() returns r:int { r=; }", "syntax")]:
            with self.subTest(phase=phase):
                result = check(source)
                self.assertIsNone(result["ast"])
                self.assertEqual(result["errors"][0]["phase"], phase)

    def test_parser_is_unchanged(self):
        source = "f() returns r:int uses x { x=1; r=x; }"
        before = parse(source)
        check(source)
        self.assertEqual(parse(source), before)
        self.assertIsNone(before["ast"]["functions"][0]["locals"][0]["type"])

    def test_many_inputs_do_not_crash(self):
        generator = random.Random(3)
        expressions = ["0", "true", "a", "x", "missing", "length(a)", "a[0]", "unknown(1)"]
        for _ in range(150):
            left, right = generator.choices(expressions, k=2)
            op = generator.choice(["+", "-", "*", "/", "and", "or", "==", "->"])
            source = f"f(a:int[]) returns r:int uses x {{ x=1; r={left} {op} {right}; }}"
            result = check(source)
            self.assertIsNotNone(result["ast"])
            self.assertTrue(all(e["phase"] == "semantic" for e in result["errors"]))

    def test_large_nesting_diagnostic(self):
        result = check("f() returns r:int { r=" + "(" * 1500 + "1" + ")" * 1500 + "; }")
        self.assertTrue(result["errors"])
        self.assertIn("вложенность", result["errors"][-1]["message"])


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "src" / "main.py"), "check", *args],
                              capture_output=True, text=True, cwd=tempfile.gettempdir())

    def test_success(self):
        result = self.run_cli("--file", str(ROOT / "examples" / "good_inference.funny"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["errors"], [])
        self.assertEqual(result.stderr, "")

    def test_errors_default_and_json(self):
        source = "f() returns r:int { r=missing; }"
        result = self.run_cli("--text", source)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("<text>:1:23: семантическая ошибка:", result.stderr)
        result = self.run_cli("--text", source, "--json")
        self.assertEqual(result.returncode, 1)
        self.assertIsNotNone(json.loads(result.stdout)["ast"])
        self.assertEqual(result.stderr, "")

    def test_file_crlf_and_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.funny"
            path.write_bytes(b"f() returns r:int {\r\n    r = missing;\r\n}")
            result = self.run_cli("--file", str(path), "--json")
            self.assertEqual(json.loads(result.stdout)["errors"][0]["pos"]["line"], 2)
            result = self.run_cli("--file", str(Path(directory) / "absent.funny"))
            self.assertEqual(result.returncode, 1)
            self.assertIn("Ошибка:", result.stderr)


if __name__ == "__main__":
    unittest.main()
