"""рекурсивный спуск: токены funny -> ast и синтаксическая диагностика."""
from scanner import LexError, position, scan


class ParseError(Exception):
    pass


def node(kind, token, **fields):
    return {"kind": kind, "pos": {key: token[key] for key in ("offset", "line", "col")},
            **fields}


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.index = 0
        self.errors = []

    @property
    def current(self):
        return self.tokens[self.index]

    def at(self, *kinds):
        return self.current["kind"] in kinds

    def take(self):
        token = self.current
        if not self.at("EOF"):
            self.index += 1
        return token

    def accept(self, kind):
        return self.take() if self.at(kind) else None

    def fail(self, message):
        token = self.current
        found = repr(token["lexeme"]) if not self.at("EOF") else "конец файла"
        self.errors.append({"phase": "syntax", "message": f"{message}; получено {found}",
                            "pos": {key: token[key] for key in ("offset", "line", "col")}})
        raise ParseError()

    def expect(self, kind):
        if not self.at(kind):
            expected = {
                "LPAREN": "'('", "RPAREN": "')'", "LBRACKET": "'['",
                "RBRACKET": "']'", "LBRACE": "'{'", "RBRACE": "'}'",
                "SEMICOLON": "';'", "ASSIGN": "'='", "COLON": "':'",
                "IDENT": "идентификатор", "TYPE_INT": "тип int",
                "RETURNS": "returns", "BAR": "'|'",
            }.get(kind, kind)
            self.fail(f"Ожидался {expected}")
        return self.take()

    def synchronize_statement(self, start):
        # '}' оставляем владельцу блока. каждый другой путь потребляет токен
        # или достигает eof, поэтому повтор той же ошибки невозможен.
        while not self.at("SEMICOLON", "RBRACE", "EOF"):
            self.take()
        if self.at("SEMICOLON"):
            self.take()
        if self.index == start and not self.at("RBRACE", "EOF"):
            self.take()

    def program(self):
        start = self.current
        functions = []
        if self.at("EOF"):
            try:
                self.fail("Ожидалась хотя бы одна функция")
            except ParseError:
                pass
        while not self.at("EOF"):
            before = self.index
            try:
                functions.append(self.function())
            except ParseError:
                # после повреждённой функции ищем следующее объявление.
                if self.index == before:
                    self.take()
                while not self.at("EOF", "FUNCTION"):
                    if (self.at("IDENT")
                            and self.tokens[self.index + 1]["kind"] == "LPAREN"):
                        break
                    self.take()
        return node("Program", start, functions=functions)

    def declaration(self, optional_type=False):
        token = self.expect("IDENT")
        type_name = None
        if self.accept("COLON"):
            self.expect("TYPE_INT")
            type_name = "int"
            if self.accept("LBRACKET"):
                self.expect("RBRACKET")
                type_name = "int[]"
        elif not optional_type:
            self.fail("Ожидался COLON и тип int или int[]")
        return node("VarDecl", token, name=token["lexeme"], type=type_name)

    def declarations(self, optional_type=False):
        result = [self.declaration(optional_type)]
        while self.accept("COMMA"):
            result.append(self.declaration(optional_type))
        return result

    def function(self):
        start = self.current
        self.accept("FUNCTION")
        name = self.expect("IDENT")["lexeme"]
        self.expect("LPAREN")
        params = [] if self.at("RPAREN") else self.declarations()
        self.expect("RPAREN")
        requires = self.expression() if self.accept("REQUIRES") else None
        self.expect("RETURNS")
        returns = self.declarations()
        ensures = self.expression() if self.accept("ENSURES") else None
        locals_ = self.declarations(True) if self.accept("USES") else []
        body = self.statement()
        return node("Function", start, name=name, params=params, returns=returns,
                    requires=requires, ensures=ensures, locals=locals_, body=body)

    def block(self):
        start = self.expect("LBRACE")
        statements = []
        while not self.at("RBRACE", "EOF"):
            before = self.index
            try:
                statements.append(self.statement())
            except ParseError:
                self.synchronize_statement(before)
        self.expect("RBRACE")
        return node("Block", start, statements=statements)

    def statement(self):
        start = self.current
        if self.at("LBRACE"):
            return self.block()
        if self.accept("IF"):
            self.expect("LPAREN")
            condition = self.expression()
            self.expect("RPAREN")
            then = self.block()
            # блок заканчивается до else, поэтому вложенные if не конфликтуют.
            otherwise = self.block() if self.accept("ELSE") else None
            return node("If", start, condition=condition, then=then, otherwise=otherwise)
        if self.accept("WHILE"):
            self.expect("LPAREN")
            condition = self.expression()
            self.expect("RPAREN")
            invariant = self.expression() if self.accept("INVARIANT") else None
            return node("While", start, condition=condition, invariant=invariant,
                        body=self.block())
        if self.at("ASSERT", "ASSUME"):
            keyword = self.take()
            value = self.expression()
            self.expect("SEMICOLON")
            return node("Assert" if keyword["kind"] == "ASSERT" else "Assume",
                        start, expression=value)
        if self.at("IDENT"):
            return self.assignment()
        self.fail("Ожидался оператор: присваивание, if, while, блок, assert или assume")

    def assignment(self):
        start = self.expect("IDENT")
        target = node("Name", start, name=start["lexeme"])
        if self.accept("LBRACKET"):
            index = self.expression()
            self.expect("RBRACKET")
            self.expect("ASSIGN")
            value = self.expression()
            self.expect("SEMICOLON")
            # это внутренний узел, а не вызов публичной функции set.
            update = node("ArrayUpdate", start, array=target, index=index, value=value)
            return node("Assign", start, target=target, value=update)
        targets = [target]
        while self.accept("COMMA"):
            token = self.expect("IDENT")
            targets.append(node("Name", token, name=token["lexeme"]))
        self.expect("ASSIGN")
        value = self.expression()
        if len(targets) > 1 and value["kind"] != "Call":
            self.fail("В правой части множественного присваивания ожидался вызов функции")
        self.expect("SEMICOLON")
        if len(targets) == 1:
            return node("Assign", start, target=target, value=value)
        return node("TupleAssign", start, targets=targets, value=value)

    def expression(self):
        left = self.disjunction()
        if self.at("IMPLIES"):
            operator = self.take()
            return node("Binary", operator, op=operator["lexeme"], left=left,
                        right=self.expression())
        return left

    def binary(self, operand, kinds):
        left = operand()
        while self.at(*kinds):
            operator = self.take()
            left = node("Binary", operator, op=operator["lexeme"], left=left,
                        right=operand())
        return left

    def disjunction(self):
        return self.binary(self.conjunction, ("OR",))

    def conjunction(self):
        return self.binary(self.negation, ("AND",))

    def negation(self):
        if self.at("NOT"):
            operator = self.take()
            return node("Unary", operator, op="not", operand=self.negation())
        return self.comparison()

    def comparison(self):
        left = self.addition()
        if self.at("EQ", "NE", "LT", "LE", "GT", "GE"):
            operator = self.take()
            left = node("Binary", operator, op=operator["lexeme"], left=left,
                        right=self.addition())
        return left

    def addition(self):
        return self.binary(self.multiplication, ("PLUS", "MINUS"))

    def multiplication(self):
        return self.binary(self.unary, ("STAR", "SLASH"))

    def unary(self):
        if self.at("MINUS"):
            operator = self.take()
            return node("Unary", operator, op="-", operand=self.unary())
        return self.primary()

    def primary(self):
        token = self.current
        if self.accept("INT"):
            # сохраняем десятичную запись без ограничения размера python int.
            return node("Int", token, value=token["lexeme"])
        if self.at("TRUE", "FALSE"):
            self.take()
            return node("Bool", token, value=token["kind"] == "TRUE")
        if self.accept("LPAREN"):
            value = self.expression()
            self.expect("RPAREN")
            return value
        if self.at("FORALL", "EXISTS"):
            self.take()
            self.expect("LPAREN")
            variable = self.declaration()
            self.expect("BAR")
            predicate = self.expression()
            self.expect("RPAREN")
            return node("Quantifier", token, quantifier=token["lexeme"],
                        variable=variable, predicate=predicate)
        if self.at("IDENT", "LENGTH"):
            self.take()
            name = token["lexeme"]
            if self.accept("LPAREN"):
                args = []
                if not self.at("RPAREN"):
                    args.append(self.expression())
                    while self.accept("COMMA"):
                        args.append(self.expression())
                self.expect("RPAREN")
                return node("Call", token, name=name, args=args)
            if token["kind"] == "LENGTH":
                self.fail("После length ожидалась открывающая скобка")
            value = node("Name", token, name=name)
            if self.accept("LBRACKET"):
                index = self.expression()
                self.expect("RBRACKET")
                value = node("Index", token, array=value, index=index)
            return value
        self.fail("Ожидалось выражение")


def parse(text):
    """результат всегда содержит ast и errors; при лексической ошибке ast=null."""
    try:
        tokens = scan(text)
    except LexError as error:
        return {"ast": None, "errors": [{"phase": "lexical", "message": str(error),
                                         "pos": position(text, error.offset)}]}
    parser = Parser(tokens)
    try:
        ast = parser.program()
    except RecursionError:
        # при чрезмерной вложенности возвращаем диагностику вместо traceback.
        return {"ast": None, "errors": parser.errors + [{"phase": "syntax",
            "message": "Слишком большая вложенность программы",
            "pos": {key: parser.current[key] for key in ("offset", "line", "col")}}]}
    return {"ast": ast, "errors": parser.errors}
