"""Ручная разметка реальных текстов для проверки метрики (ADR-0004, раздел «Проверка на реальных текстах»).

Три шага:
  1) pre   — предразметка: каждый docs/<name>.txt → <name>.ann.txt с разметкой прямо в тексте;
  2) правка .ann.txt вручную в любом редакторе (это и есть ручная разметка);
  3) build — сборка .ann.txt → JSONL в формате train (те же spans/ignore/entity) с проверками.

    uv run python tools/annotate.py pre   --docs operator/real_check/docs
    uv run python tools/annotate.py build --docs operator/real_check/docs --out operator/real_check/real.jsonl
    uv run python tools/annotate.py apply --docs operator/real_check/docs --spec operator/real_check/spec.json

`apply` — альтернатива правке .ann.txt: spec.json {"r01": [["фрагмент", "КЛАСС"], ...]} размечает все вхождения
каждого фрагмента (длинные первыми, без пересечений, по границам слов) и перезаписывает .ann.txt.

Разметка: [[КЛАСС|фрагмент]]. Классы — из case/metric/policy.json. Нейтральная зона (реквизиты
организаций и т. п.): [[~ORG|фрагмент]]. Несколько фрагментов одной сущности (серия и номер паспорта)
получают общий номер: [[ПАСПОРТ#1|45 12]] № [[ПАСПОРТ#1|345678]].
Предразметка — только подсказка (baseline): её нужно проверить целиком, добавить пропущенное и убрать лишнее.
Реальные тексты не коммитятся: каталог operator/ вне Git.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "case" / "metric"))
sys.path.insert(0, str(ROOT / "case" / "ways" / "baseline"))
sys.dont_write_bytecode = True

import solve as baseline  # noqa: E402
from pii_metric import load_policy  # noqa: E402

MARK = re.compile(r"\[\[(~?[A-ZА-ЯЁ_]+)(?:#(\d+))?\|(.+?)\]\]", re.S)


def pre(docs: Path) -> None:
    for txt in sorted(docs.glob("*.txt")):
        if txt.name.endswith(".ann.txt"):
            continue
        out = txt.with_name(txt.stem + ".ann.txt")
        if out.exists():
            print(f"skip {out.name}: уже есть (удалите, чтобы перегенерировать)")
            continue
        text = txt.read_text(encoding="utf-8")
        if "[[" in text or "]]" in text:
            raise SystemExit(f"{txt.name}: в тексте уже есть [[ или ]] — уберите их")
        parts, pos = [], 0
        for a, b, label in baseline.find_spans(text):
            parts += [text[pos:a], f"[[{label}|{text[a:b]}]]"]
            pos = b
        parts.append(text[pos:])
        out.write_text("".join(parts), encoding="utf-8")
        print(f"pre {out.name}: {len(baseline.find_spans(text))} подсказок")


def parse(ann: str, labels: set[str]) -> tuple[str, list[dict], list[dict]]:
    text, spans, ignore = [], [], []
    pos = off = 0
    auto = 10_000
    for m in MARK.finditer(ann):
        text.append(ann[pos:m.start()])
        off += m.start() - pos
        label, group, frag = m.group(1), m.group(2), m.group(3)
        if label.lstrip("~") not in labels and not label.startswith("~"):
            raise ValueError(f"неизвестный класс {label!r} у фрагмента {frag!r}")
        if frag != frag.strip():
            raise ValueError(f"фрагмент с пробелами по краям: {frag!r}")
        if group is None:
            auto += 1
            eid = auto
        else:
            eid = int(group)
        rec = {"start": off, "end": off + len(frag), "label": label.lstrip("~"), "entity": eid}
        (ignore if label.startswith("~") else spans).append(rec)
        text.append(frag)
        off += len(frag)
        pos = m.end()
    text.append(ann[pos:])
    plain = "".join(text)
    if "[[" in plain or "]]" in plain:
        raise ValueError("остались незакрытые или неверные [[ ]] — проверьте синтаксис")
    for s in spans + ignore:
        a, b = s["start"], s["end"]
        if (a > 0 and plain[a - 1].isalnum() and plain[a].isalnum()) or (
                b < len(plain) and plain[b - 1].isalnum() and plain[b].isalnum()):
            raise ValueError(f"фрагмент режет слово: {plain[max(0, a - 10):b + 10]!r}")
    return plain, spans, ignore


def build(docs: Path, out: Path) -> None:
    labels = set(load_policy()["labels"])
    rows, errors = [], []
    for ann_path in sorted(docs.glob("*.ann.txt")):
        name = ann_path.name.removesuffix(".ann.txt")
        try:
            plain, spans, ignore = parse(ann_path.read_text(encoding="utf-8"), labels)
            src = docs / f"{name}.txt"
            if src.exists() and src.read_text(encoding="utf-8") != plain:
                raise ValueError("текст без разметки отличается от исходного .txt — правьте только [[ ]]")
        except ValueError as e:
            errors.append(f"{ann_path.name}: {e}")
            continue
        rows.append({"id": f"real-{name}",
                     "input": json.dumps({"task": "mask", "text": plain}, ensure_ascii=False),
                     "reference": json.dumps({"task": "mask", "spans": spans, "ignore": ignore,
                                              "meta": {"doc_type": "real", "families": [f"real.{name}"]}},
                                             ensure_ascii=False)})
    if errors:
        raise SystemExit("ошибки разметки:\n  " + "\n  ".join(errors))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    counts: dict[str, int] = {}
    for r in rows:
        for s in json.loads(r["reference"])["spans"]:
            counts[s["label"]] = counts.get(s["label"], 0) + 1
    print(json.dumps({"documents": len(rows), "spans_by_class": counts}, ensure_ascii=False, indent=1))


def apply(docs: Path, spec_path: Path) -> None:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    labels = set(load_policy()["labels"])
    for name, items in spec.items():
        text = (docs / f"{name}.txt").read_text(encoding="utf-8")
        taken = [False] * len(text)
        marks = []
        for frag, label in sorted(items, key=lambda x: -len(x[0])):
            if label.lstrip("~") not in labels and not label.startswith("~"):
                raise SystemExit(f"{name}: неизвестный класс {label}")
            found = 0
            for m in re.finditer(re.escape(frag), text):
                a, b = m.start(), m.end()
                if (a > 0 and text[a - 1].isalnum() and text[a].isalnum()) or (
                        b < len(text) and text[b - 1].isalnum() and text[b].isalnum()):
                    continue
                if any(taken[a:b]):
                    continue
                taken[a:b] = [True] * (b - a)
                marks.append((a, b, label))
                found += 1
            if not found:
                raise SystemExit(f"{name}: фрагмент не найден целиком: {frag!r}")
        marks.sort()
        out, pos = [], 0
        for a, b, label in marks:
            out += [text[pos:a], f"[[{label}|{text[a:b]}]]"]
            pos = b
        out.append(text[pos:])
        (docs / f"{name}.ann.txt").write_text("".join(out), encoding="utf-8")
    print(f"apply: {len(spec)} документов")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pre")
    p.add_argument("--docs", type=Path, required=True)
    b = sub.add_parser("build")
    b.add_argument("--docs", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("apply")
    s.add_argument("--docs", type=Path, required=True)
    s.add_argument("--spec", type=Path, required=True)
    a = ap.parse_args()
    if a.cmd == "pre":
        pre(a.docs)
    elif a.cmd == "apply":
        apply(a.docs, a.spec)
    else:
        build(a.docs, a.out)


if __name__ == "__main__":
    main()
