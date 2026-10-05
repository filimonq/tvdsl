"""строим нка по регуляркам, превращаем в дка и минимизируем."""
from collections import deque
from dataclasses import dataclass, field

ALPHABET = range(128)


@dataclass
class NFA:
    # метка None означает ε-переход, остальные метки содержат коды символов
    edges: list = field(default_factory=list)
    accepting: dict = field(default_factory=dict)  # номер конечного состояния → индекс правила
    start: int = 0

    def state(self):
        self.edges.append([])
        return len(self.edges) - 1

    def edge(self, source, label, target):
        self.edges[source].append((label, target))


class RegexParser:
    """читаем регулярку и собираем нка из её частей."""

    def __init__(self, pattern, nfa):
        self.pattern, self.nfa, self.pos = pattern, nfa, 0

    def peek(self):
        return self.pattern[self.pos:self.pos + 1]

    def parse(self):
        fragment = self.alternative()
        if self.pos != len(self.pattern):
            raise ValueError(f"Unexpected character at {self.pos}: {self.pattern!r}")
        return fragment

    def epsilon(self):
        # фрагмент для пустой строки: от входа к выходу без чтения символа
        start, end = self.nfa.state(), self.nfa.state()
        self.nfa.edge(start, None, end)
        return start, end

    def alternative(self):
        left = self.concatenate()
        while self.peek() == "|":
            self.pos += 1
            right = self.concatenate()
            start, end = self.nfa.state(), self.nfa.state()
            # из общего входа доступны обе ветки, их выходы ведут в общий выход
            for entry, exit_state in (left, right):
                self.nfa.edge(start, None, entry)
                self.nfa.edge(exit_state, None, end)
            left = start, end
        return left

    def concatenate(self):
        result = None
        while self.peek() and self.peek() not in ")|":
            fragment = self.repeat()
            if result is None:
                result = fragment
            else:
                # соединяем конец предыдущего фрагмента с началом следующего
                self.nfa.edge(result[1], None, fragment[0])
                result = result[0], fragment[1]
        return self.epsilon() if result is None else result

    def repeat(self):
        entry, exit_state = self.atom()
        if self.peek() and self.peek() in "*+?":
            operator = self.peek()
            self.pos += 1
            start, end = self.nfa.state(), self.nfa.state()
            self.nfa.edge(start, None, entry)
            self.nfa.edge(exit_state, None, end)
            if operator in "*?":
                # звёздочка и вопрос разрешают пропустить весь фрагмент
                self.nfa.edge(start, None, end)
            if operator in "*+":
                # звёздочка и плюс разрешают вернуться и повторить фрагмент
                self.nfa.edge(exit_state, None, entry)
            return start, end
        return entry, exit_state

    def character(self):
        if not self.peek():
            raise ValueError("Incomplete character or escape")
        char = self.peek()
        self.pos += 1
        if char == "\\":
            if not self.peek():
                raise ValueError("Trailing backslash")
            escaped = self.peek()
            self.pos += 1
            char = {"n": "\n", "r": "\r", "t": "\t"}.get(escaped, escaped)
        if ord(char) >= 128:
            raise ValueError("Regular expressions must use ASCII")
        return ord(char)

    def character_class(self):
        self.pos += 1  # пропускаем открывающую квадратную скобку
        negative = self.peek() == "^"
        if negative:
            self.pos += 1
        chars = set()
        while self.peek() and self.peek() != "]":
            first = self.character()
            if self.peek() == "-" and self.pattern[self.pos + 1:self.pos + 2] not in ("", "]"):
                self.pos += 1
                last = self.character()
                if first > last:
                    raise ValueError("Reversed range")
                chars.update(range(first, last + 1))
            else:
                chars.add(first)
        if self.peek() != "]" or not chars:
            raise ValueError("Unclosed or empty character class")
        self.pos += 1
        # для [^...] берём все символы алфавита, кроме перечисленных
        return frozenset(set(ALPHABET) - chars if negative else chars)

    def atom(self):
        # скобки содержат выражение, а символ или класс дают один переход
        if self.peek() == "(":
            self.pos += 1
            fragment = self.alternative()
            if self.peek() != ")":
                raise ValueError("Unclosed group")
            self.pos += 1
            return fragment
        if self.peek() == "[":
            label = self.character_class()
        else:
            if self.peek() in ("", "*", "+", "?", ")", "|", ".", "^", "$"):
                raise ValueError(f"Unexpected or unsupported metacharacter at {self.pos}")
            label = frozenset([self.character()])
        start, end = self.nfa.state(), self.nfa.state()
        self.nfa.edge(start, label, end)
        return start, end


def build_nfa(rules):
    """собираем правила токенов в один нка с общим стартом."""
    nfa = NFA()
    nfa.start = nfa.state()
    for priority, rule in enumerate(rules):
        start, end = RegexParser(rule["pattern"], nfa).parse()
        # ε-переход даёт доступ к правилу, метка выхода запоминает его индекс
        nfa.edge(nfa.start, None, start)
        nfa.accepting[end] = priority
    return nfa


def epsilon_closure(nfa, states):
    """находим всё, куда можно дойти без чтения символов."""
    visited = set(states)
    pending = list(states)
    while pending:
        for label, target in nfa.edges[pending.pop()]:
            if label is None and target not in visited:
                # уже посещённые состояния не обходим снова, чтобы не зациклиться
                visited.add(target)
                pending.append(target)
    return frozenset(visited)


@dataclass
class DFA:
    transitions: list  # строка — состояние, столбец — код символа, ячейка — куда идти
    accepting: list  # для конечного состояния — индекс правила, для остальных — None
    start: int
    trap: int

    def step(self, state, char):
        # символы вне ascii сразу отправляем в ловушку
        code = ord(char)
        return self.transitions[state][code] if code < 128 else self.trap

    def match(self, text):
        """проверяем всю строку как одну лексему, возвращаем индекс правила."""
        state = self.start
        for char in text:
            state = self.step(state, char)
        return self.accepting[state]

    def to_dict(self, rules):
        """собираем данные автомата для сохранения в json."""
        return {
            "format": "funny-dfa-v1", "alphabet_size": 128,
            "start": self.start, "trap": self.trap,
            "outside_alphabet": "trap", "rules": rules,
            "accepting": self.accepting, "transitions": self.transitions,
        }


def determinize(nfa):
    """каждое множество возможных состояний нка становится состоянием дка."""
    start = epsilon_closure(nfa, [nfa.start])
    subsets = [start]
    indices = {start: 0}
    transitions, accepting = [], []
    for subset in subsets:
        # если подошло несколько правил, побеждает более раннее в списке
        priorities = [nfa.accepting[s] for s in subset if s in nfa.accepting]
        accepting.append(min(priorities) if priorities else None)
        row = []
        for code in ALPHABET:
            # сначала идём по символу, затем учитываем доступные ε-переходы
            moved = {target for state in subset for label, target in nfa.edges[state]
                     if label is not None and code in label}
            target = epsilon_closure(nfa, moved)
            if target not in indices:
                # новые множества добавляем в список, этот же цикл их обработает
                indices[target] = len(subsets)
                subsets.append(target)
            row.append(indices[target])
        transitions.append(row)
    # пустое множество — ловушка; оставляем её явно, даже если по ascii она не нужна
    if frozenset() not in indices:
        indices[frozenset()] = len(transitions)
        transitions.append([len(transitions)] * 128)
        accepting.append(None)
    dfa = DFA(transitions, accepting, 0, indices[frozenset()])
    if dfa.accepting[dfa.start] is not None:
        # пустой токен не сдвинул бы позицию сканера и мог бы зациклить чтение
        raise ValueError("Token rules must not accept the empty string")
    return dfa


def minimize(dfa):
    """по хопкрофту разделяем группы состояний, пока их поведение различается."""
    groups = {}
    # разные категории токенов сразу разделяем, чтобы сохранить результат сканера
    for state, output in enumerate(dfa.accepting):
        groups.setdefault(output, set()).add(state)
    partition = {frozenset(group) for group in groups.values()}
    work = set(partition)
    # заранее запоминаем, из каких состояний можно попасть в каждое состояние
    inverse = [[set() for _ in dfa.transitions] for _ in ALPHABET]
    for state, row in enumerate(dfa.transitions):
        for code, target in enumerate(row):
            inverse[code][target].add(state)
    while work:
        splitter = work.pop()
        for code in ALPHABET:
            predecessors = set().union(*(inverse[code][s] for s in splitter))
            for block in list(partition):
                # часть группы ведёт в выбранную группу, другая часть — за её пределы
                inside, outside = block & predecessors, block - predecessors
                if not inside or not outside:
                    continue
                left, right = frozenset(inside), frozenset(outside)
                partition.remove(block)
                partition.update((left, right))
                if block in work:
                    # запланированную старую группу заменяем обеими новыми
                    work.remove(block)
                    work.update((left, right))
                else:
                    # иначе достаточно добавить меньшую часть для следующих проверок
                    work.add(left if len(left) <= len(right) else right)
    # сортируем группы, чтобы номера состояний были одинаковыми при повторном запуске
    blocks = sorted(partition, key=min)
    mapping = {state: i for i, block in enumerate(blocks) for state in block}
    # каждую группу заменяем одним состоянием и пересчитываем номера переходов
    transitions = [[mapping[target] for target in dfa.transitions[min(block)]]
                   for block in blocks]
    accepting = [dfa.accepting[min(block)] for block in blocks]
    return DFA(transitions, accepting, mapping[dfa.start], mapping[dfa.trap])


def distinguishing_word(left, right):
    """ищем строку, на которой два дка дают разные ответы; если её нет, вернём None."""
    # идём по обоим автоматам одновременно и запоминаем пары состояний
    queue = deque([(left.start, right.start, "")])
    visited = {(left.start, right.start)}
    while queue:
        a, b, word = queue.popleft()
        if left.accepting[a] != right.accepting[b]:
            # нашли продолжение, которое отличает автоматы
            return word
        for code in ALPHABET:
            pair = left.transitions[a][code], right.transitions[b][code]
            if pair not in visited:
                visited.add(pair)
                queue.append((*pair, word + chr(code)))
    return None
