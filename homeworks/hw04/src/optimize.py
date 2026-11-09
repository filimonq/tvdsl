"""локальные константы и копии, алгебра, cse и dce с межблочной живостью."""
from copy import deepcopy
from flow import build_cfg, liveness
from ir import constant, definitions, ref, uses
from runtime import ExecutionError, binary, unary

PASSES = ("const", "copy", "algebra", "cse", "dce")


def copy_instruction(ins, value):
    return {"op": "copy", "dest": ins["dest"], "args": [value]}


def simplify(ins):
    if ins["op"] != "binary":
        return ins
    left, right = ins["args"]
    op = ins["operator"]
    left_value, right_value = left.get("const"), right.get("const")
    if op in ("+", "-") and right_value == 0:
        return copy_instruction(ins, left)
    if op == "+" and left_value == 0:
        return copy_instruction(ins, right)
    if op in ("*", "/") and right_value == 1:
        return copy_instruction(ins, left)
    if op == "*" and left_value == 1:
        return copy_instruction(ins, right)
    if op == "*" and (left_value == 0 or right_value == 0):
        return copy_instruction(ins, constant(0))
    if op == "-" and left == right:
        return copy_instruction(ins, constant(0))
    # x/x и 0/x сохраняем: x может быть нулём.
    return ins


def local_pass(function, enabled):
    output = []
    for block in build_cfg(function):
        constants, copies, expressions = {}, {}, {}
        for original in block["instructions"]:
            ins = deepcopy(original)
            if "copy" in enabled:
                ins["args"] = [ref(copies.get(arg["ref"], arg["ref"])) if "ref" in arg else arg
                               for arg in ins["args"]]
            if "const" in enabled:
                ins["args"] = [deepcopy(constants.get(arg["ref"], arg)) if "ref" in arg else arg
                               for arg in ins["args"]]
                if ins["op"] in ("binary", "unary") and all("const" in a for a in ins["args"]):
                    args = [a["const"] for a in ins["args"]]
                    try:
                        value = (binary(ins["operator"], *args) if ins["op"] == "binary"
                                 else unary(ins["operator"], *args))
                        ins = copy_instruction(ins, constant(value))
                    except ExecutionError:
                        pass
            if "algebra" in enabled:
                ins = simplify(ins)
            key = None
            expression_uses = uses(ins)
            if "cse" in enabled and ins["op"] in ("binary", "unary"):
                key = (ins["op"], ins["operator"],
                       tuple(("ref", a["ref"]) if "ref" in a else
                             (type(a["const"]).__name__, a["const"]) for a in ins["args"]))
                if key in expressions:
                    ins = copy_instruction(ins, ref(expressions[key]))
            written = definitions(ins)
            for name in written:
                constants.pop(name, None)
            # источники копий уже подставлены до конечного имени. при записи
            # удаляем связи как с получателем, так и с прежним источником.
            copies = {dest: source for dest, source in copies.items()
                      if dest not in written and source not in written}
            # запись уничтожает выражения, зависящие от старого значения имени,
            # и записи, результат которых хранился в перезаписанном месте.
            expressions = {k: value for k, value in expressions.items()
                           if value not in written and
                           not any(tag == "ref" and name in written for tag, name in k[2])}
            if key is not None and not (expression_uses & written):
                expressions[key] = ins["dest"][0]
            if ins["op"] == "copy" and "const" in ins["args"][0]:
                constants[ins["dest"][0]] = ins["args"][0]
            if "copy" in enabled and ins["op"] == "copy" and "ref" in ins["args"][0]:
                dest, source = ins["dest"][0], ins["args"][0]["ref"]
                if dest != source:
                    copies[dest] = source
            output.append(ins)
    function["code"] = output


def removable(ins):
    # вызовы, чтение/обновление массива и деление могут упасть или не завершиться.
    return ins["op"] in ("copy", "unary") or (ins["op"] == "binary" and ins["operator"] != "/")


def dead_code(function):
    output = []
    for block in liveness(function):
        live = set(block["live_out"])
        kept = []
        for ins in reversed(block["instructions"]):
            written = definitions(ins)
            if written and not (written & live) and removable(ins):
                continue
            kept.append(ins)
            live = (live - written) | uses(ins)
        output.extend(reversed(kept))
    function["code"] = output


def optimize(program, passes=PASSES):
    enabled = set(passes)
    if enabled - set(PASSES):
        raise ValueError(f"Неизвестные оптимизации: {sorted(enabled - set(PASSES))}")
    result = deepcopy(program)
    if not enabled:
        return result
    for function in result["functions"]:
        while True:
            previous = deepcopy(function["code"])
            local_pass(function, enabled)
            if "dce" in enabled:
                dead_code(function)
            if function["code"] == previous:
                break
    return result
