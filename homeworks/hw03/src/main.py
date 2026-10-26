"""cli hw3: check, test."""
import argparse
import json
from pathlib import Path
import sys
import unittest

from checker import check


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    command = commands.add_parser("check")
    source = command.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--file", type=Path)
    command.add_argument("--json", action="store_true",
                         help="вывести полный JSON, включая AST при семантических ошибках")
    commands.add_parser("test")
    args = cli.parse_args()
    if args.command == "test":
        suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern="test_*.py")
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    try:
        if args.file:
            with args.file.open(encoding="utf-8", newline="") as stream:
                text = stream.read()
        else:
            text = args.text
        result = check(text)
        if result["errors"] and not args.json:
            phases = {"lexical": "лексическая", "syntax": "синтаксическая", "semantic": "семантическая"}
            source_name = str(args.file) if args.file else "<text>"
            for error in result["errors"]:
                pos = error["pos"]
                print(f"{source_name}:{pos['line']}:{pos['col']}: "
                      f"{phases[error['phase']]} ошибка: {error['message']}", file=sys.stderr)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["errors"] else 0
    except (OSError, ValueError, RecursionError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
