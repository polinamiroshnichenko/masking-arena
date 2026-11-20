"""Чувствительность метрики к её трём параметрам (порог precision, степень штрафа, кредит за частичное).

    uv run python tools/metric_params.py --data case/dataset/train.jsonl \
        --baseline results/baseline/train/answers.jsonl --out results/metric_sensitivity/params.md

Ответы стратегий строятся из эталона (tools/reference_strategies.py) — это проверка метрики, не решения.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "case" / "metric"))
sys.path.insert(0, str(ROOT / "tools"))
sys.dont_write_bytecode = True

import reference_strategies as R  # noqa: E402
from pii_metric import aggregate, load_policy, score_example  # noqa: E402

STRATEGIES = {
    "все токены": "mask_all_tokens + oracle",
    "эталон + все числа": "oracle_plus_all_digits + oracle",
    "ФИО без фамилии": "oracle_fio_surname_only + oracle",
    "без класса «карта»": "oracle_drop_КАРТА + oracle",
}
GRID_PHI = [(0.8, 2), (0.85, 2), (0.9, 1), (0.9, 2), (0.9, 3), (0.95, 2)]
GRID_CREDIT = [0.0, 0.25, 0.5, 0.75, 1.0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    ex = [json.loads(x) for x in a.data.read_text(encoding="utf-8").splitlines() if x.strip()]
    base = {json.loads(x)["id"]: json.loads(x)["answer"] for x in a.baseline.read_text(encoding="utf-8").splitlines()}
    answers = {k: [R.strategy(v, json.loads(e["input"]), json.loads(e["reference"])) for e in ex]
               for k, v in STRATEGIES.items()}
    answers["baseline"] = [base[e["id"]] for e in ex]
    p0 = load_policy()

    def run(**kw) -> dict[str, float]:
        p = dict(p0, **kw)
        return {k: aggregate([score_example(e, x, p)["statistics"] for e, x in zip(ex, ans, strict=True)], p)["score"]
                for k, ans in answers.items()}

    names = list(answers)
    lines = ["Порог и степень штрафа φ(P) = min(1, P/порог)^степень (кредит частичного = 0.5):", "",
             "| порог | степень | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names)]
    for fl, pw in GRID_PHI:
        r = run(precision_floor=fl, precision_penalty_power=pw)
        mark = " **(v1)**" if (fl, pw) == (p0["precision_floor"], p0["precision_penalty_power"]) else ""
        lines.append(f"| {fl}{mark} | {pw} | " + " | ".join(f"{r[n]:.3f}" for n in names) + " |")
    lines += ["", "Кредит за частично замаскированную сущность (порог 0.9, степень 2):", "",
              "| кредит | ФИО без фамилии | baseline |", "|---|---|---|"]
    for c in GRID_CREDIT:
        r = run(partial_credit=c)
        mark = " **(v1)**" if c == p0["partial_credit"] else ""
        lines.append(f"| {c}{mark} | {r['ФИО без фамилии']:.3f} | {r['baseline']:.3f} |")
    table = "\n".join(lines)
    print(table)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(table + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
