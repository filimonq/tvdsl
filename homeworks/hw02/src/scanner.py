"""подключение дка и сканера hw1; позиции и eof для парсера hw2."""
import json
from pathlib import Path
import sys

HW1 = Path(__file__).resolve().parents[2] / "hw01"
sys.path.insert(0, str(HW1 / "src"))
from automata import DFA
from lexer import LexError, tokenize


def position(text, offset):
    line, col, previous_cr = 1, 1, False
    for char in text[:offset]:
        if char == "\r":
            line, col = line + 1, 1
        elif char == "\n":
            if not previous_cr:
                line += 1
            col = 1
        else:
            col += 1
        previous_cr = char == "\r"
    return {"offset": offset, "line": line, "col": col}


def scan(text):
    data = json.loads((HW1 / "output" / "dfa.json").read_text(encoding="utf-8"))
    dfa = DFA(data["transitions"], data["accepting"], data["start"], data["trap"])
    tokens = tokenize(text, dfa, data["rules"], with_positions=True)
    tokens.append({"kind": "EOF", "lexeme": "", "end": len(text),
                   **position(text, len(text))})
    return tokens
