# HW01: регулярные выражения → НКА → ДКА → минимизация

Построение минимального ДКА для токенов Funny и экспорт таблицы переходов в JSON.
Для проверки таблицы есть команда разбора текста на токены.

Требуется Python 3.10 или новее. Используется только стандартная библиотека.

## Запуск

Из корня репозитория:

```sh
cd homeworks/hw01
```

Запуск тестов и построение таблицы:

```sh
python3 src/main.py test
python3 src/main.py build
```

Тесты завершаются сообщением `Ran 9 tests` и `OK`. При построении выводятся размеры автоматов:

```text
NFA: 295 states; DFA: 125; minimal DFA: 122; trap: 1
```

Таблица сохраняется в `output/dfa.json`. При повторном построении файл перезаписывается.
Перед сохранением проверяется эквивалентность исходного и минимизированного ДКА.

## Разбор текста

```sh
python3 src/main.py scan --table output/dfa.json --text 'x=42;'
```

Результат:

```json
[
  {"kind": "IDENT", "lexeme": "x"},
  {"kind": "ASSIGN", "lexeme": "="},
  {"kind": "INT", "lexeme": "42"},
  {"kind": "SEMICOLON", "lexeme": ";"}
]
```

Выбирается самое длинное совпадение. При равной длине побеждает правило,
которое стоит раньше в списке:

```sh
python3 src/main.py scan --table output/dfa.json --text 'if ifx == ='
```

Результат: `IF("if")`, `IDENT("ifx")`, `EQ("==")`, `ASSIGN("=")`.

Пробелы и комментарии распознаются, но не добавляются в результат:

```sh
python3 src/main.py scan --table output/dfa.json --text 'x = 42; // comment'
python3 src/main.py scan --table output/dfa.json --text '// comment'
```

Первая команда возвращает токены `x=42;`, вторая — `[]`.

Чтение из файла:

```sh
python3 src/main.py scan --table output/dfa.json --file program.funny
```

Без `--table` автомат строится в памяти из стандартных правил:

```sh
python3 src/main.py scan --text 'x=12+y;'
```

## Ошибки входа

```sh
python3 src/main.py scan --table output/dfa.json --text '01'
python3 src/main.py scan --table output/dfa.json --text '@'
python3 src/main.py scan --table output/dfa.json --text 'я'
```

Ведущие нули, неизвестные символы и символы вне ASCII вызывают ошибку.
Сообщение содержит смещение от начала текста, начиная с нуля.
Код завершения — `0` при успехе и `1` при ошибке.

## Правила токенов

Стандартные правила заданы в [src/lexer.py](src/lexer.py).
Каждое правило содержит название `name`, регулярное выражение `pattern`
и флаг `skip`. Список правил можно передать в JSON-файле через `--rules`.

Пример `my_rules.json`:

```json
[
  {"name": "WORD", "pattern": "[A-Za-z]+", "skip": false},
  {"name": "WS", "pattern": "[ \\t\\r\\n]+", "skip": true}
]
```

```sh
python3 src/main.py build --rules my_rules.json --out output/my_dfa.json
python3 src/main.py scan --table output/my_dfa.json --text 'hello world'
```

Поддерживаются символы, экранирование, конкатенация, `|`, группы `()`,
классы символов с диапазонами и отрицанием, повторения `*`, `+`, `?`.
После `\` буквы `n`, `r`, `t` обозначают таб и переводы строк;
остальные экранированные символы считаются литералами.
Не поддерживаются `.`, якоря, сокращения вроде `\d`, просмотр вперёд и назад,
обратные ссылки. Правила, допускающие пустой токен, запрещены.

## Таблица ДКА

В `output/dfa.json` сохраняются:

| Поле | Значение |
| --- | --- |
| `transitions` | Таблица переходов: строка — состояние, столбец — ASCII-код |
| `accepting` | Индекс правила для конечного состояния или `null` |
| `start` | Начальное состояние |
| `trap` | Ловушка |
| `rules` | Список правил в порядке приоритета |
| `alphabet_size` | Размер алфавита: 128 |
| `outside_alphabet` | Обработка символов вне алфавита: `trap` |
| `format` | Версия формата: `funny-dfa-v1` |

Переход по ASCII-символу: `transitions[state][ord(char)]`.
Символы вне ASCII направляются в ловушку до обращения к таблице.
Пути, переданные в аргументах CLI, отсчитываются от текущей папки терминала.
Стандартный путь вывода определяется относительно `src/main.py`.

## Файлы

| Файл | Назначение |
| --- | --- |
| [src/automata.py](src/automata.py) | Разбор регулярных выражений, НКА, детерминизация, минимизация, проверка эквивалентности |
| [src/lexer.py](src/lexer.py) | Правила Funny и разбор текста на токены |
| [src/main.py](src/main.py) | Команды `build`, `scan`, `test` |
| [src/test_hw01.py](src/test_hw01.py) | Автоматические тесты |
| [cases.json](cases.json) | Входные строки и ожидаемые токены или позиции ошибок |
| [output/dfa.json](output/dfa.json) | Таблица минимизированного ДКА |
| [REPORT.md](REPORT.md) | Описание алгоритмов и результаты проверок |
