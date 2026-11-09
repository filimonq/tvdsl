"""интерпретатор ir: int32, массивы по значению, общий лимит команд."""
from copy import deepcopy
from ir import i32


class ExecutionError(ValueError):
    pass


def unary(operator, value):
    if operator == "-":
        return i32(-value)
    if operator == "not":
        return not value
    raise ExecutionError(f"Неизвестная операция {operator}")


def binary(operator, left, right):
    if operator == "+":
        return i32(left + right)
    if operator == "-":
        return i32(left - right)
    if operator == "*":
        return i32(left * right)
    if operator == "/":
        if right == 0:
            raise ExecutionError("Деление на ноль")
        # python // округляет вниз; funny/jvm делит с округлением к нулю.
        value = abs(left) // abs(right)
        return i32(-value if (left < 0) != (right < 0) else value)
    comparisons = {"==": left == right, "!=": left != right,
                   "<": left < right, "<=": left <= right,
                   ">": left > right, ">=": left >= right}
    if operator in comparisons:
        return comparisons[operator]
    raise ExecutionError(f"Неизвестная операция {operator}")


def argument(value, type_name):
    if type_name == "int" and type(value) is int:
        return i32(value)
    if type_name == "int[]" and isinstance(value, list) and all(type(x) is int for x in value):
        return [i32(x) for x in value]
    raise ExecutionError(f"Аргумент не соответствует типу {type_name}")


def validate_entry(program, function, arguments, max_steps):
    if type(max_steps) is not int or max_steps < 1:
        raise ExecutionError("Лимит команд должен быть положительным целым")
    target = next((fn for fn in program["functions"] if fn["name"] == function), None)
    if target is None:
        raise ExecutionError(f"Неизвестная функция {function}")
    if not isinstance(arguments, (list, tuple)) or len(arguments) != len(target["params"]):
        raise ExecutionError(f"Функция {function}: неверное число аргументов")
    for value, param in zip(arguments, target["params"]):
        argument(value, param["type"])


def execute(program, function, arguments=(), max_steps=100000):
    validate_entry(program, function, arguments, max_steps)
    functions = {f["name"]: f for f in program["functions"]}
    steps = 0

    def invoke(name, supplied):
        nonlocal steps
        if name not in functions:
            raise ExecutionError(f"Неизвестная функция {name}")
        fn = functions[name]
        if len(supplied) != len(fn["params"]):
            raise ExecutionError(f"Функция {name}: неверное число аргументов")
        env = {param["name"]: argument(value, param["type"])
               for param, value in zip(fn["params"], supplied)}
        code = fn["code"]
        labels = {ins["label"]: index for index, ins in enumerate(code) if ins["op"] == "label"}

        def read(arg):
            if "const" in arg:
                return arg["const"]
            if arg["ref"] not in env:
                raise ExecutionError(f"Чтение неинициализированной переменной {arg['ref']}")
            return env[arg["ref"]]

        pc = 0
        while pc < len(code):
            ins = code[pc]
            pc += 1
            op = ins["op"]
            if op in ("label", "spec"):
                continue
            steps += 1
            if steps > max_steps:
                raise ExecutionError("Превышен лимит команд")
            args = [read(arg) for arg in ins["args"]]
            values = []
            if op == "copy":
                values = [deepcopy(args[0])]
            elif op == "unary":
                values = [unary(ins["operator"], args[0])]
            elif op == "binary":
                values = [binary(ins["operator"], *args)]
            elif op in ("load", "update"):
                array, index = args[:2]
                if not 0 <= index < len(array):
                    raise ExecutionError("Индекс вне границ массива")
                if op == "load":
                    values = [array[index]]
                else:
                    updated = list(array)
                    updated[index] = args[2]
                    values = [updated]
            elif op == "length":
                values = [i32(len(args[0]))]
            elif op == "call":
                values = invoke(ins["function"], args)
            elif op == "jump":
                pc = labels[ins["target"]]
            elif op == "branch":
                pc = labels[ins["true"] if args[0] else ins["false"]]
            elif op == "return":
                return deepcopy(args)
            else:
                raise ExecutionError(f"Неизвестная инструкция {op}")
            for dest, value in zip(ins["dest"], values):
                env[dest] = value
        raise ExecutionError(f"Функция {name} завершилась без return")

    try:
        return invoke(function, arguments)
    except RecursionError as error:
        raise ExecutionError("Превышена глубина вызовов") from error


def compare(before, after, function, arguments=(), max_steps=100000):
    # неверный запрос запуска не является результатом выполнения программы.
    validate_entry(before, function, arguments, max_steps)
    validate_entry(after, function, arguments, max_steps)

    def outcome(program):
        try:
            return {"result": execute(program, function, arguments, max_steps)}
        except ExecutionError as error:
            return {"error": str(error)}
    original, optimized = outcome(before), outcome(after)
    # исчерпание ресурсов не является наблюдаемым результатом программы.
    inconclusive = any("лимит" in value.get("error", "") or "глубина" in value.get("error", "")
                       for value in (original, optimized))
    return {"before": original, "after": optimized,
            "equal": original == optimized and not inconclusive,
            "inconclusive": inconclusive}
