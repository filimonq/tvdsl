"""проверка имён и типов funny поверх парсера hw2."""
from pathlib import Path
import sys

HW2_SRC = Path(__file__).resolve().parents[2] / "hw02" / "src"
sys.path.insert(0, str(HW2_SRC))
from parser import parse


ERROR = "error"
VARIABLE_TYPES = ("int", "int[]")


class Checker:
    def __init__(self):
        self.errors = []
        self.functions = {"length": {"params": ["int[]"], "returns": ["int"]}}
        self.scopes = []

    def error(self, node, message):
        self.errors.append({"phase": "semantic", "message": message,
                            "pos": dict(node["pos"])})

    def lookup(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def declare(self, node, role):
        scope = self.scopes[-1]
        name = node["name"]
        if name in scope:
            self.error(node, f"Повторное объявление имени '{name}'")
            return
        scope[name] = {"node": node, "role": role, "type": node["type"]}

    def unify(self, actual, expected, node, context):
        """согласовать типы; неизвестный тип связывается только при присваивании."""
        if actual == ERROR or expected == ERROR:
            return ERROR
        if actual is None:
            return expected
        if actual != expected:
            self.error(node, f"{context}: ожидался {expected}, получен {actual}")
            return ERROR
        return actual

    def program(self, ast):
        # сигнатуры собираются до тел: разрешены рекурсия и вызовы вперёд.
        for function in ast["functions"]:
            signature = {"params": [p["type"] for p in function["params"]],
                         "returns": [r["type"] for r in function["returns"]]}
            function["signature"] = signature
            if function["name"] in self.functions:
                self.error(function, f"Повторное объявление функции '{function['name']}'")
            else:
                self.functions[function["name"]] = signature
        for function in ast["functions"]:
            self.function(function)

    def function(self, function):
        self.scopes = [{}]
        for parameter in function["params"]:
            self.declare(parameter, "parameter")
        self.predicate(function["requires"], "Предусловие requires")
        for result in function["returns"]:
            self.declare(result, "return")
        self.predicate(function["ensures"], "Постусловие ensures")
        for local in function["locals"]:
            self.declare(local, "local")
        self.statement(function["body"])
        for symbol in self.scopes[0].values():
            if symbol["type"] is None:
                self.error(symbol["node"],
                           f"Не удалось вывести тип локальной переменной '{symbol['node']['name']}'")

    def predicate(self, node, context):
        if node is not None:
            self.unify(self.expression(node), "bool", node, context)

    def name(self, node, writing=False, allow_unknown=False):
        name = node["name"]
        symbol = self.lookup(name)
        if symbol is None:
            message = (f"Имя '{name}' обозначает функцию, а не переменную"
                       if name in self.functions else f"Неизвестное имя '{name}'")
            self.error(node, message)
            node["type"] = ERROR
            return None
        node["type"] = symbol["type"] or ERROR
        if writing and symbol["role"] in ("parameter", "quantifier"):
            self.error(node, f"Нельзя присваивать значение имени '{name}': оно доступно только для чтения")
            return None
        if symbol["type"] is None and not allow_unknown:
            self.error(node, f"Тип переменной '{name}' ещё не выведен: сначала нужно корректное присваивание")
        return symbol

    def expression(self, node):
        kind = node["kind"]
        result = ERROR
        if kind == "Int":
            result = "int"
        elif kind == "Bool":
            result = "bool"
        elif kind == "Name":
            self.name(node)
            return node["type"]
        elif kind == "Unary":
            expected = "bool" if node["op"] == "not" else "int"
            result = self.unify(self.expression(node["operand"]), expected,
                                node, f"Операция '{node['op']}'")
        elif kind == "Binary":
            op = node["op"]
            expected = "bool" if op in ("and", "or", "->") else "int"
            left = self.unify(self.expression(node["left"]), expected,
                              node["left"], f"Левый операнд '{op}'")
            right = self.unify(self.expression(node["right"]), expected,
                               node["right"], f"Правый операнд '{op}'")
            if left != ERROR and right != ERROR:
                result = "int" if op in ("+", "-", "*", "/") else "bool"
        elif kind == "Call":
            return self.call(node, tuple_context=False)
        elif kind == "Index":
            array = self.unify(self.expression(node["array"]), "int[]",
                               node["array"], "Чтение элемента массива")
            index = self.unify(self.expression(node["index"]), "int",
                               node["index"], "Индекс массива")
            if array != ERROR and index != ERROR:
                result = "int"
        elif kind == "ArrayUpdate":
            result = self.array_update(node)
        elif kind == "Quantifier":
            variable = node["variable"]
            if self.lookup(variable["name"]) is not None:
                self.error(variable, f"Имя квантора '{variable['name']}' уже объявлено в доступной области")
            self.scopes.append({})
            self.declare(variable, "quantifier")
            value = self.expression(node["predicate"])
            result = self.unify(value, "bool", node["predicate"], "Предикат квантора")
            self.scopes.pop()
        node["type"] = result
        return result

    def call(self, node, tuple_context):
        # аргументы проверяются даже при неизвестном имени или неверной арности.
        arguments = [self.expression(arg) for arg in node["args"]]
        name = node["name"]
        signature = self.functions.get(name)
        valid = True
        if self.lookup(name) is not None:
            self.error(node, f"Имя '{name}' обозначает переменную, а не функцию")
            signature = None
        elif signature is None:
            self.error(node, f"Неизвестная функция '{name}'")
        if signature is None:
            node["type"] = ERROR
            return ERROR
        if len(arguments) != len(signature["params"]):
            self.error(node, f"Функция '{name}': ожидалось аргументов {len(signature['params'])}, "
                             f"получено {len(arguments)}")
            valid = False
        for index, (actual, expected) in enumerate(zip(arguments, signature["params"])):
            if self.unify(actual, expected, node["args"][index],
                          f"Аргумент {index + 1} функции '{name}'") == ERROR:
                valid = False
        returns = signature["returns"]
        if not tuple_context and len(returns) != 1:
            self.error(node, f"Функция '{name}' возвращает {len(returns)} значений; "
                             "нужно кортежное присваивание")
            valid = False
        result = (list(returns) if tuple_context else returns[0]) if valid else ERROR
        node["type"] = result
        return result

    def array_update(self, node):
        # неизвестный получатель может стать int[] по корректному a[i] = v.
        # имя уже проверено как левая часть нормализованного присваивания.
        symbol = self.lookup(node["array"]["name"])
        actual = symbol["type"] if symbol else ERROR
        array = self.unify(actual, "int[]", node["array"], "Обновление массива")
        index = self.unify(self.expression(node["index"]), "int", node["index"], "Индекс массива")
        value = self.unify(self.expression(node["value"]), "int", node["value"], "Элемент массива")
        if ERROR in (array, index, value):
            return ERROR
        return "int[]"

    def assign(self, target, symbol, value):
        if symbol is None or value == ERROR:
            return
        if value not in VARIABLE_TYPES:
            self.error(target, f"Переменная '{target['name']}' не может иметь тип {value}; "
                               "разрешены int и int[]")
            return
        if symbol["type"] is None:
            result = self.unify(None, value, target,
                                f"Вывод типа переменной '{target['name']}'")
        else:
            result = self.unify(value, symbol["type"], target,
                                f"Присваивание переменной '{target['name']}'")
        if result != ERROR:
            symbol["type"] = result
            symbol["node"]["type"] = result
            target["type"] = result

    def statement(self, node):
        kind = node["kind"]
        if kind == "Block":
            self.scopes.append({})
            for statement in node["statements"]:
                self.statement(statement)
            self.scopes.pop()
        elif kind == "Assign":
            target = node["target"]
            symbol = self.name(target, writing=True, allow_unknown=True)
            value = self.expression(node["value"])
            self.assign(target, symbol, value)
            # нормализованное обновление содержит отдельное чтение получателя.
            if node["value"]["kind"] == "ArrayUpdate" and value != ERROR and symbol is not None:
                node["value"]["array"]["type"] = symbol["type"] or ERROR
        elif kind == "TupleAssign":
            targets = node["targets"]
            symbols = [self.name(t, writing=True, allow_unknown=True) for t in targets]
            values = self.call(node["value"], tuple_context=True)
            names = set()
            for target in targets:
                if target["name"] in names:
                    self.error(target, f"Имя '{target['name']}' повторяется в кортежном присваивании")
                names.add(target["name"])
            if values != ERROR:
                if len(targets) != len(values):
                    self.error(node, f"Кортежное присваивание: получателей {len(targets)}, "
                                     f"возвращаемых значений {len(values)}")
                else:
                    for target, symbol, value in zip(targets, symbols, values):
                        self.assign(target, symbol, value)
        elif kind == "If":
            self.predicate(node["condition"], "Условие if")
            self.statement(node["then"])
            if node["otherwise"] is not None:
                self.statement(node["otherwise"])
        elif kind == "While":
            self.predicate(node["condition"], "Условие while")
            self.predicate(node["invariant"], "Инвариант цикла")
            self.statement(node["body"])
        elif kind in ("Assert", "Assume"):
            self.predicate(node["expression"], f"Предикат {kind.lower()}")


def check(text):
    """текст -> аннотированное дерево и ошибки; повреждённый синтаксис не типизируем."""
    result = parse(text)
    if result["errors"]:
        return {"ast": None, "errors": result["errors"]}
    checker = Checker()
    try:
        checker.program(result["ast"])
    except RecursionError:
        return {"ast": None, "errors": checker.errors + [{"phase": "semantic",
            "message": "Слишком большая вложенность для проверки типов",
            "pos": result["ast"]["pos"]}]}
    return {"ast": result["ast"], "errors": checker.errors}
