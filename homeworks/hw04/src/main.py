"""cli hw4: ir, run, verify, test."""
import argparse
import json
from pathlib import Path
import sys
import unittest

from flow import analyze, cfg_dot, interference_dot
from ir import compile_source, format_ir
from optimize import PASSES, optimize
from runtime import compare, execute


def format_analysis(analyses):
    def names(values):
        return "{" + ", ".join(values) + "}"

    lines = ["МЕТРИКИ (до -> после)",
             "Функция | инструкции | временные | блоки | цвета"]
    for name, before in analyses["before"].items():
        after = analyses["after"][name]
        values = [f"{before['metrics'][key]} -> {after['metrics'][key]}"
                  for key in ("instructions", "temporaries", "blocks", "colors")]
        lines.append(name + " | " + " | ".join(values))
    for stage, title in (("before", "ДО"), ("after", "ПОСЛЕ")):
        lines.append(f"\nCFG И КОНФЛИКТЫ {title} ОПТИМИЗАЦИЙ")
        for name, analysis in analyses[stage].items():
            lines.append(f"Функция {name}:")
            for block in analysis["blocks"]:
                targets = ", ".join(f"B{s}" for s in block["successors"]) or "выход"
                labels = ", ".join(block["labels"]) or "без метки"
                lines.append(f"  B{block['id']} ({labels}) [IR {block['start']}:{block['end']}) -> {targets}")
                lines.append(f"    живые на входе: {names(block['live_in'])}; "
                             f"на выходе: {names(block['live_out'])}")
            graph = analysis["interference"]
            lines.append("  Временная | конфликтует с | цвет")
            for temporary, neighbors in graph["edges"].items():
                lines.append(f"  {temporary} | {', '.join(neighbors) or '-'} | {graph['colors'][temporary]}")
            if not graph["edges"]:
                lines.append("  Временных нет; цветов: 0")
    return "\n".join(lines)


def format_checks(checks):
    def outcome(value):
        return "ошибка: " + value["error"] if "error" in value else json.dumps(value["result"])

    lines = []
    for index, item in enumerate(checks, 1):
        status = "СОВПАЛО" if item["equal"] else "НЕ СОВПАЛО"
        if item["inconclusive"]:
            status = "НЕ ОПРЕДЕЛЕНО (лимит ресурсов)"
        lines.append(f"{index}. Аргументы: {json.dumps(item['args'])} — {status}")
        lines.append("   До:    " + outcome(item["before"]))
        lines.append("   После: " + outcome(item["after"]))
    lines.append(f"Совпало: {sum(item['equal'] for item in checks)} из {len(checks)}")
    return "\n".join(lines)


def export(directory, before, after, analyses):
    directory.mkdir(parents=True, exist_ok=True)
    for stage, program in (("before", before), ("after", after)):
        (directory / f"{stage}.ir").write_text(format_ir(program) + "\n", encoding="utf-8")
        (directory / f"{stage}.json").write_text(json.dumps(program, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
        # индексы вместо исходных имён исключают коллизии имён файлов.
        for index, function in enumerate(program["functions"]):
            (directory / f"{stage}_{index}_cfg.dot").write_text(cfg_dot(function) + "\n", encoding="utf-8")
            (directory / f"{stage}_{index}_conflicts.dot").write_text(interference_dot(function) + "\n",
                                                                   encoding="utf-8")
    (directory / "analysis.json").write_text(json.dumps(analyses, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8")
    (directory / "analysis.txt").write_text(format_analysis(analyses) + "\n", encoding="utf-8")


def parse_passes(value):
    names = [] if value == "none" else value.split(",")
    if any(name not in PASSES for name in names):
        raise argparse.ArgumentTypeError("Допустимы const,algebra,cse,dce или none")
    return names


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    for name in ("ir", "run", "verify"):
        command = commands.add_parser(name)
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument("--file", type=Path)
        source.add_argument("--text")
        command.add_argument("--passes", type=parse_passes, default=list(PASSES))
        command.add_argument("--no-opt", action="store_true")
        command.add_argument("--format", choices=("text", "json"), default="text")
        if name == "ir":
            command.add_argument("--out-dir", type=Path)
        else:
            command.add_argument("--function", default="main")
            command.add_argument("--max-steps", type=int, default=100000)
            if name == "run":
                command.add_argument("--args", default="[]", help="JSON-массив аргументов")
            else:
                command.add_argument("--inputs", required=True, help="JSON-массив наборов аргументов")
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
        result = compile_source(text)
        if result["errors"]:
            for error in result["errors"]:
                pos = error["pos"]
                phase = {"lexical": "лексическая", "syntax": "синтаксическая",
                         "semantic": "семантическая", "ir": "генерация IR"}[error["phase"]]
                print(f"{args.file or '<text>'}:{pos['line']}:{pos['col']}: "
                      f"{phase}: {error['message']}", file=sys.stderr)
            return 1
        before = result["ir"]
        after = optimize(before, [] if args.no_opt else args.passes)
        if args.command == "ir":
            analyses = {"before": analyze(before), "after": analyze(after)}
            if args.out_dir:
                export(args.out_dir, before, after, analyses)
            if args.format == "json":
                print(json.dumps({"before": before, "after": after, "analysis": analyses},
                                 ensure_ascii=False, indent=2))
            else:
                enabled = [] if args.no_opt else args.passes
                print("Оптимизации: " + (", ".join(enabled) or "выключены"))
                print("=== IR ДО ОПТИМИЗАЦИЙ ===\n" + format_ir(before))
                print("=== IR ПОСЛЕ ОПТИМИЗАЦИЙ ===\n" + format_ir(after))
                print(format_analysis(analyses))
        elif args.command == "run":
            supplied = json.loads(args.args)
            if not isinstance(supplied, list):
                raise ValueError("--args должен содержать JSON-массив")
            values = execute(after, args.function, supplied, args.max_steps)
            if args.format == "json":
                print(json.dumps({"result": values}))
            else:
                print(f"Функция: {args.function}; аргументы: {json.dumps(supplied)}")
                print("Результат: " + json.dumps(values))
        else:
            inputs = json.loads(args.inputs)
            if not isinstance(inputs, list) or not inputs or not all(isinstance(x, list) for x in inputs):
                raise ValueError("--inputs должен содержать непустой массив наборов аргументов")
            checks = [{"args": supplied, **compare(before, after, args.function, supplied, args.max_steps)}
                      for supplied in inputs]
            print(json.dumps(checks, ensure_ascii=False, indent=2) if args.format == "json"
                  else format_checks(checks))
            return 0 if all(item["equal"] for item in checks) else 1
        return 0
    except (OSError, ValueError, RecursionError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
