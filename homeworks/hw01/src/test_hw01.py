"""проверяем примеры, символы ascii и результаты построения автоматов."""
import itertools
import json
from pathlib import Path
import random
import re
import unittest

from automata import DFA, distinguishing_word
from lexer import LexError, compile_rules, default_rules, tokenize

HERE = Path(__file__).resolve().parent.parent


class HW01Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = default_rules()
        cls.nfa, cls.original, cls.minimal = compile_rules(cls.rules)
        # библиотечные регулярки используем как независимый эталон только в тестах
        cls.oracle = [re.compile(rule["pattern"], re.ASCII) for rule in cls.rules]

    def test_cases_on_both_automata(self):
        cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["name"]):
                # один и тот же пример должен работать до и после минимизации
                for dfa in (self.original, self.minimal):
                    if "error" in case:
                        with self.assertRaises(LexError) as caught:
                            tokenize(case["input"], dfa, self.rules)
                        self.assertEqual(caught.exception.offset, case["error"])
                    else:
                        actual = [[t["kind"], t["lexeme"]] for t in tokenize(case["input"], dfa, self.rules)]
                        self.assertEqual(actual, case["tokens"])
                # печатаем успех только после обеих проверок этого примера
                print(f"PASS {case['name']}: {case['input']!r}")

    def test_equivalence_for_all_ascii_strings(self):
        # проверка всех достижимых пар охватывает строки любой длины
        self.assertIsNone(distinguishing_word(self.original, self.minimal))

    def test_complete_table_and_trap(self):
        for dfa in (self.original, self.minimal):
            self.assertIsNone(dfa.accepting[dfa.trap])
            self.assertEqual(dfa.transitions[dfa.trap], [dfa.trap] * 128)
            for row in dfa.transitions:
                self.assertEqual(len(row), 128)
                self.assertTrue(all(0 <= target < len(dfa.transitions) for target in row))
            for char in ("я", "é", "🙂", "\u0080", "\U0010ffff"):
                for state in range(len(dfa.transitions)):
                    self.assertEqual(dfa.step(state, char), dfa.trap)

    def test_each_ascii_character(self):
        for code in range(128):
            char = chr(code)
            priority = next((i for i, regex in enumerate(self.oracle) if regex.fullmatch(char)), None)
            with self.subTest(code=code):
                self.assertEqual(self.minimal.match(char), priority)
                if priority is None:
                    with self.assertRaises(LexError):
                        tokenize(char, self.minimal, self.rules)
                else:
                    rule = self.rules[priority]
                    expected = [] if rule["skip"] else [{"kind": rule["name"], "lexeme": char}]
                    self.assertEqual(tokenize(char, self.minimal, self.rules), expected)
                # тот же символ проверяем внутри комментария
                if char not in "\r\n":
                    self.assertEqual(tokenize("//" + char, self.minimal, self.rules), [])

    def test_regex_oracle(self):
        samples = ["", "00", "01", "//", "//hello", " \t\r\n", "_ab9", "1234"]
        samples += [r["pattern"] for r in self.rules[:20]]
        samples += ["".join(chars) for n in range(4)
                    for chars in itertools.product("a0_/=\n", repeat=n)]
        rng = random.Random(1)
        samples += ["".join(rng.choice("ab_019 /\r\n=<>!") for _ in range(rng.randrange(15)))
                    for _ in range(1000)]
        for text in samples:
            expected = next((i for i, regex in enumerate(self.oracle) if regex.fullmatch(text)), None)
            self.assertEqual(self.original.match(text), expected, repr(text))
            self.assertEqual(self.minimal.match(text), expected, repr(text))

    def test_thompson_operators_and_known_minimum(self):
        patterns = ["a(b|c)d", "(ab)*c", "a?b+", "[a-c]+", "[^a-c]", r"\+", r"[\t\n]", "a|a"]
        words = ["".join(chars) for n in range(5) for chars in itertools.product("abcd", repeat=n)]
        words += ["+", "\n", "\t", ""]
        for pattern in patterns:
            if pattern == "a|a":
                _, original, minimal = compile_rules([{"name": "T", "pattern": pattern}])
                self.assertGreater(len(original.transitions), 0)
                self.assertEqual(len(minimal.transitions), 3)  # старт, конечное, ловушка
            else:
                _, original, minimal = compile_rules([{"name": "T", "pattern": pattern}])
            self.assertIsNone(distinguishing_word(original, minimal))
            for word in words:
                self.assertEqual(minimal.match(word) is not None, re.fullmatch(pattern, word) is not None,
                                 (pattern, repr(word)))
        _, original, minimal = compile_rules([{"name": "T", "pattern": "ab|ac"}])
        self.assertLess(len(minimal.transitions), len(original.transitions))
        self.assertEqual(len(minimal.transitions), 4)

    def test_preserve_token_priorities(self):
        rules = [{"name": "FIRST", "pattern": "a"}, {"name": "SECOND", "pattern": "[ab]"}]
        _, _, dfa = compile_rules(rules)
        self.assertEqual(tokenize("ab", dfa, rules), [
            {"kind": "FIRST", "lexeme": "a"}, {"kind": "SECOND", "lexeme": "b"}])

    def test_json_roundtrip(self):
        data = json.loads(json.dumps(self.minimal.to_dict(self.rules)))
        loaded = DFA(data["transitions"], data["accepting"], data["start"], data["trap"])
        self.assertIsNone(distinguishing_word(self.minimal, loaded))
        self.assertEqual(tokenize("x=42;", loaded, data["rules"]),
                         tokenize("x=42;", self.minimal, self.rules))

    def test_invalid_regex_and_empty_tokens(self):
        for pattern in ("[", "[z-a]", "(", "a\\", "*a", ".", "я", "", "a*", "a?"):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                compile_rules([{"name": "T", "pattern": pattern}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
