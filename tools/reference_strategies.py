"""Поведение метрики на эталонных и «жульнических» стратегиях + сравнение с альтернативными метриками.

    uv run python tools/reference_strategies.py --data case/dataset/train.jsonl \
        --baseline results/baseline/train/answers.jsonl --out results/metric_sensitivity

Стратегии генерируют ответы прямо из эталона (oracle) или по простому правилу, поэтому это проверка
метрики, а не решения. Результат — таблица для ADR-0003.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "case" / "metric"))
sys.dont_write_bytecode = True

from pii_metric import TOKEN_RE, aggregate, load_policy, score_example  # noqa: E402


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def mask_spans(text: str, spans: list[tuple[int, int, str]]) -> str:
    spans = sorted(spans)
    numbering: dict[tuple[str, str], int] = {}
    counters: dict[str, int] = {}
    out, reps, pos = [], [], 0
    for a, b, label in spans:
        if a < pos:
            continue
        orig = text[a:b]
        key = (label, orig)
        if key not in numbering:
            counters[label] = counters.get(label, 0) + 1
            numbering[key] = counters[label]
        ph = f"[{label}_{numbering[key]}]"
        out += [text[pos:a], ph]
        reps.append({"placeholder": ph, "original": orig})
        pos = b
    out.append(text[pos:])
    return json.dumps({"masked_text": "".join(out), "replacements": reps}, ensure_ascii=False)


def gold_full(ref: dict) -> str:
    return ref["segments"][0] + "".join(g + s for g, s in zip(ref["golds"], ref["segments"][1:], strict=True))


def strategy(pair: str, inp: dict, ref: dict) -> str:
    """pair = "<стратегия mask> + <стратегия unmask>"."""
    name, unmask_name = (x.strip() for x in pair.split("+"))
    if inp["task"] == "unmask":
        if unmask_name == "oracle":
            return json.dumps({"text": gold_full(ref)}, ensure_ascii=False)
        reply = inp["reply"]
        if unmask_name == "plain_replace":
            for ph, v in inp["mapping"].items():
                reply = reply.replace(ph, v)
        elif unmask_name != "identity":
            raise KeyError(unmask_name)
        return json.dumps({"text": reply}, ensure_ascii=False)
    text = inp["text"]
    gold = [(s["start"], s["end"], s["label"]) for s in ref["spans"]]
    if name == "identity":
        return mask_spans(text, [])
    if name == "oracle":
        return mask_spans(text, gold)
    if name.startswith("oracle_drop_"):
        cls = name.removeprefix("oracle_drop_")
        return mask_spans(text, [s for s in gold if s[2] != cls])
    if name == "oracle_fio_surname_only":  # частичное маскирование ФИО: только первое слово
        spans = []
        for a, b, lbl in gold:
            if lbl == "ФИО":
                m = TOKEN_RE.search(text, a, b)
                spans.append((m.start(), m.end(), lbl))
            else:
                spans.append((a, b, lbl))
        return mask_spans(text, spans)
    if name == "mask_all_tokens":
        return mask_spans(text, [(m.start(), m.end(), "ФИО") for m in TOKEN_RE.finditer(text)])
    if name == "mask_all_digits_and_capitals":
        return mask_spans(text, [(m.start(), m.end(), "ФИО") for m in TOKEN_RE.finditer(text)
                                 if m.group().isdigit() or m.group()[0].isupper()])
    if name == "oracle_plus_all_digits":
        extra = [(m.start(), m.end(), "ИНН") for m in re.finditer(r"\d+", text)]
        covered = {i for a, b, _ in gold for i in range(a, b)}
        extra = [s for s in extra if not any(i in covered for i in range(s[0], s[1]))]
        return mask_spans(text, gold + extra)
    raise KeyError(name)


def alt_metrics(per_ex: list[dict], examples: list[dict]) -> dict:
    """Альтернативы для сравнения: токенные micro P/R/F1/F2, macro-F2, строгий entity-recall."""
    tp = fp = fn = 0
    for st in (p for p in per_ex if p["task"] == "mask"):
        tp += st["masked_pii_tokens"]
        fp += st["masked_tokens"] - st["masked_pii_tokens"]
        fn += st["_gold_tokens"] - st["masked_pii_tokens"]
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 0.0

    def fb(beta: float, pp: float, rr: float) -> float:
        return (1 + beta**2) * pp * rr / (beta**2 * pp + rr) if pp + rr else 0.0

    return {"tok_P": p, "tok_R": r, "F1": fb(1, p, r), "F2": fb(2, p, r)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--baseline", type=Path, help="answers.jsonl baseline для той же выборки")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    examples = load_jsonl(a.data)
    policy = load_policy()
    names = ["identity + identity", "mask_all_tokens + oracle", "mask_all_digits_and_capitals + oracle",
             "oracle_plus_all_digits + oracle", "oracle_fio_surname_only + oracle"] + [
             f"oracle_drop_{c} + oracle" for c in ("ФИО", "АДРЕС", "КАРТА")] + [
             "oracle + plain_replace", "oracle + oracle"]
    answers_by_name = {}
    for name in names:
        answers_by_name[name] = [strategy(name, json.loads(e["input"]), json.loads(e["reference"]))
                                 for e in examples]
    if a.baseline:
        base = {r["id"]: r["answer"] for r in load_jsonl(a.baseline)}
        answers_by_name["baseline (regex + plain_replace)"] = [base[e["id"]] for e in examples]
    rows = []
    for name, answers in answers_by_name.items():
        per_ex = []
        for e, ans in zip(examples, answers, strict=True):
            st = score_example(e, ans, policy)["statistics"]
            if st["task"] == "mask":
                ref = json.loads(e["reference"])
                text = json.loads(e["input"])["text"]
                st["_gold_tokens"] = sum(1 for m in TOKEN_RE.finditer(text)
                                         if any(s["start"] <= m.start() and m.end() <= s["end"]
                                                for s in ref["spans"]))
            per_ex.append(st)
        agg = aggregate(per_ex, policy)
        s = agg["statistics"]
        rows.append({"strategy": name, "score": agg["score"], "R_hmean": s["recall_class_hmean"],
                     "R_macro": s["recall_macro"], "P": s["precision"], "phi": s["precision_penalty"],
                     "A": s["restore_accuracy"], "S_mask": s["recall_class_hmean"] * s["precision_penalty"],
                     "S_mask_arith": s["recall_macro"] * s["precision_penalty"],
                     **alt_metrics(per_ex, examples)})
    cols = ["strategy", "score", "S_mask", "R_hmean", "R_macro", "S_mask_arith", "P", "phi", "A", "F1", "F2"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(r[c] if isinstance(r[c], str) else f"{r[c]:.3f}" for c in cols) + " |")
    table = "\n".join(lines)
    print(table)
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "strategies.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n")
        (a.out / "strategies.md").write_text(table + "\n")


if __name__ == "__main__":
    main()
