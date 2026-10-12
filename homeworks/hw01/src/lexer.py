"""задаём правила токенов funny и разбираем текст по построенному дка."""

from automata import build_nfa, determinize, minimize

KEYWORDS = (
    "function returns uses while if else true false int requires ensures "
    "assert assume invariant length forall exists not and or"
).split()
OPERATORS = {
    "LPAREN": "(", "RPAREN": ")", "LBRACKET": "[", "RBRACKET": "]",
    "LBRACE": "{", "RBRACE": "}", "COMMA": ",", "SEMICOLON": ";",
    "COLON": ":", "BAR": "|", "PLUS": "+", "MINUS": "-", "STAR": "*",
    "SLASH": "/", "EQ": "==", "NE": "!=", "LE": "<=", "GE": ">=",
    "LT": "<", "GT": ">", "ASSIGN": "=", "IMPLIES": "->", "ARROW": "=>",
}


def literal_pattern(text):
    # экранируем знаки вроде плюса, чтобы в регулярке они обозначали самих себя
    return "".join("\\" + c if c in r"\[]()|*+?.^$" else c for c in text)


def default_rules():
    # при одинаковой длине совпадения побеждает правило, которое стоит раньше
    return (
        [{"name": "TYPE_INT" if word == "int" else word.upper(),
          "pattern": word, "skip": False} for word in KEYWORDS]
        + [{"name": "IDENT", "pattern": r"[A-Za-z_][A-Za-z0-9_]*", "skip": False},
           {"name": "INT", "pattern": r"0|[1-9][0-9]*", "skip": False},
           {"name": "WS", "pattern": r"[ \t\r\n]+", "skip": True},
           {"name": "COMMENT", "pattern": r"//[^\r\n]*", "skip": True}]
        + [{"name": name, "pattern": literal_pattern(text), "skip": False}
           for name, text in OPERATORS.items()]
    )


def compile_rules(rules):
    """проверяем список правил и запускаем построение нка, дка и минимизацию."""
    if not rules:
        raise ValueError("At least one rule is required")
    names = set()
    for rule in rules:
        if not isinstance(rule.get("name"), str) or not rule["name"]:
            raise ValueError("Each rule needs a nonempty name")
        if rule["name"] in names:
            raise ValueError("Rule names must be unique")
        names.add(rule["name"])
        if not isinstance(rule.get("pattern"), str):
            raise ValueError("Each rule needs a regex pattern")
        if not isinstance(rule.get("skip", False), bool):
            raise ValueError("skip must be boolean")
    nfa = build_nfa(rules)
    original = determinize(nfa)
    return nfa, original, minimize(original)


class LexError(ValueError):
    def __init__(self, offset, reason):
        self.offset = offset
        super().__init__(f"offset {offset}: {reason}")


def tokenize(text, dfa, rules, *, with_positions=False):
    """читаем текст и выдаём токены, каждый раз выбирая самое длинное совпадение."""
    tokens = []
    offset = 0
    line, col, previous_cr = 1, 1, False
    while offset < len(text):
        # новый токен ищем со старта автомата, offset — начало его текста
        state, cursor, last_accept = dfa.start, offset, None
        while cursor < len(text):
            state = dfa.step(state, text[cursor])
            if state == dfa.trap:
                # дальше токен не продолжается, используем последнее принятие
                break
            cursor += 1
            priority = dfa.accepting[state]
            if priority is not None:
                # запоминаем конец подходящего фрагмента и правило, которое его приняло
                last_accept = cursor, priority
        if last_accept is None:
            # с этой позиции не удалось распознать ни одного токена
            raise LexError(offset, f"unexpected character {text[offset]!r}; trap={dfa.trap}")
        end, priority = last_accept
        rule = rules[priority]
        # без этой проверки 01 распалось бы на два числа: 0 и 1
        if rule["name"] == "INT" and text[offset] == "0" and end < len(text) and text[end] in "0123456789":
            raise LexError(offset, "leading zeros in an integer")
        if not rule.get("skip", False):
            # пробелы и комментарии потребляем, но в список токенов не добавляем
            token = {"kind": rule["name"], "lexeme": text[offset:end]}
            if with_positions:
                token.update(offset=offset, end=end, line=line, col=col)
            tokens.append(token)
        if with_positions:
            # crlf — один перевод строки; отдельные cr и lf тоже поддерживаем.
            for char in text[offset:end]:
                if char == "\r":
                    line, col = line + 1, 1
                elif char == "\n":
                    if not previous_cr:
                        line += 1
                    col = 1
                else:
                    col += 1
                previous_cr = char == "\r"
        offset = end  # продолжаем с конца принятого фрагмента
    return tokens
