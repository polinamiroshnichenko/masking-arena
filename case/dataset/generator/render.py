"""Мини-язык шаблонов и сборка экземпляров mask / unmask.

Синтаксис шаблона:
  [a|b|c]                     — альтернатива (допускается вложенность и пустой вариант);
  {P1.fio:gen:short}          — ФИО персоны P1 в падеже gen в формате short;
  {P1.g:ый/ая}                — согласование по полу (мужской/женский вариант);
  {P1.phone} {P1.email} {P1.passport} {P1.passport_issue} {P1.snils} {P1.inn} {P1.card}
  {P1.account} {P1.birth[:style]} {P1.addr[:style]} {P1.pos}
  {ORG1.name|inn|ogrn|kpp|rs|ks|bik|bank|addr|phone|email} — реквизиты организации (нейтральная зона);
  {DATE} {DOCNO} {SUM} {ORDERNO} {TRACK} {TIME} {AREA} {CITY} {LAW} {EAN} {PERCENT} — дистракторы.
"""
from __future__ import annotations

import itertools
import random
import re
from dataclasses import dataclass

from . import values as V
from .morph import CASES

ALT_RE = re.compile(r"\[([^\[\]]*)\]")
SLOT_RE = re.compile(r"\{([^{}]+)\}")
TOKEN_RE = re.compile(r"[^\W_]+")

FIO_FORMATS = {
    "any": {"full": 4, "short": 2.5, "io_short": 1, "name_patr": 1, "full_io": 0.5, "name_surn": 1},
    "formal": {"full": 5.5, "short": 3.5, "io_short": 1},
    "informal": {"name": 4, "name_surn": 3, "name_patr": 3},
}
PERSON_TAGS = {"phone": "ТЕЛЕФОН", "email": "EMAIL", "passport": "ПАСПОРТ", "snils": "СНИЛС",
               "inn": "ИНН", "card": "КАРТА", "account": "СЧЕТ", "birth": "ДАТА_РОЖДЕНИЯ",
               "addr": "АДРЕС", "fio": "ФИО"}


def expand_alternations(template: str, rng: random.Random) -> str:
    while True:
        m = ALT_RE.search(template)
        if not m:
            return template
        choice = rng.choice(m.group(1).split("|"))
        template = template[:m.start()] + choice + template[m.end():]


@dataclass
class Cast:
    persons: dict[str, V.PersonData]
    orgs: dict[str, V.OrgData]


def make_cast(rng: random.Random, pools, n_persons: int = 3, n_orgs: int = 2) -> Cast:
    return Cast(persons={f"P{i + 1}": V.make_person(rng, pools) for i in range(n_persons)},
                orgs={f"ORG{i + 1}": V.make_org(rng, pools) for i in range(n_orgs)})


# ------------------------------------------------------------------ режим документа (mask)

def render_doc_slot(slot: str, cast: Cast, rng: random.Random, pools, style: str) -> list[V.Piece]:
    head, *args = slot.split(":", 1)
    arg = args[0] if args else ""
    if "." not in head:
        return [(V.distractor(rng, head, pools), None)]
    ref, attr = head.split(".", 1)
    if ref.startswith("ORG"):
        o = cast.orgs[ref]
        if attr == "addr":
            text = o.address.render(rng, office=True)
        else:
            text = getattr(o, attr)
        return [(text, "~ORG")]
    p = cast.persons[ref]
    if attr == "g":
        male, female = arg.split("/")
        return [(male if p.gender == "m" else female, None)]
    if attr == "fio":
        parts = arg.split(":") if arg else []
        case = parts[0] if parts and parts[0] in CASES else "nom"
        fmt = parts[1] if len(parts) > 1 else ("informal" if style == "chat" else "any")
        if fmt in FIO_FORMATS:
            fmt = V.pick(rng, FIO_FORMATS[fmt])
        return [(p.person.form(fmt, case), "ФИО")]
    if attr == "pos":
        return [(p.position, None)]
    if attr == "phone":
        return V.r_phone(rng, p.phone10, arg or ("eight_compact" if style == "chat" and rng.random() < .5 else None))
    if attr == "email":
        return [(p.email, "EMAIL")]
    if attr == "passport":
        return V.r_passport(rng, p)
    if attr == "passport_issue":
        return V.r_passport_issue(rng, p)
    if attr == "snils":
        return V.r_snils(rng, p.snils)
    if attr == "inn":
        return V.r_inn(rng, p.inn)
    if attr == "card":
        return V.r_card(rng, p.card)
    if attr == "account":
        return V.r_account(rng, p.account)
    if attr == "birth":
        return V.r_birth(rng, p.birth, arg or None)
    if attr == "addr":
        return [(p.address.render(rng, arg or ("chat" if style == "chat" and rng.random() < .5 else None)),
                 "АДРЕС")]
    raise KeyError(f"unknown slot {slot!r}")


def render_doc_template(template: str, cast: Cast, rng: random.Random, pools, style: str,
                        entity_ids: itertools.count) -> list[tuple[str, str | None, int | None]]:
    """Куски (text, tag, entity): все спаны одного слота (серия и номер паспорта) — одна сущность."""
    text = expand_alternations(template, rng)
    pieces: list[tuple[str, str | None, int | None]] = []
    pos = 0
    for m in SLOT_RE.finditer(text):
        if m.start() > pos:
            pieces.append((text[pos:m.start()], None, None))
        eid = next(entity_ids)
        pieces.extend((t, tag, eid if tag else None)
                      for t, tag in render_doc_slot(m.group(1), cast, rng, pools, style))
        pos = m.end()
    if pos < len(text):
        pieces.append((text[pos:], None, None))
    return pieces


def assemble(pieces: list[tuple[str, str | None, int | None]]) -> tuple[str, list[dict], list[dict]]:
    out, spans, ignore = [], [], []
    offset = 0
    for text, tag, eid in pieces:
        if tag is not None and text:
            rec = {"start": offset, "end": offset + len(text), "label": tag.lstrip("~"), "entity": eid}
            (ignore if tag.startswith("~") else spans).append(rec)
        out.append(text)
        offset += len(text)
    return "".join(out), spans, ignore


def check_boundaries(text: str, spans: list[dict]) -> None:
    """Спаны не должны резать токены — иначе токенная метрика станет неоднозначной."""
    for s in spans:
        a, b = s["start"], s["end"]
        if a > 0 and text[a - 1].isalnum() and text[a].isalnum():
            raise ValueError(f"span cuts token at start: {text[max(0, a - 10):b]!r}")
        if b < len(text) and text[b - 1].isalnum() and text[b].isalnum():
            raise ValueError(f"span cuts token at end: {text[a:b + 10]!r}")


def build_mask_example(doc_type: str, spec: dict, families: dict[str, list[tuple[str, str]]],
                       rng: random.Random, pools) -> dict:
    cast = make_cast(rng, pools)
    style = spec.get("style", "doc")
    chosen: list[tuple[str, str]] = []
    chosen.append(rng.choice(families["head"]))
    lo, hi = spec["n_body"]
    body = families["body"]
    k = min(len(body), rng.randint(lo, hi))
    chosen.extend(rng.sample(body, k))
    chosen.append(rng.choice(families["tail"]))
    pieces: list[tuple[str, str | None, int | None]] = []
    joiner = spec.get("joiner", "\n")
    entity_ids = itertools.count()
    for i, (_, tpl) in enumerate(chosen):
        if i:
            pieces.append((joiner, None, None))
        pieces.extend(render_doc_template(tpl, cast, rng, pools, style, entity_ids))
    text, spans, ignore = assemble(pieces)
    if rng.random() < spec.get("lower_prob", 0.0):
        low = text.lower()
        if len(low) != len(text):
            raise ValueError("lowercasing changed length")
        text = low
    check_boundaries(text, spans)
    check_boundaries(text, ignore)
    return {
        "input": {"task": "mask", "text": text},
        "reference": {"task": "mask", "spans": spans, "ignore": ignore,
                      "meta": {"doc_type": doc_type, "families": [fid for fid, _ in chosen]}},
    }


# ------------------------------------------------------------------ режим ответа модели (unmask)

def _canonical(p: V.PersonData, attr: str, rng: random.Random) -> str:
    if attr == "fio":
        fmts = [f for f in ("full", "short", "io_short") if p.person.restorable_from(f)]
        weights = {"full": 7, "short": 2, "io_short": 1}
        return V.pick(rng, {f: weights[f] for f in fmts})
    return ""


def perturb_placeholder(label: str, num: int, rng: random.Random) -> str:
    kind = V.pick(rng, {"exact": 78, "nobr": 8, "lower": 7, "space": 7})
    if kind == "nobr":
        return f"{label}_{num}"
    if kind == "lower":
        return f"[{label.lower()}_{num}]"
    if kind == "space":
        return f"[{label.replace('_', ' ')} {num}]"
    return f"[{label}_{num}]"


def person_value_text(p: V.PersonData, attr: str, rng: random.Random) -> str:
    """Каноническое (как его сохранил бы маскировщик) значение не-ФИО атрибута."""
    pieces = render_doc_slot(f"X.{attr}", Cast({"X": p}, {}), rng, None, "doc")
    return "".join(t for t, _ in pieces)


def build_unmask_example(spec: dict, families: dict[str, list[tuple[str, str]]], rng: random.Random,
                         pools) -> dict:
    cast = make_cast(rng, pools)
    chosen = [rng.choice(families["head"])]
    lo, hi = spec["n_body"]
    chosen.extend(rng.sample(families["body"], min(len(families["body"]), rng.randint(lo, hi))))
    chosen.append(rng.choice(families["tail"]))
    text = spec.get("joiner", "\n").join(expand_alternations(t, rng) for _, t in chosen)

    keys: list[str] = []
    for m in SLOT_RE.finditer(text):
        head = m.group(1).split(":", 1)[0]
        ref, _, attr = head.partition(".")
        if ref in cast.persons and attr in PERSON_TAGS and head not in keys:
            keys.append(head)
    # лишние записи словаря: маскировщик видел больше сущностей, чем упомянуто в ответе
    extra_pool = [f"P{i}.{a}" for i in (1, 2, 3) for a in ("phone", "email", "fio", "addr", "snils")]
    extras = [k for k in rng.sample(extra_pool, rng.randint(0, 3)) if k not in keys]
    all_keys = keys + extras
    rng.shuffle(all_keys)
    counters: dict[str, int] = {}
    numbering: dict[str, tuple[str, int]] = {}
    canon: dict[str, str] = {}
    fio_fmt: dict[str, str] = {}
    for key in all_keys:
        ref, attr = key.split(".")
        label = PERSON_TAGS[attr]
        counters[label] = counters.get(label, 0) + 1
        numbering[key] = (label, counters[label])
        p = cast.persons[ref]
        if attr == "fio":
            fio_fmt[key] = _canonical(p, attr, rng)
            canon[key] = p.person.form(fio_fmt[key], "nom")
        else:
            canon[key] = person_value_text(p, attr, rng)

    segments, golds, labels, shown = [""], [], [], []
    pos = 0
    for m in SLOT_RE.finditer(text):
        segments[-1] += text[pos:m.start()]
        pos = m.end()
        slot = m.group(1)
        head, _, arg = slot.partition(":")
        ref, _, attr = head.partition(".")
        if ref in cast.persons and attr == "g":
            male, female = arg.split("/")
            segments[-1] += male if cast.persons[ref].gender == "m" else female
            continue
        if ref in cast.persons and attr in PERSON_TAGS:
            label, num = numbering[head]
            if attr == "fio":
                case = arg.split(":")[0] if arg.split(":")[0] in CASES else "nom"
                gold = cast.persons[ref].person.form(fio_fmt[head], case)
            else:
                gold = canon[head]
            ph = perturb_placeholder(label, num, rng)
            shown.append(ph)
            golds.append(gold)
            labels.append(label)
            segments.append("")
            continue
        segments[-1] += "".join(t for t, _ in render_doc_slot(slot, cast, rng, pools, "doc"))
    segments[-1] += text[pos:]

    if any(not seg for seg in segments[1:-1]):
        raise ValueError("adjacent placeholders without separator are ambiguous")
    reply = segments[0] + "".join(ph + seg for ph, seg in zip(shown, segments[1:], strict=True))
    mapping = {f"[{numbering[k][0]}_{numbering[k][1]}]": canon[k] for k in all_keys}
    mapping = dict(sorted(mapping.items()))
    return {
        "input": {"task": "unmask", "mapping": mapping, "reply": reply},
        "reference": {"task": "unmask", "segments": segments, "golds": golds, "labels": labels,
                      "meta": {"doc_type": "reply", "families": [fid for fid, _ in chosen]}},
    }


def tokens(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in TOKEN_RE.finditer(text)]
