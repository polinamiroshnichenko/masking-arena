"""Ядро метрики кейса AR-05 (маскирование ПДн). Без внешних зависимостей.

Два типа примеров (поле "task" во входе):

mask   — вход {"task":"mask","text":...}; ответ — JSON
         {"masked_text": str, "replacements": [{"placeholder": "[ФИО_1]", "original": "..."}, ...]}
         `replacements` перечисляет КАЖДОЕ вхождение метки в masked_text в порядке появления.
         Ответ валиден, только если подстановка originals вместо меток восстанавливает исходный
         текст символ в символ (точный round-trip) и метки пронумерованы 1..k внутри каждого класса.
         Невалидный ответ = «ничего не замаскировано» для всех сущностей примера.

unmask — вход {"task":"unmask","mapping":{метка: каноническое значение},"reply": str};
         ответ — JSON {"text": str}. Метки в reply могут стоять в косвенном падеже и быть искажены
         моделью ([фио_1], ФИО_1, [ФИО 1]); нужно вернуть значение в правильной грамматической форме.
         Вне меток текст ответа должен совпадать с reply.

Обоснование правил — docs/adr/0003-metric.md.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

TOKEN_RE = re.compile(r"[^\W_]+")
POLICY_PATH = Path(__file__).resolve().parent / "policy.json"


class Invalid(Exception):
    pass


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def placeholder_re(labels: list[str]) -> re.Pattern:
    alts = "|".join(re.escape(lbl) for lbl in sorted(labels, key=len, reverse=True))
    return re.compile(r"\[(" + alts + r")_([1-9][0-9]{0,3})\]")


def tokens(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in TOKEN_RE.finditer(text)]


def phi(precision: float, policy: dict) -> float:
    """Штраф за ложные срабатывания: 1 при P ≥ floor, далее (P/floor)^power."""
    return min(1.0, precision / policy["precision_floor"]) ** policy["precision_penalty_power"]


# ------------------------------------------------------------------ mask

def reconstruct(text: str, answer: str, policy: dict) -> list[tuple[int, int, str]]:
    """Проверяет ответ mask и возвращает замаскированные интервалы исходного текста."""
    try:
        obj = json.loads(answer)
    except (json.JSONDecodeError, TypeError) as e:
        raise Invalid(f"answer is not JSON: {e}") from None
    if not isinstance(obj, dict):
        raise Invalid("answer must be a JSON object")
    masked, reps = obj.get("masked_text"), obj.get("replacements")
    if not isinstance(masked, str) or not isinstance(reps, list):
        raise Invalid("need masked_text: str and replacements: list")
    ph_re = placeholder_re(policy["labels"])
    found = list(ph_re.finditer(masked))
    if len(found) != len(reps):
        raise Invalid(f"{len(found)} placeholders in masked_text, {len(reps)} replacements")
    out, intervals = [], []
    pos = cur = 0
    used: dict[str, set[int]] = {}
    for m, rep in zip(found, reps, strict=True):
        if not isinstance(rep, dict) or rep.get("placeholder") != m.group(0):
            raise Invalid("replacements must list placeholders in order of appearance")
        orig = rep.get("original")
        if not isinstance(orig, str) or not orig:
            raise Invalid("original must be a non-empty string")
        idx = int(m.group(2))
        if idx > policy["max_placeholder_index"]:
            raise Invalid("placeholder index too large")
        used.setdefault(m.group(1), set()).add(idx)
        out.append(masked[pos:m.start()])
        cur += m.start() - pos
        intervals.append((cur, cur + len(orig), m.group(1)))
        out.append(orig)
        cur += len(orig)
        pos = m.end()
    out.append(masked[pos:])
    if "".join(out) != text:
        raise Invalid("round trip failed: restored text differs from input")
    for label, idxs in used.items():
        if idxs != set(range(1, len(idxs) + 1)):
            raise Invalid(f"placeholders of {label} must be numbered 1..k without gaps")
    return intervals


def score_mask(text: str, reference: dict, answer: str, policy: dict) -> dict:
    labels = policy["labels"]
    error = None
    try:
        intervals = reconstruct(text, answer, policy)
    except Invalid as e:
        intervals, error = [], str(e)

    char_label: list[str | None] = [None] * len(text)
    for a, b, lbl in intervals:
        for i in range(a, b):
            char_label[i] = lbl

    toks = tokens(text)

    def tok_masked(t: tuple[int, int]) -> bool:
        return all(char_label[i] is not None for i in range(t[0], t[1]))

    def inside(t: tuple[int, int], spans: list[dict]) -> bool:
        return any(s["start"] <= t[0] and t[1] <= s["end"] for s in spans)

    gold_spans = reference["spans"]
    ignore_spans = reference.get("ignore", [])
    entities: dict = {}
    for i, s in enumerate(gold_spans):
        entities.setdefault(s.get("entity", f"s{i}"), []).append(s)

    per_class = {lbl: {"entities": 0, "credit": 0.0, "strict": 0, "typed": 0} for lbl in labels}
    for spans in entities.values():
        label = spans[0]["label"]
        ent_toks = [t for t in toks if inside(t, spans)]
        if not ent_toks:
            continue
        n_masked = sum(tok_masked(t) for t in ent_toks)
        cov = n_masked / len(ent_toks)
        rec = per_class[label]
        rec["entities"] += 1
        rec["credit"] += 1.0 if cov == 1.0 else policy["partial_credit"] * cov
        if cov == 1.0:
            rec["strict"] += 1
            if all(char_label[i] == label for t in ent_toks for i in range(t[0], t[1])):
                rec["typed"] += 1

    masked_counted = masked_pii = 0
    for t in toks:
        if not tok_masked(t):
            continue
        in_gold = inside(t, gold_spans)
        if not in_gold and inside(t, ignore_spans):
            continue
        masked_counted += 1
        masked_pii += in_gold

    return {"task": "mask", "valid": error is None, "error": error, "per_class": per_class,
            "masked_tokens": masked_counted, "masked_pii_tokens": masked_pii, "n_tokens": len(toks)}


# ------------------------------------------------------------------ unmask

def _norm(s: str) -> str:
    return " ".join(s.replace("ё", "е").replace("Ё", "Е").split())


def _occurrences(text: str, seg: str, start: int, cap: int = 64) -> list[int]:
    res, i = [], text.find(seg, start)
    while i != -1 and len(res) < cap:
        res.append(i)
        i = text.find(seg, i + 1)
    return res


def align_slots(text: str, segments: list[str], golds: list[str]) -> list[bool] | None:
    """Разбор text = s0 x1 s1 ... xk sk с максимумом верных слотов (DP по вхождениям сегментов)."""
    k = len(golds)
    if not text.startswith(segments[0]):
        return None
    states: dict[int, list[bool]] = {len(segments[0]): []}
    for i in range(1, k + 1):
        seg, last = segments[i], i == k
        nxt: dict[int, list[bool]] = {}
        for p, path in states.items():
            if last:
                q = len(text) - len(seg)
                cands = [q] if q >= p and text.endswith(seg) else []
            else:
                cands = _occurrences(text, seg, p)
            for q in cands:
                new = path + [_norm(text[p:q]) == _norm(golds[i - 1])]
                end = q + len(seg)
                if end not in nxt or sum(new) > sum(nxt[end]):
                    nxt[end] = new
        states = nxt
        if not states:
            return None
    if k == 0:
        return [] if text == segments[0] else None
    return max(states.values(), key=sum)


def score_unmask(reference: dict, answer: str) -> dict:
    segments, golds, labels = reference["segments"], reference["golds"], reference["labels"]
    per_label: dict[str, list[int]] = {}
    for lbl in labels:
        per_label.setdefault(lbl, [0, 0])[0] += 1
    error, flags = None, None
    try:
        obj = json.loads(answer)
        if not isinstance(obj, dict) or not isinstance(obj.get("text"), str):
            raise Invalid("need {\"text\": str}")
        text = obj["text"]
        gold_full = segments[0] + "".join(g + s for g, s in zip(golds, segments[1:], strict=True))
        flags = [True] * len(golds) if text == gold_full else align_slots(text, segments, golds)
        if flags is None:
            error = "text outside placeholders was changed; cannot align"
    except (json.JSONDecodeError, TypeError, Invalid) as e:
        error = f"invalid answer: {e}"
    flags = flags or [False] * len(golds)
    for lbl, ok in zip(labels, flags, strict=True):
        per_label[lbl][1] += int(ok)
    return {"task": "unmask", "valid": error is None, "error": error, "slots": len(golds),
            "correct": sum(flags), "per_label": per_label}


# ------------------------------------------------------------------ пример и набор

def score_example(example: dict, answer: str, policy: dict) -> dict:
    inp = json.loads(example["input"])
    ref = json.loads(example["reference"])
    if inp["task"] != ref["task"]:
        raise ValueError("input/reference task mismatch")
    if inp["task"] == "mask":
        st = score_mask(inp["text"], ref, answer, policy)
        ents = sum(c["entities"] for c in st["per_class"].values())
        rec = sum(c["credit"] for c in st["per_class"].values()) / ents if ents else 1.0
        prec = st["masked_pii_tokens"] / st["masked_tokens"] if st["masked_tokens"] else 1.0
        return {"score": rec * phi(prec, policy), "statistics": st}
    if inp["task"] == "unmask":
        st = score_unmask(ref, answer)
        return {"score": st["correct"] / st["slots"] if st["slots"] else 1.0, "statistics": st}
    raise ValueError(f"unknown task {inp['task']!r}")


def class_mean(values: list[float]) -> float:
    """Гармоническое среднее recall по классам: провал любого класса тянет итог к нулю."""
    if any(v <= 0 for v in values):
        return 0.0
    return len(values) / sum(1 / v for v in values)


def aggregate(stats: list[dict], policy: dict) -> dict:
    """Итог набора: score = H(recall_c) · φ(P) · A, где H — гармоническое среднее по классам.

    Компонент отсутствующего типа примеров (нет mask или нет unmask) опускается.
    """
    labels = policy["labels"]
    cls = {lbl: {"entities": 0, "credit": 0.0, "strict": 0, "typed": 0} for lbl in labels}
    masked = masked_pii = n_mask = n_unmask = invalid_mask = invalid_unmask = slots = correct = 0
    slot_labels: dict[str, list[int]] = {}
    for st in stats:
        if st["task"] == "mask":
            n_mask += 1
            invalid_mask += not st["valid"]
            masked += st["masked_tokens"]
            masked_pii += st["masked_pii_tokens"]
            for lbl, c in st["per_class"].items():
                for key in cls[lbl]:
                    cls[lbl][key] += c[key]
        elif st["task"] == "unmask":
            n_unmask += 1
            invalid_unmask += not st["valid"]
            slots += st["slots"]
            correct += st["correct"]
            for lbl, (n, k) in st["per_label"].items():
                acc = slot_labels.setdefault(lbl, [0, 0])
                acc[0] += n
                acc[1] += k
        else:
            raise ValueError("unknown task in statistics")
    recall = {lbl: c["credit"] / c["entities"] for lbl, c in cls.items() if c["entities"]}
    strict = {lbl: c["strict"] / c["entities"] for lbl, c in cls.items() if c["entities"]}
    typed = {lbl: c["typed"] / c["entities"] for lbl, c in cls.items() if c["entities"]}
    r_macro = sum(recall.values()) / len(recall) if recall else None
    r_class = class_mean(list(recall.values())) if recall else None
    precision = masked_pii / masked if masked else 1.0
    restore = correct / slots if slots else None
    components = []
    if n_mask:
        components.append(r_class * phi(precision, policy))
    if n_unmask:
        components.append(restore)
    if not components:
        raise ValueError("empty result set")
    score = math.prod(components)
    return {
        "score": score,
        "statistics": {
            "recall_class_hmean": r_class, "recall_macro": r_macro, "precision": precision,
            "precision_penalty": phi(precision, policy),
            "restore_accuracy": restore, "recall_by_class": recall, "strict_recall_by_class": strict,
            "typed_recall_by_class": typed,
            "restore_by_label": {lbl: k / n for lbl, (n, k) in slot_labels.items() if n},
            "n_mask": n_mask, "n_unmask": n_unmask, "invalid_mask": invalid_mask,
            "invalid_unmask": invalid_unmask, "masked_tokens": masked, "masked_pii_tokens": masked_pii,
        },
    }
