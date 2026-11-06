"""Smoke кейса через код арены: замороженный пакет метрики → evaluate_answer → aggregate_results.

    uv run --project external/arena python tools/arena_smoke.py \
        --frozen /tmp/pii-metric-frozen --data case/dataset/train.jsonl \
        --answers results/baseline/train/answers.jsonl --report results/baseline/train/report.json --limit 200

Проверяет, что арена загружает пакет (`load_metric`), вызывает score.py на каждый пример и aggregate.py
на набор, и что её итог совпадает с локальным tools/evaluate.py на тех же ответах.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evaluator.contracts import Example
from evaluator.metric_package import load_metric, metric_package_provenance
from evaluator.metrics import aggregate_results, evaluate_answer
from evaluator.reliability import empty_usage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "case" / "metric"))
sys.dont_write_bytecode = True
from pii_metric import aggregate, load_policy, score_example  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frozen", type=Path, required=True)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--answers", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=200)
    a = ap.parse_args()
    metric = load_metric(a.frozen)
    rows = [json.loads(x) for x in a.data.read_text(encoding="utf-8").splitlines() if x.strip()]
    answers = {json.loads(x)["id"]: json.loads(x)["answer"]
               for x in a.answers.read_text(encoding="utf-8").splitlines() if x.strip()}
    # подвыборка с обоими типами примеров
    rows = [r for r in rows if "-m-" in r["id"]][: a.limit * 7 // 10] + \
           [r for r in rows if "-u-" in r["id"]][: a.limit * 3 // 10]
    results, local = [], []
    policy = load_policy()
    for r in rows:
        ex = Example(id=r["id"], input=r["input"], reference=r["reference"])
        res = evaluate_answer(metric, ex, answers[r["id"]], usage=empty_usage())
        results.append({"example_id": ex.id, "answer": answers[r["id"]], **res,
                        "resources": {"solver": {"wall_seconds": 0.0, "usage": empty_usage()},
                                      "judge": {"wall_seconds": 0.0, "usage": empty_usage()}}})
        local.append(score_example(r, answers[r["id"]], policy)["statistics"])
    arena_score = aggregate_results(metric, results)
    local_score = aggregate(local, policy)["score"]
    out = {"metric_package": metric_package_provenance(a.frozen), "examples": len(rows),
           "arena_score": arena_score, "local_score": local_score, "match": abs(arena_score - local_score) < 1e-12}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if not out["match"]:
        raise SystemExit("arena and local scores differ")


if __name__ == "__main__":
    main()
