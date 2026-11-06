"""Локальный прогон кандидата и оценка — тем же контрактом, что у runner и оценщика арены.

    uv run python tools/evaluate.py --candidate case/ways/baseline --data case/dataset/train.jsonl \
        --out results/baseline/train

Решатель запускается отдельным процессом на каждый пример (`python -I solve.py`, cwd во временном
каталоге, вход через stdin), как в runner арены, но без Docker. Оценка — тот же код, что в
case/metric/score.py и aggregate.py (флаг --via-scripts запускает именно эти скрипты подпроцессами).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRIC = ROOT / "case" / "metric"
sys.path.insert(0, str(METRIC))
sys.dont_write_bytecode = True

from pii_metric import aggregate, load_policy, score_example  # noqa: E402


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_one(solve: Path, example: dict, timeout: float) -> dict:
    payload = json.dumps({"input": example["input"], "assets_dir": "/assets"}, ensure_ascii=False)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run([sys.executable, "-I", str(solve)], input=payload.encode(), cwd=tmp,
                              capture_output=True, timeout=timeout)
    seconds = time.perf_counter() - started
    if proc.returncode != 0:
        return {"id": example["id"], "error": proc.stderr.decode(errors="replace")[-2000:],
                "seconds": seconds}
    try:
        answer = json.loads(proc.stdout)["answer"]
        if not isinstance(answer, str):
            raise ValueError("answer is not a string")
    except (ValueError, KeyError) as e:
        return {"id": example["id"], "error": f"bad runner output: {e}", "seconds": seconds}
    return {"id": example["id"], "answer": answer, "seconds": seconds}


def score_via_scripts(example: dict, answer: str) -> dict:
    out = subprocess.run([sys.executable, "-I", str(METRIC / "score.py")], cwd="/",
                         input=json.dumps({"example": example, "answer": answer}).encode(),
                         capture_output=True, check=True).stdout
    return json.loads(out)


def aggregate_via_script(results: list[dict]) -> dict:
    out = subprocess.run([sys.executable, "-I", str(METRIC / "aggregate.py")], cwd="/",
                         input=json.dumps({"results": results}).encode(),
                         capture_output=True, check=True).stdout
    return json.loads(out)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--candidate", type=Path, required=True, help="каталог с solve.py")
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--out", type=Path, help="каталог для answers.jsonl, per_example.jsonl, report.json")
    p.add_argument("--jobs", type=int, default=8)
    p.add_argument("--timeout", type=float, default=60)
    p.add_argument("--limit", type=int)
    p.add_argument("--via-scripts", action="store_true", help="оценивать подпроцессами score.py/aggregate.py")
    a = p.parse_args()

    examples = load_jsonl(a.data)[: a.limit]
    solve = (a.candidate / "solve.py").resolve()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(a.jobs) as pool:
        runs = list(pool.map(lambda ex: run_one(solve, ex, a.timeout), examples))
    wall = time.perf_counter() - t0
    failures = [r for r in runs if "error" in r]
    if failures:
        print(json.dumps(failures[:3], ensure_ascii=False, indent=1), file=sys.stderr)
        raise SystemExit(f"{len(failures)} solver failures — оценка набора не выполняется (как в арене)")

    policy = load_policy()
    per_example, results = [], []
    for ex, run in zip(examples, runs, strict=True):
        res = score_via_scripts(ex, run["answer"]) if a.via_scripts else score_example(ex, run["answer"], policy)
        per_example.append({"id": ex["id"], "score": res["score"], "statistics": res["statistics"],
                            "solver_seconds": run["seconds"]})
        results.append({"example_id": ex["id"], "answer": run["answer"], **res})
    agg = aggregate_via_script(results) if a.via_scripts else aggregate([r["statistics"] for r in per_example], policy)
    report = {"candidate": str(a.candidate), "data": str(a.data), "examples": len(examples),
              "score": agg["score"], **agg["statistics"],
              "solver_seconds_total": round(sum(r["seconds"] for r in runs), 2),
              "wall_seconds": round(wall, 2), "solver_tokens": 0}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        with (a.out / "answers.jsonl").open("w", encoding="utf-8") as f:
            for r in runs:
                f.write(json.dumps({"id": r["id"], "answer": r["answer"]}, ensure_ascii=False) + "\n")
        with (a.out / "per_example.jsonl").open("w", encoding="utf-8") as f:
            for r in per_example:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        (a.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                           encoding="utf-8")


if __name__ == "__main__":
    main()
