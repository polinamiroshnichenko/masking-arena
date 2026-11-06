"""Агрегация уровня набора по контракту арены: stdin {"results": [...]} → stdout {"score": float, ...}.
Каждая запись results содержит statistics, которые вернул score.py для своего примера."""
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pii_metric import aggregate, load_policy  # noqa: E402


def main() -> None:
    results = json.load(sys.stdin)["results"]
    stats = []
    for r in results:
        st = r.get("statistics")
        if not isinstance(st, dict) or "task" not in st:
            raise ValueError(f"result {r.get('example_id')!r} has no metric statistics")
        stats.append(st)
    print(json.dumps(aggregate(stats, load_policy()), ensure_ascii=False))


if __name__ == "__main__":
    main()
