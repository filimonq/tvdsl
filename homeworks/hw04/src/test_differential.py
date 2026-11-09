"""независимое исполнение ast и проверка ir на сгенерированных программах."""
from copy import deepcopy
import itertools
from pathlib import Path
import random
import unittest

from ir import constant, generate, ref
from checker import check
from flow import interference, liveness
from optimize import PASSES, optimize
from runtime import compare, ExecutionError, execute


def execute_ast(ast, entry, supplied):
    """учебный эталон без использования инструкций и арифметики интерпретатора ir."""
    functions = {fn["name"]: fn for fn in ast["functions"]}

    def wrap(value):
        value %= 4294967296
        return value - 4294967296 if value >= 2147483648 else value

    def invoke(name, arguments):
        if name == "length":
            return [len(arguments[0])]
        fn = functions[name]
        env = {p["name"]: deepcopy(v) if isinstance(v, list) else wrap(v)
               for p, v in zip(fn["params"], arguments)}

        def expression(node):
            kind = node["kind"]
            if kind == "Int":
                return wrap(int(node["value"]))
            if kind == "Bool":
                return node["value"]
            if kind == "Name":
                return env[node["name"]]
            if kind == "Call":
                return invoke(node["name"], [expression(a) for a in node["args"]])[0]
            if kind == "Unary":
                value = expression(node["operand"])
                return not value if node["op"] == "not" else wrap(-value)
            if kind == "Binary":
                left = expression(node["left"])
                op = node["op"]
                if op == "and":
                    return left and expression(node["right"])
                if op == "or":
                    return left or expression(node["right"])
                if op == "->":
                    return not left or expression(node["right"])
                right = expression(node["right"])
                if op == "+":
                    return wrap(left + right)
                if op == "-":
                    return wrap(left - right)
                if op == "*":
                    return wrap(left * right)
                if op == "/":
                    if right == 0:
                        raise ValueError("Деление на ноль")
                    sign = 1 if (left >= 0) == (right >= 0) else -1
                    return wrap(sign * (abs(left) // abs(right)))
                return {"==": left == right, "!=": left != right, "<": left < right,
                        "<=": left <= right, ">": left > right, ">=": left >= right}[op]
            if kind in ("Index", "ArrayUpdate"):
                array = expression(node["array"])
                index = expression(node["index"])
                value = expression(node["value"]) if kind == "ArrayUpdate" else None
                if index < 0 or index >= len(array):
                    raise ValueError("Индекс вне границ массива")
                if kind == "Index":
                    return array[index]
                result = list(array)
                result[index] = value
                return result
            raise AssertionError(kind)

        def statement(node):
            kind = node["kind"]
            if kind == "Block":
                for child in node["statements"]:
                    statement(child)
            elif kind == "Assign":
                env[node["target"]["name"]] = deepcopy(expression(node["value"]))
            elif kind == "TupleAssign":
                call = node["value"]
                values = invoke(call["name"], [expression(a) for a in call["args"]])
                for target, value in zip(node["targets"], values):
                    env[target["name"]] = deepcopy(value)
            elif kind == "If":
                branch = node["then"] if expression(node["condition"]) else node["otherwise"]
                if branch is not None:
                    statement(branch)
            elif kind == "While":
                iterations = 0
                while expression(node["condition"]):
                    statement(node["body"])
                    iterations += 1
                    if iterations > 100:
                        raise AssertionError("эталон рассчитан на короткие конечные циклы")
            elif kind not in ("Assert", "Assume"):
                raise AssertionError(kind)

        statement(fn["body"])
        return deepcopy([env[v["name"]] for v in fn["returns"]])

    return invoke(entry, supplied)


def allocated(program):
    """проверить раскраску выполнением копии ir с общими именами для одного цвета."""
    result = deepcopy(program)
    for fn in result["functions"]:
        colors = interference(fn)["colors"]
        mapping = {name: f"%slot{color}" for name, color in colors.items()}
        for ins in fn["code"]:
            ins["dest"] = [mapping.get(name, name) for name in ins["dest"]]
            for arg in ins["args"]:
                if "ref" in arg:
                    arg["ref"] = mapping.get(arg["ref"], arg["ref"])
    return result


def instruction_liveness(function):
    """отдельный расчёт по каждой инструкции без разбиения на блоки."""
    code = function["code"]
    labels = {ins["label"]: pc for pc, ins in enumerate(code) if ins["op"] == "label"}
    incoming = [set() for _ in code]
    outgoing = [set() for _ in code]
    changed = True
    while changed:
        changed = False
        for pc in reversed(range(len(code))):
            ins = code[pc]
            if ins["op"] == "branch":
                targets = [labels[ins["true"]], labels[ins["false"]]]
            elif ins["op"] == "jump":
                targets = [labels[ins["target"]]]
            elif ins["op"] == "return" or pc + 1 == len(code):
                targets = []
            else:
                targets = [pc + 1]
            out = set().union(*(incoming[t] for t in targets))
            used = {arg["ref"] for arg in ins["args"] if "ref" in arg}
            live = used | (out - set(ins["dest"]))
            if live != incoming[pc] or out != outgoing[pc]:
                incoming[pc], outgoing[pc] = live, out
                changed = True
    return incoming, outgoing


class DifferentialTests(unittest.TestCase):
    def test_generated_programs_against_ast(self):
        rng = random.Random(4404)

        def arithmetic(depth):
            if depth == 0:
                return rng.choice(["a", "b", "x", "y", "i", "arr[0]", "2147483647",
                                   "4294967295", str(rng.randint(0, 9))])
            op = rng.choice(["+", "-", "*", "/"])
            right = str(rng.randint(1, 7)) if op == "/" else arithmetic(depth-1)
            return f"({arithmetic(depth-1)}{op}{right})"

        def condition(depth):
            if depth == 0:
                return f"({arithmetic(1)}{rng.choice(['<','>','==','!=','<=','>='])}{arithmetic(1)})"
            return f"({condition(depth-1)} {rng.choice(['and','or','->'])} {condition(depth-1)})"

        inputs = [[1,2,[3]], [-3,7,[-2,8]], [2147483647,-2147483648,[0]]]
        combinations = [passes for count in range(5) for passes in itertools.combinations(PASSES, count)]
        for case in range(100):
            source = ("pair(v:int) returns p:int,q:int {p=v;q=v+1;} "
                      "main(a:int,b:int,arr:int[]) returns r:int uses x:int,y:int,i:int,z:int[] {"
                      "x=a;y=b;i=0;z=arr;"
                      f"x={arithmetic(2)};y={arithmetic(2)};"
                      f"while(i<3){{if({condition(1)}){{x={arithmetic(2)};}}"
                      f"else{{y={arithmetic(2)};}}i=i+1;}}"
                      f"x,y=pair({arithmetic(1)});z[0]=x;"
                      f"if({condition(1)}){{r=x+y+z[0];}}else{{r={arithmetic(2)};}}}}")
            checked = check(source)
            self.assertEqual(checked["errors"], [], source)
            before = generate(checked["ast"])
            expected = [execute_ast(checked["ast"], "main", supplied) for supplied in inputs]
            for passes in combinations:
                after = optimize(before, passes)
                for supplied, result in zip(inputs, expected):
                    with self.subTest(case=case, passes=passes, supplied=supplied):
                        self.assertEqual(execute(after, "main", supplied), result, source)
                if len(passes) in (0, 4):
                    stored = allocated(after)
                    for supplied, result in zip(inputs, expected):
                        self.assertEqual(execute(stored, "main", supplied), result, source)
                    for fn in after["functions"]:
                        live_in, live_out = instruction_liveness(fn)
                        for block in liveness(fn):
                            self.assertEqual(block["live_in"], live_in[block["start"]])
                            self.assertEqual(block["live_out"], live_out[block["end"]-1])

    def test_examples_against_ast_and_allocated_execution(self):
        root = Path(__file__).resolve().parents[1] / "examples"
        cases = {"arithmetic": [[2, 3], [-2, 9], [2147483647, 1]],
                 "control": [[-2], [0], [1], [2], [3], [4], [8]],
                 "calls_arrays": [[[4, 8]], [[-1]]], "contracts": [[[9]]]}
        for name, inputs in cases.items():
            checked = check((root / f"{name}.funny").read_text())
            self.assertEqual(checked["errors"], [])
            before = generate(checked["ast"])
            for supplied in inputs:
                expected = execute_ast(checked["ast"], "main", supplied)
                for program in (before, optimize(before)):
                    self.assertEqual(execute(program, "main", supplied), expected)
                    self.assertEqual(execute(allocated(program), "main", supplied), expected)

    def test_invalid_verification_request(self):
        program = generate(check("main(a:int) returns r:int {r=a;}")["ast"])
        for entry, supplied, limit in (("missing", [1], 100), ("main", [], 100),
                                       ("main", [True], 100), ("main", [1], 0)):
            with self.assertRaises(ExecutionError):
                compare(program, program, entry, supplied, limit)

    def test_generated_ir_with_overwritten_results(self):
        rng = random.Random(4091)
        for case in range(200):
            names = ["x", "y", "z", "%t0", "%t1"]
            code = [{"op": "copy", "dest": [name], "args": [constant(rng.randrange(-3, 4))]}
                    for name in names]
            for _ in range(20):
                dest = rng.choice(names)
                args = [ref(rng.choice(names)) if rng.randrange(3) else constant(rng.randrange(-2, 3))
                        for _ in range(2)]
                code.append({"op": "binary", "operator": rng.choice(["+", "-", "*", "/"]),
                             "dest": [dest], "args": args})
            code.append({"op": "return", "dest": [], "args": [ref(name) for name in names]})
            program = {"format": "funny-ir-v1", "functions": [{"name": "main", "params": [],
                       "returns": [{"name": name, "type": "int"} for name in names],
                       "types": {name: "int" for name in names}, "code": code}]}
            for count in range(5):
                for passes in itertools.combinations(PASSES, count):
                    with self.subTest(case=case, passes=passes):
                        self.assertTrue(compare(program, optimize(program, passes), "main")["equal"])
