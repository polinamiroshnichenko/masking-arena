"""Шум метрики: бутстрэп по документам для разных размеров набора (обоснование размеров split).

    uv run python tools/bootstrap.py --per-example results/baseline/train/per_example.jsonl \
        [--paired results/other/train/per_example.jsonl] --sizes 100,200,400,800

Ресэмплинг стратифицирован по типу примера (mask/unmask) с сохранением их доли.
С --paired считается SE разности двух кандидатов на одних и тех же примерах.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "case" / "metric"))
sys.dont_write_bytecode = True

from pii_metric import aggregate, load_policy  # noqa: E402


def load(path: Path) -> dict[str, dict]:
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {r["id"]: r["statistics"] for r in rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-example", type=Path, required=True)
    ap.add_argument("--paired", type=Path)
    ap.add_argument("--sizes", default="100,200,300,400,600,800")
    ap.add_argument("--unmask-ratio", type=float, default=0.3, help="доля unmask в наборе")
    ap.add_argument("--reps", type=int, default=400)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    policy = load_policy()
    base = load(a.per_example)
    other = load(a.paired) if a.paired else None
    mask_ids = [i for i, s in base.items() if s["task"] == "mask"]
    unmask_ids = [i for i, s in base.items() if s["task"] == "unmask"]
    rng = random.Random(0)
    out = []
    for n in (int(x) for x in a.sizes.split(",")):
        n_u = round(n * a.unmask_ratio)
        n_m = n - n_u
        scores, diffs = [], []
        for _ in range(a.reps):
            ids = rng.choices(mask_ids, k=n_m) + rng.choices(unmask_ids, k=n_u)
            s1 = aggregate([base[i] for i in ids], policy)["score"]
            scores.append(s1)
            if other:
                diffs.append(aggregate([other[i] for i in ids], policy)["score"] - s1)
        row = {"n": n, "n_mask": n_m, "n_unmask": n_u, "mean": statistics.mean(scores),
               "se": statistics.stdev(scores)}
        if other:
            row |= {"diff_mean": statistics.mean(diffs), "diff_se": statistics.stdev(diffs)}
        out.append(row)
        print(json.dumps(row))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
