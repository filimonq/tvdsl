"""запускаем построение автомата, разбор текста или тесты из терминала."""
import argparse
import json
from pathlib import Path
import sys
import unittest

from automata import DFA, distinguishing_word
from lexer import LexError, compile_rules, default_rules, tokenize

SOURCE_DIR = Path(__file__).resolve().parent
HERE = SOURCE_DIR.parent


def main():
    # разбираем команду и её параметры: build, scan или test
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="regex -> NFA -> DFA -> minimized JSON")
    build.add_argument("--rules", type=Path, help="JSON list of name/pattern/skip rules")
    build.add_argument("--out", type=Path, default=HERE / "output" / "dfa.json")
    scan = sub.add_parser("scan", help="tokenize source with a generated or loaded DFA")
    source = scan.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--file", type=Path)
    scan.add_argument("--table", type=Path)
    sub.add_parser("test", help="print test cases and run algorithm checks")
    args = parser.parse_args()
    try:
        if args.command == "test":
            # находим тесты в папке src и запускаем все проверки
            suite = unittest.defaultTestLoader.discover(str(SOURCE_DIR), pattern="test_*.py")
            return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
        if args.command == "build":
            # правила берём из указанного файла или из стандартного списка
            rules = json.loads(args.rules.read_text(encoding="utf-8")) if args.rules else default_rules()
            nfa, original, minimal = compile_rules(rules)
            # перед сохранением проверяем, что минимизация сохранила ответы автомата
            if distinguishing_word(original, minimal) is not None:
                raise ValueError("Minimization changed the recognized language")
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(minimal.to_dict(rules), indent=2) + "\n", encoding="utf-8")
            print(f"NFA: {len(nfa.edges)} states; DFA: {len(original.transitions)}; "
                  f"minimal DFA: {len(minimal.transitions)}; trap: {minimal.trap}")
            print(f"ASCII equivalence verified; table: {args.out}")
        else:
            if args.table:
                # восстанавливаем готовый автомат из сохранённой таблицы
                data = json.loads(args.table.read_text(encoding="utf-8"))
                dfa = DFA(data["transitions"], data["accepting"], data["start"], data["trap"])
                rules = data["rules"]
            else:
                # без файла таблицы строим автомат в памяти
                rules = default_rules()
                _, _, dfa = compile_rules(rules)
            text = args.file.read_text(encoding="utf-8") if args.file else args.text
            print(json.dumps(tokenize(text, dfa, rules), ensure_ascii=False, indent=2))
        return 0
    except (LexError, ValueError, OSError, KeyError, TypeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
