"""cli hw2: parse, scan, test."""
import argparse
import json
from pathlib import Path
import sys
import unittest

from parser import parse
from scanner import LexError, position, scan


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    for name in ("parse", "scan"):
        command = commands.add_parser(name)
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument("--text")
        source.add_argument("--file", type=Path)
    commands.add_parser("test")
    args = cli.parse_args()
    if args.command == "test":
        suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent),
                                                  pattern="test_*.py")
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    try:
        if args.file:
            # newline='' сохраняет cr/crlf и исходные смещения.
            with args.file.open(encoding="utf-8", newline="") as stream:
                text = stream.read()
        else:
            text = args.text
        if args.command == "parse":
            result = parse(text)
        else:
            try:
                result = {"tokens": scan(text), "errors": []}
            except LexError as error:
                result = {"tokens": [], "errors": [{"phase": "lexical", "message": str(error),
                                                    "pos": position(text, error.offset)}]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["errors"] else 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
