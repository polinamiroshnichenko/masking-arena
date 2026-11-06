"""Выделить примеры одного типа (mask или unmask) — данные для отдельной кампании (ADR-0006).

    uv run python tools/select_task.py --task mask --data case/dataset/train.jsonl --out <campaign_dir>/train.jsonl

Строки копируются без изменений, порядок сохраняется.
"""
import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=("mask", "unmask"), required=True)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    kept = 0
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.data.open(encoding="utf-8") as src, a.out.open("w", encoding="utf-8") as dst:
        for line in src:
            if line.strip() and json.loads(json.loads(line)["input"])["task"] == a.task:
                dst.write(line if line.endswith("\n") else line + "\n")
                kept += 1
    print(json.dumps({"task": a.task, "examples": kept, "out": str(a.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
