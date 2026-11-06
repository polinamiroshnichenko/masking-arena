"""Генерация split'ов кейса. Один код и одни параметры для всех split; различаются только семейства
шаблонов и значения открытых классов (фамилии, улицы), разбитые по split секретным сидом оператора.

    uv run python case/dataset/generator/generate.py --split train \
        --content operator/content --secret operator/secret.json --out case/dataset/train.jsonl

Содержимое шаблонов и пулов (`--content`) и `secret.json` хранит оператор вне Git до финализации
кампании (ADR-0004): иначе исследователь увидит формулировки public/private.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generator import render  # noqa: E402
from generator import values as V  # noqa: E402

SPLITS = ("train", "public", "private")
FAMILY_PATTERN = ("train", "public", "private", "train")  # стратифицированно 2:1:1 внутри группы
POOL_SHARES = (("train", 0.5), ("public", 0.75), ("private", 1.0))


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def partition_families(families: dict[str, list[str]], doc_type: str, secret: str
                       ) -> dict[str, dict[str, list[tuple[str, str]]]]:
    """{split: {pos: [(family_id, template)]}} — каждое семейство ровно в одном split."""
    out: dict[str, dict[str, list[tuple[str, str]]]] = {s: {} for s in SPLITS}
    for pos, templates in families.items():
        items = [(f"{doc_type}.{pos}.{i:02d}", t) for i, t in enumerate(templates)]
        if len(items) < 3:
            raise ValueError(f"{doc_type}.{pos}: нужно ≥3 семейств, чтобы каждый split получил своё")
        random.Random(f"{secret}|{doc_type}|{pos}").shuffle(items)
        for i, item in enumerate(items):
            out[FAMILY_PATTERN[i % len(FAMILY_PATTERN)]].setdefault(pos, []).append(item)
    return out


SLOT_CLASS_RE = re.compile(r"\{P\d\.(\w+)")
MIN_FAMILIES_PER_CLASS = 2


def classes_in(template: str) -> set[str]:
    return {render.PERSON_TAGS[a] for a in SLOT_CLASS_RE.findall(template) if a in render.PERSON_TAGS}


def partition_all(doc_types: dict, secret: str, min_per_class: int = MIN_FAMILIES_PER_CLASS,
                  max_attempts: int = 20000) -> tuple[dict, int, dict]:
    """Случайное стратифицированное разбиение семейств всех типов документов с ограничением покрытия:
    в каждом split каждый класс ПДн встречается хотя бы в MIN_FAMILIES_PER_CLASS семействах.
    Перебираются сиды secret|0, secret|1, ... — берётся первый допустимый (детерминированно)."""
    for attempt in range(max_attempts):
        parts = {t: partition_families(spec["families"], t, f"{secret}|{attempt}")
                 for t, spec in doc_types.items()}
        cover = {split: Counter() for split in SPLITS}
        for by_split in parts.values():
            for split, by_pos in by_split.items():
                for items in by_pos.values():
                    for _, tpl in items:
                        cover[split].update(classes_in(tpl))
        if all(cover[s][c] >= min_per_class for s in SPLITS for c in V.PII_LABELS):
            return parts, attempt, {s: dict(c) for s, c in cover.items()}
    raise RuntimeError("не удалось разбить семейства с покрытием всех классов — добавьте семейства")


def partition_pool(items: list, name: str, secret: str, split: str) -> list:
    """Открытые классы значений делятся между split; закрытые (имена, города) общие."""
    chosen = []
    for item in items:
        key = item[0] if isinstance(item, tuple) else item
        r = random.Random(f"{secret}|{name}|{key}").random()
        bucket = next(s for s, edge in POOL_SHARES if r < edge)
        if bucket == split:
            chosen.append(item)
    return chosen


class Pools:
    def __init__(self, mod, secret: str, split: str):
        self.surnames = partition_pool(mod.SURNAMES, "surnames", secret, split)
        self.streets = partition_pool(mod.STREETS, "streets", secret, split)
        self.org_names = partition_pool(mod.ORG_NAMES, "orgs", secret, split)
        self.first_m = mod.FIRST_M
        self.first_f = mod.FIRST_F
        self.patronymics = mod.PATRONYMICS
        self.localities = mod.LOCALITIES
        self.email_domains = mod.EMAIL_DOMAINS
        self.city_codes = mod.CITY_CODES
        self.pass_issuers = mod.PASS_ISSUERS
        self.positions = mod.POSITIONS
        self.banks = mod.BANKS


def generate(split: str, n_mask: int, n_unmask: int, content: Path, secret: dict) -> list[dict]:
    fam_mod = load_module(content / "families.py")
    pools = Pools(load_module(content / "pools.py"), secret["partition_seed"], split)
    rng = random.Random(f"{secret['split_seeds'][split]}|{split}")
    doc_types = fam_mod.DOC_TYPES
    all_parts, attempt, cover = partition_all(doc_types, secret["partition_seed"])
    parts = {t: by_split[split] for t, by_split in all_parts.items()}
    print(json.dumps({"partition_attempt": attempt, "families_per_class": cover[split]},
                     ensure_ascii=False), file=sys.stderr)
    reply_all, reply_attempt, reply_cover = partition_all({"reply": fam_mod.REPLY}, secret["partition_seed"],
                                                          min_per_class=1)
    reply_parts = reply_all["reply"][split]
    print(json.dumps({"reply_partition_attempt": reply_attempt, "reply_families_per_class": reply_cover[split]},
                     ensure_ascii=False), file=sys.stderr)
    names = list(doc_types)
    weights = [doc_types[t].get("weight", 1.0) for t in names]
    rows = []
    for i in range(n_mask):
        t = rng.choices(names, weights=weights)[0]
        ex = render.build_mask_example(t, doc_types[t], parts[t], rng, pools)
        rows.append({"id": f"{split}-m-{i:05d}", **ex})
    for i in range(n_unmask):
        ex = render.build_unmask_example(fam_mod.REPLY, reply_parts, rng, pools)
        rows.append({"id": f"{split}-u-{i:05d}", **ex})
    return rows


def stats(rows: list[dict]) -> dict:
    labels: Counter = Counter()
    docs: Counter = Counter()
    slots: Counter = Counter()
    chars = 0
    for r in rows:
        ref = r["reference"]
        docs[ref["meta"]["doc_type"]] += 1
        if ref["task"] == "mask":
            chars += len(r["input"]["text"])
            labels.update(s["label"] for s in ref["spans"])
        else:
            slots.update(ref["labels"])
    n_mask = sum(1 for r in rows if r["reference"]["task"] == "mask")
    return {"examples": len(rows), "mask": n_mask, "unmask": len(rows) - n_mask,
            "avg_chars_mask": round(chars / max(1, n_mask)), "doc_types": dict(docs),
            "entities": dict(labels), "unmask_slots": dict(slots)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--split", choices=SPLITS, required=True)
    p.add_argument("--n-mask", type=int, required=True)
    p.add_argument("--n-unmask", type=int, required=True)
    p.add_argument("--content", type=Path, default=Path("operator/content"))
    p.add_argument("--secret", type=Path, default=Path("operator/secret.json"))
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    secret = json.loads(a.secret.read_text())
    rows = generate(a.split, a.n_mask, a.n_unmask, a.content, secret)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"id": r["id"],
                                "input": json.dumps(r["input"], ensure_ascii=False),
                                "reference": json.dumps(r["reference"], ensure_ascii=False)},
                               ensure_ascii=False) + "\n")
    print(json.dumps(stats(rows), ensure_ascii=False, indent=1), file=sys.stderr)


if __name__ == "__main__":
    main()
