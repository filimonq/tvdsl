"""эталонные ast, позиции и восстановление, а также cli из другой папки."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
import random

from parser import parse
from scanner import scan

HERE = Path(__file__).resolve().parents[1]


def shape(value):
    """для структурных эталонов убираем только позиции; их проверяем отдельно."""
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items() if key != "pos"}
    if isinstance(value, list):
        return [shape(item) for item in value]
    return value


def name(value):
    return {"kind": "Name", "name": value}


def integer(value):
    return {"kind": "Int", "value": str(value)}


def binary(op, left, right):
    return {"kind": "Binary", "op": op, "left": left, "right": right}


def check_grammar():
    """вычисляем first/follow и проверяем, что выбор правила однозначен."""
    grammar = {}
    for line in (HERE / "grammar.bnf").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        left, right = line.split("->")
        grammar[left.strip()] = [alternative.split() for alternative in right.split("|")]
    first = {rule: set() for rule in grammar}
    follow = {rule: set() for rule in grammar}
    follow["program"].add("$")

    def first_of(sequence):
        result = set()
        for symbol in sequence:
            values = first[symbol] if symbol in grammar else {symbol}
            result.update(values - {"epsilon"})
            if "epsilon" not in values:
                return result
        return result | {"epsilon"}

    changed = True
    while changed:
        changed = False
        for rule, alternatives in grammar.items():
            for sequence in alternatives:
                previous = len(first[rule])
                first[rule].update(first_of(sequence))
                changed |= previous != len(first[rule])
    changed = True
    while changed:
        changed = False
        for rule, alternatives in grammar.items():
            for sequence in alternatives:
                for index, symbol in enumerate(sequence):
                    if symbol not in grammar:
                        continue
                    tail = first_of(sequence[index + 1:])
                    previous = len(follow[symbol])
                    follow[symbol].update(tail - {"epsilon"})
                    if "epsilon" in tail:
                        follow[symbol].update(follow[rule])
                    changed |= previous != len(follow[symbol])
    conflicts = []
    for rule, alternatives in grammar.items():
        used = set()
        for sequence in alternatives:
            choices = first_of(sequence)
            prediction = choices - {"epsilon"}
            if "epsilon" in choices:
                prediction |= follow[rule]
            if used & prediction:
                conflicts.append((rule, sorted(used & prediction)))
            used.update(prediction)
    return grammar, first, follow, conflicts


class HW02Tests(unittest.TestCase):
    def test_ll1_grammar(self):
        grammar, first, follow, conflicts = check_grammar()
        self.assertEqual(conflicts, [])
        self.assertEqual(first["statement"], {"IDENT", "IF", "WHILE", "LBRACE", "ASSERT", "ASSUME"})
        self.assertNotIn("ELSE", follow["conditional"])
        from lexer import default_rules
        terminals = {symbol for alternatives in grammar.values() for sequence in alternatives
                     for symbol in sequence if symbol not in grammar}
        self.assertLessEqual(terminals, {rule["name"] for rule in default_rules()} | {"EOF"})

    def good(self, text):
        result = parse(text)
        self.assertEqual(result["errors"], [])
        return shape(result["ast"])

    def statements(self, body):
        return self.good(f"f() returns r:int {{ {body} }}")["functions"][0]["body"]["statements"]

    def test_cases(self):
        for case in json.loads((HERE / "cases.json").read_text(encoding="utf-8")):
            with self.subTest(case=case["name"]):
                text = (HERE / case["file"]).read_text() if "file" in case else case["input"]
                errors = parse(text)["errors"]
                actual = [{"phase": e["phase"], "line": e["pos"]["line"],
                           "col": e["pos"]["col"]} for e in errors]
                self.assertEqual(actual, case["errors"])
                for error in errors:
                    self.assertTrue(error["message"])

    def test_full_ast(self):
        actual = self.good("function f(x:int) returns r:int { r=x+2*3; }")
        expected = {"kind": "Program", "functions": [{"kind": "Function", "name": "f",
            "params": [{"kind": "VarDecl", "name": "x", "type": "int"}],
            "returns": [{"kind": "VarDecl", "name": "r", "type": "int"}],
            "requires": None, "ensures": None, "locals": [],
            "body": {"kind": "Block", "statements": [{"kind": "Assign", "target": name("r"),
                "value": binary("+", name("x"), binary("*", integer(2), integer(3)))}]}}]}
        self.assertEqual(actual, expected)

    def test_arithmetic_associativity(self):
        value = self.statements("r=8-3-1;")[0]["value"]
        self.assertEqual(value, binary("-", binary("-", integer(8), integer(3)), integer(1)))
        value = self.statements("r=8/2*3;")[0]["value"]
        self.assertEqual(value, binary("*", binary("/", integer(8), integer(2)), integer(3)))
        value = self.statements("r=-(1+2)*3;")[0]["value"]
        self.assertEqual(value, binary("*", {"kind": "Unary", "op": "-",
            "operand": binary("+", integer(1), integer(2))}, integer(3)))

    def test_boolean_precedence(self):
        value = self.statements("assert not x < 2 and true or false -> true -> false;")[0]["expression"]
        expected = binary("->", binary("or", binary("and", {"kind": "Unary", "op": "not",
            "operand": binary("<", name("x"), integer(2))}, {"kind": "Bool", "value": True}),
            {"kind": "Bool", "value": False}), binary("->", {"kind": "Bool", "value": True},
            {"kind": "Bool", "value": False}))
        self.assertEqual(value, expected)

    def test_all_comparison_operators(self):
        for operator in ("==", "!=", "<", "<=", ">", ">="):
            with self.subTest(operator=operator):
                value = self.statements(f"assert a {operator} b;")[0]["expression"]
                self.assertEqual(value, binary(operator, name("a"), name("b")))

    def test_multiple_functions_and_recursion(self):
        ast = self.good("f(x:int) returns r:int {r=f(x-1);} "
                        "function g() returns a:int,b:int {a,b=f(1);}")
        self.assertEqual([f["name"] for f in ast["functions"]], ["f", "g"])
        self.assertEqual(ast["functions"][0]["body"]["statements"][0]["value"]["name"], "f")

    def test_parentheses_in_conditions(self):
        value = self.statements("assert (x+1)*2 < y and (x < 3 or y > 4);")[0]["expression"]
        self.assertEqual(value["op"], "and")
        self.assertEqual(value["left"]["left"], binary("*", binary("+", name("x"), integer(1)), integer(2)))
        self.assertEqual(value["right"]["op"], "or")

    def test_array_update(self):
        actual = self.statements("a[i+1]=length(b);")[0]
        self.assertEqual(actual, {"kind": "Assign", "target": name("a"), "value": {
            "kind": "ArrayUpdate", "array": name("a"), "index": binary("+", name("i"), integer(1)),
            "value": {"kind": "Call", "name": "length", "args": [name("b")]}}})
        self.assertEqual(self.statements("r=a[0];")[0]["value"], {
            "kind": "Index", "array": name("a"), "index": integer(0)})

    def test_tuple_and_nested_calls(self):
        actual = self.statements("x,y=minmax(f(1),2);")[0]
        self.assertEqual(actual, {"kind": "TupleAssign", "targets": [name("x"), name("y")],
            "value": {"kind": "Call", "name": "minmax", "args": [
                {"kind": "Call", "name": "f", "args": [integer(1)]}, integer(2)]}})
        self.assertTrue(parse("f() returns r:int {x,y=1;}")["errors"])

    def test_nested_if_blocks(self):
        outer = self.statements("if (true) {if (false) {r=1;} else {r=2;}}")[0]
        self.assertIsNone(outer["otherwise"])
        inner = outer["then"]["statements"][0]
        self.assertEqual(inner["otherwise"]["statements"][0]["value"], integer(2))
        outer = self.statements("if (true) {if (false) {r=1;}} else {r=2;}")[0]
        self.assertEqual(outer["otherwise"]["statements"][0]["value"], integer(2))
        for body in ("if(true) r=1;", "if(true) {} else r=2;", "while(true) r=1;"):
            self.assertTrue(parse(f"f() returns r:int {{{body}}}")["errors"])

    def test_function_contracts_and_locals(self):
        function = self.good("f(a:int[]) requires length(a)>0 returns b:int[],r:int "
                             "ensures r>=0 uses i, j:int {r=a[0];}")["functions"][0]
        self.assertEqual(function["params"][0]["type"], "int[]")
        self.assertEqual(function["returns"][0]["type"], "int[]")
        self.assertEqual(function["locals"], [{"kind": "VarDecl", "name": "i", "type": None},
                                               {"kind": "VarDecl", "name": "j", "type": "int"}])
        self.assertEqual(function["requires"]["op"], ">")
        self.assertEqual(function["ensures"]["op"], ">=")

    def test_quantifier(self):
        value = self.statements("assert forall(i:int | i >= 0 -> a[i] >= 0);")[0]["expression"]
        self.assertEqual(value["kind"], "Quantifier")
        self.assertEqual(value["variable"], {"kind": "VarDecl", "name": "i", "type": "int"})
        self.assertEqual(value["predicate"]["op"], "->")

    def test_while_and_nested_blocks(self):
        loop = self.statements("while (i<3) invariant i>=0 {{i=i+1;} assume true;}")[0]
        self.assertEqual(loop["kind"], "While")
        self.assertEqual(loop["invariant"]["op"], ">=")
        self.assertEqual([s["kind"] for s in loop["body"]["statements"]], ["Block", "Assume"])

    def test_positions_skip_and_eof(self):
        tokens = scan("// skip\r\n\tfoo\rbar\nbaz")
        self.assertEqual([(t["kind"], t["line"], t["col"], t["offset"]) for t in tokens],
                         [("IDENT", 2, 2, 10), ("IDENT", 3, 1, 14),
                          ("IDENT", 4, 1, 18), ("EOF", 4, 4, 21)])
        self.assertEqual(tokens[0]["end"], 13)
        ast = parse("f() returns r:int {\n r=1;\n}")["ast"]
        assignment = ast["functions"][0]["body"]["statements"][0]
        self.assertEqual(assignment["pos"], {"offset": 21, "line": 2, "col": 2})

    def test_recovery_keeps_later_statements(self):
        result = parse((HERE / "examples/bad_recovery.funny").read_text())
        self.assertEqual(len(result["errors"]), 2)
        statements = shape(result["ast"])["functions"][0]["body"]["statements"]
        self.assertEqual(statements, [{"kind": "Assign", "target": name("r"), "value": integer(3)}])

    def test_recovery_keeps_next_function(self):
        result = parse("function broken(x:int returns r:int {} function good() returns r:int {r=1;}")
        self.assertTrue(result["errors"])
        self.assertEqual([f["name"] for f in result["ast"]["functions"]], ["good"])

    def test_recovery_without_function_keyword(self):
        result = parse("bad(x:int returns r:int {} good() returns r:int {r=1;}")
        self.assertTrue(result["errors"])
        self.assertEqual([f["name"] for f in result["ast"]["functions"]], ["good"])

    def test_excessive_nesting(self):
        result = parse("f() returns r:int {r=" + "(" * 1500 + "1" + ")" * 1500 + ";}")
        self.assertIsNone(result["ast"])
        self.assertIn("вложенность", result["errors"][-1]["message"])
        self.assertIn("line", result["errors"][-1]["pos"])

    def test_random_bad_inputs_terminate(self):
        rng = random.Random(2)
        alphabet = ["if", "else", "while", "{", "}", "(", ")", "r", "=", ";",
                    "1", "true", "assert", "returns", "function", "+", "[", "]", ","]
        for _ in range(200):
            text = " ".join(rng.choices(alphabet, k=rng.randrange(1, 40)))
            result = parse(text)
            self.assertIn("ast", result)
            self.assertTrue(result["errors"])

    def test_bad_inputs_terminate(self):
        for body in ("}", "{", ";;;", "else", "r=1+;", "r=(1;", "r=a[;", "if(true)",
                     "while(true)", "assert x<y<z;", "r=a[0][1];", "r=length;", "r=1", "r=f(,);"):
            with self.subTest(body=body):
                result = parse(f"f() returns r:int {{{body}}}")
                self.assertTrue(result["errors"])

    def test_syntax_does_not_check_types(self):
        self.good("f(x:int) returns r:int {if (x) {x=unknown;}}")

    def test_cli(self):
        command = [sys.executable, "-B", str(HERE / "src/main.py"), "parse"]
        for text, code in (("f() returns r:int {}", 0), ("", 1), ("@", 1)):
            result = subprocess.run(command + ["--text", text], cwd="/tmp", capture_output=True,
                                    text=True, timeout=5)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertIn("ast", json.loads(result.stdout))
        result = subprocess.run(command + ["--file", str(HERE / "examples/good_arrays.funny")],
                                cwd="/tmp", capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_preserves_crlf_offsets(self):
        import tempfile
        text = "f() returns r:int {\r\n\tr=@;\r\n}"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "crlf.funny"
            path.write_bytes(text.encode("ascii"))
            result = subprocess.run([sys.executable, "-B", str(HERE / "src/main.py"),
                                     "parse", "--file", str(path)], capture_output=True,
                                    text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        error = json.loads(result.stdout)["errors"][0]
        self.assertEqual(error["pos"], {"offset": text.index("@"), "line": 2, "col": 4})


if __name__ == "__main__":
    unittest.main()
