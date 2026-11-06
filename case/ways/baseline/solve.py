"""Baseline: набор регулярных выражений + обратная подстановка без склонения.

Контракт runner арены (немодельный режим): stdin {"input": "<JSON-строка задачи>", "assets_dir": ...}
→ stdout {"answer": "<JSON-строка ответа>"}. Только стандартная библиотека Python 3.12.
"""
import json
import re
import sys

D = r"(?<!\d)"
ND = r"(?!\d)"
UP = "А-ЯЁ"
LO = "а-яё"

PATTERNS = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("ТЕЛЕФОН", re.compile(D + r"(?:\+7|8)[\s-]?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}" + ND)),
    ("СНИЛС", re.compile(D + r"\d{3}-\d{3}-\d{3}[\s-]\d{2}" + ND)),
    ("КАРТА", re.compile(D + r"\d{4}([\s-]?)\d{4}\1\d{4}\1\d{4}" + ND)),
    ("СЧЕТ", re.compile(D + r"408\d{17}" + ND)),
    ("ИНН", re.compile(D + r"\d{12}" + ND)),
    ("ПАСПОРТ", re.compile(D + r"\d{2}\s?\d{2}\s(?:№\s?)?\d{6}" + ND)),
    ("ДАТА_РОЖДЕНИЯ", re.compile(r"(?:дата рождения|родил(?:ся|ась))[:\s]+(\d{2}\.\d{2}\.\d{4})", re.I)),
    ("ДАТА_РОЖДЕНИЯ", re.compile(D + r"(\d{2}\.\d{2}\.\d{4})\s?г\.\s?р\.")),
    # Фамилия И.О. / И.О. Фамилия / Фамилия Имя Отчество (по суффиксу отчества)
    ("ФИО", re.compile(rf"[{UP}][{LO}]+(?:-[{UP}][{LO}]+)?\s[{UP}]\.\s?[{UP}]\.")),
    ("ФИО", re.compile(rf"[{UP}]\.\s?[{UP}]\.\s?[{UP}][{LO}]+")),
    ("ФИО", re.compile(rf"[{UP}][{LO}]+\s[{UP}][{LO}]+\s[{UP}][{LO}]+(?:вич|вна|ична|ич)[{LO}]{{0,3}}\b")),
    ("АДРЕС", re.compile(r"(?:ул\.|улица|пр-т|проспект|пер\.|переулок)\s[^,\n]+,\s(?:д\.|дом)\s?\d+[^\n,]*"
                        r"(?:,\s(?:кв\.|квартира)\s?\d+)?")),
]


def find_spans(text: str) -> list[tuple[int, int, str]]:
    spans = []
    for label, rx in PATTERNS:
        for m in rx.finditer(text):
            g = 1 if rx.groups and m.group(1) and label == "ДАТА_РОЖДЕНИЯ" else 0
            spans.append((m.start(g), m.end(g), label))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    chosen, end = [], -1
    for s in spans:
        if s[0] >= end:
            chosen.append(s)
            end = s[1]
    return chosen


def mask(text: str) -> dict:
    numbering: dict[tuple[str, str], int] = {}
    counters: dict[str, int] = {}
    out, reps, pos = [], [], 0
    for a, b, label in find_spans(text):
        orig = text[a:b]
        key = (label, orig)
        if key not in numbering:
            counters[label] = counters.get(label, 0) + 1
            numbering[key] = counters[label]
        ph = f"[{label}_{numbering[key]}]"
        out.append(text[pos:a])
        out.append(ph)
        reps.append({"placeholder": ph, "original": orig})
        pos = b
    out.append(text[pos:])
    return {"masked_text": "".join(out), "replacements": reps}


def unmask(mapping: dict, reply: str) -> dict:
    for ph, value in mapping.items():
        reply = reply.replace(ph, value)
    return {"text": reply}


def solve(task_input: str) -> str:
    task = json.loads(task_input)
    if task["task"] == "mask":
        return json.dumps(mask(task["text"]), ensure_ascii=False)
    return json.dumps(unmask(task["mapping"], task["reply"]), ensure_ascii=False)


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    print(json.dumps({"answer": solve(payload["input"])}, ensure_ascii=False))
