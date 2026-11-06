"""Оценка одного примера по контракту арены: stdin {"example": {...}, "answer": "..."} →
stdout {"score": float, "statistics": {...}}. Итог набора считает aggregate.py (см. metric.json)."""
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pii_metric import load_policy, score_example  # noqa: E402


def main() -> None:
    payload = json.load(sys.stdin)
    answer = payload["answer"]
    if not isinstance(answer, str):
        answer = json.dumps(answer, ensure_ascii=False)
    print(json.dumps(score_example(payload["example"], answer, load_policy()), ensure_ascii=False))


if __name__ == "__main__":
    main()
