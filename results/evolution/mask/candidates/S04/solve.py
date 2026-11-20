"""tagger-t1: обучаемый токен-теггер ФИО поверх структурных правил S02 (rules-a1).

Контракт runner арены (немодельный режим): stdin {"input": "<JSON-строка задачи>", "assets_dir": ...}
→ stdout {"answer": "<JSON-строка ответа>"}. Стандартная библиотека Python 3.12; без сети и LLM.

Механизм (меняется только детектор ФИО; остальные классы и unmask — rules_a1.py, копия S02):
1. rules_a1.find_spans — структурные классы и ФИО по жёстким шаблонам (приоритет, как в S02).
2. Токен-теггер: логистическая регрессия над признаками tagfeat.py (форма/регистр, суффиксы 1–4, словарь личных
   имён names_ru, окно ±2, разделители, позиция в строке) с весами weights.json (обучены на train, см. README.md).
   Токены с p >= THRESHOLD, не покрытые спанами правил, добавляются как ФИО; соседние токены через пробел/точку/дефис
   в одной строке сливаются в один спан.
Если теггер недоступен (нет weights.json, ошибка), в stderr выводится предупреждение и ответ строится по S02.
"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import rules_a1  # noqa: E402
from tagfeat import doc_features, tokenize  # noqa: E402

THRESHOLD = 0.07
JOIN = frozenset(" .-")

_WEIGHTS: dict[str, float] | None = None


def weights() -> dict[str, float]:
    global _WEIGHTS
    if _WEIGHTS is None:
        _WEIGHTS = json.loads((HERE / "weights.json").read_text(encoding="utf-8"))["weights"]
    return _WEIGHTS


def tagger_spans(text: str, taken: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    w = weights()
    covered = [False] * len(text)
    for a, b, _ in taken:
        for i in range(a, b):
            covered[i] = True
    toks = tokenize(text)
    picked = []
    for t, feats in zip(toks, doc_features(toks), strict=True):
        z = sum(w.get(f, 0.0) for f in feats)
        p = 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))
        if p >= THRESHOLD and not any(covered[t.start:t.end]):
            picked.append(t)
    merged: list[list[int]] = []
    for t in picked:
        gap = text[merged[-1][1]:t.start] if merged else ""
        if merged and gap and all(c in JOIN for c in gap):
            merged[-1][1] = t.end
        else:
            merged.append([t.start, t.end])
    return [(a, b, "ФИО") for a, b in merged]


def mask(text: str) -> dict:
    spans = rules_a1.find_spans(text)
    try:
        spans = sorted(spans + tagger_spans(text, spans))
    except Exception as exc:  # неожиданное состояние: громко, но ответ остаётся валидным
        print(f"tagger-t1: теггер недоступен ({exc!r}), используется S02", file=sys.stderr)
    return rules_a1.render(text, spans)


def solve(task_input: str) -> str:
    task = json.loads(task_input)
    if task["task"] == "mask":
        return json.dumps(mask(task["text"]), ensure_ascii=False)
    return json.dumps(rules_a1.unmask(task["mapping"], task["reply"]), ensure_ascii=False)


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    print(json.dumps({"answer": solve(payload["input"])}, ensure_ascii=False))
