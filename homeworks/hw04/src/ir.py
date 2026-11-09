"""генерация типизированного трёхадресного кода из ast hw3."""
from copy import deepcopy
from pathlib import Path
import sys

_import_path = list(sys.path)
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hw03" / "src"))
    from checker import check
finally:
    # предыдущие этапы добавляют свои каталоги; не оставляем их перед hw4.
    sys.path[:] = _import_path


def i32(value):
    return (value + 2**31) % 2**32 - 2**31


def constant(value):
    return {"const": value}


def ref(name):
    return {"ref": name}


def definitions(instruction):
    return set(instruction.get("dest", []))


def uses(instruction):
    return {arg["ref"] for arg in instruction.get("args", []) if "ref" in arg}


class GenerationError(ValueError):
    def __init__(self, node, message):
        super().__init__(message)
        self.pos = node["pos"]


class Generator:
    def __init__(self, function):
        self.code = []
        self.types = {v["name"]: v["type"] for group in ("params", "returns", "locals")
                      for v in function[group]}
        self.counter = 0
        self.labels = 0

    def temp(self, type_name):
        name = f"%t{self.counter}"
        self.counter += 1
        self.types[name] = type_name
        return name

    def label(self):
        name = f"L{self.labels}"
        self.labels += 1
        return name

    def emit(self, op, args=(), dest=(), **fields):
        self.code.append({"op": op, "dest": list(dest), "args": list(args), **fields})

    def mark(self, label):
        self.emit("label", label=label)

    def value(self, op, args, type_name, **fields):
        dest = self.temp(type_name)
        self.emit(op, args, [dest], **fields)
        return ref(dest)

    def spec(self, role, node):
        if node is not None:
            self.emit("spec", role=role, ast=deepcopy(node))

    def expression(self, node):
        kind = node["kind"]
        if kind == "Int":
            return constant(i32(int(node["value"])))
        if kind == "Bool":
            return constant(node["value"])
        if kind == "Name":
            return ref(node["name"])
        if kind == "Unary":
            return self.value("unary", [self.expression(node["operand"])],
                              node["type"], operator=node["op"])
        if kind == "Binary":
            if node["op"] in ("and", "or", "->"):
                # логика вычисляется с коротким замыканием через обычные переходы.
                left = self.expression(node["left"])
                rhs, shortcut, end = self.label(), self.label(), self.label()
                dest = self.temp("bool")
                on_true, on_false = (rhs, shortcut) if node["op"] != "or" else (shortcut, rhs)
                self.emit("branch", [left], true=on_true, false=on_false)
                self.mark(shortcut)
                self.emit("copy", [constant(node["op"] != "and")], [dest])
                self.emit("jump", target=end)
                self.mark(rhs)
                right = self.expression(node["right"])
                self.emit("copy", [right], [dest])
                self.emit("jump", target=end)
                self.mark(end)
                return ref(dest)
            args = [self.expression(node["left"]), self.expression(node["right"])]
            return self.value("binary", args, node["type"], operator=node["op"])
        if kind == "Call":
            return self.call(node)[0]
        if kind in ("Index", "ArrayUpdate"):
            args = [self.expression(node["array"]), self.expression(node["index"])]
            if kind == "ArrayUpdate":
                args.append(self.expression(node["value"]))
            return self.value("load" if kind == "Index" else "update", args, node["type"])
        raise GenerationError(node, "Квантор в исполняемом выражении не поддерживается; "
                              "кванторы допускаются в аннотациях спецификации")

    def call(self, node):
        args = [self.expression(arg) for arg in node["args"]]
        if node["name"] == "length":
            return [self.value("length", args, "int")]
        types = node["type"] if isinstance(node["type"], list) else [node["type"]]
        dest = [self.temp(type_name) for type_name in types]
        self.emit("call", args, dest, function=node["name"])
        return [ref(name) for name in dest]

    def statement(self, node):
        kind = node["kind"]
        if kind == "Block":
            for child in node["statements"]:
                self.statement(child)
        elif kind == "Assign":
            self.emit("copy", [self.expression(node["value"])], [node["target"]["name"]])
        elif kind == "TupleAssign":
            values = self.call(node["value"])
            for target, value in zip(node["targets"], values):
                self.emit("copy", [value], [target["name"]])
        elif kind == "If":
            condition = self.expression(node["condition"])
            yes, no, end = self.label(), self.label(), self.label()
            self.emit("branch", [condition], true=yes, false=no)
            self.mark(yes)
            self.statement(node["then"])
            self.emit("jump", target=end)
            self.mark(no)
            if node["otherwise"] is not None:
                self.statement(node["otherwise"])
            self.emit("jump", target=end)
            self.mark(end)
        elif kind == "While":
            head, body, end = self.label(), self.label(), self.label()
            self.emit("jump", target=head)
            self.mark(head)
            self.spec("invariant", node["invariant"])
            condition = self.expression(node["condition"])
            self.emit("branch", [condition], true=body, false=end)
            self.mark(body)
            self.statement(node["body"])
            self.emit("jump", target=head)
            self.mark(end)
        elif kind in ("Assert", "Assume"):
            self.spec(kind.lower(), node["expression"])


def generate(ast):
    """принимать только успешно проверенный аннотированный ast hw3."""
    functions = []
    for function in ast["functions"]:
        generator = Generator(function)
        generator.mark(generator.label())
        generator.spec("requires", function["requires"])
        generator.statement(function["body"])
        generator.spec("ensures", function["ensures"])
        generator.emit("return", [ref(v["name"]) for v in function["returns"]])
        functions.append({"name": function["name"],
                          "params": deepcopy(function["params"]),
                          "returns": deepcopy(function["returns"]),
                          "types": generator.types, "code": generator.code})
    return {"format": "funny-ir-v1", "functions": functions}


def compile_source(text):
    result = check(text)
    if result["errors"]:
        return {"ir": None, "errors": result["errors"]}
    try:
        return {"ir": generate(result["ast"]), "errors": []}
    except GenerationError as error:
        return {"ir": None, "errors": [{"phase": "ir", "pos": error.pos,
                                        "message": str(error)}]}


def format_expression(node):
    kind = node["kind"]
    if kind in ("Int", "Bool"):
        return str(node["value"]).lower()
    if kind == "Name":
        return node["name"]
    if kind == "Unary":
        return f"({node['op']} {format_expression(node['operand'])})"
    if kind == "Binary":
        return f"({format_expression(node['left'])} {node['op']} {format_expression(node['right'])})"
    if kind == "Call":
        return f"{node['name']}({', '.join(format_expression(a) for a in node['args'])})"
    if kind == "Index":
        return f"{format_expression(node['array'])}[{format_expression(node['index'])}]"
    if kind == "ArrayUpdate":
        return (f"update({format_expression(node['array'])}, "
                f"{format_expression(node['index'])}, {format_expression(node['value'])})")
    if kind == "Quantifier":
        variable = node["variable"]
        return (f"{node['quantifier']}({variable['name']}:{variable['type']} | "
                f"{format_expression(node['predicate'])})")
    raise ValueError(f"Неизвестный узел выражения {kind}")


def format_ir(program):
    def operand(arg):
        if "ref" in arg:
            return arg["ref"]
        return str(arg["const"]).lower()

    lines = []
    for function in program["functions"]:
        params = ", ".join(f"{p['name']}:{p['type']}" for p in function["params"])
        results = ", ".join(f"{p['name']}:{p['type']}" for p in function["returns"])
        lines.append(f"function {function['name']}({params}) returns {results}:")
        for ins in function["code"]:
            op = ins["op"]
            args = [operand(arg) for arg in ins["args"]]
            prefix = ", ".join(ins["dest"]) + " = " if ins["dest"] else ""
            if op == "label":
                lines.append(ins["label"] + ":")
                continue
            if op == "binary":
                text = f"{args[0]} {ins['operator']} {args[1]}"
            elif op == "unary":
                text = f"{ins['operator']} {args[0]}"
            elif op == "copy":
                text = args[0]
            elif op == "branch":
                text = f"branch {args[0]} ? {ins['true']} : {ins['false']}"
            elif op == "jump":
                text = f"jump {ins['target']}"
            elif op == "call":
                text = f"call {ins['function']}({', '.join(args)})"
            elif op == "spec":
                text = f"spec {ins['role']} {format_expression(ins['ast'])}"
            else:
                text = f"{op} {', '.join(args)}"
            lines.append("  " + prefix + text)
    return "\n".join(lines)
