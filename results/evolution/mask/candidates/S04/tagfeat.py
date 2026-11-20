"""Признаки токенов для обучаемого теггера ФИО (stdlib). Общий модуль для train_tagger.py и solve.py.

Токены совпадают с токенами метрики: `[^\\W_]+`. Признаки — форма и регистр, суффиксы (1–4 буквы), словарь личных имён
(names_ru, общие знания, не из train), окно ±2, разделители, позиция в строке. Идентичность слов (в т.ч. имён,
фамилий, служебных слов) в признаках не используется: модель не хранит значений ПДн и фраз train.
"""
import re

from names_ru import is_first_name

TOKEN_RE = re.compile(r"[^\W_]+")
PATR_RE = re.compile(r"(?:ович|евич|ьич|ич|овна|евна|ична|ьична|инична)(?:а|у|ем|е|ей|ы|и|ом)?$")


def shape(w: str) -> str:
    if w.isdigit():
        return "D"
    if any(c.isdigit() for c in w):
        return "X"
    if len(w) == 1:
        return "I" if w.isupper() else "i"  # одиночная буква: заглавная (инициал) / строчная
    if w.isupper():
        return "U"
    if w[0].isupper() and w[1:].islower():
        return "C"
    if w.islower():
        return "l"
    return "M"


def gap_class(gap: str) -> str:
    if "\n" in gap:
        return "NL"
    g = gap.replace(" ", "_")
    return g if len(g) <= 3 else "LONG"


class Tok:
    __slots__ = ("start", "end", "text", "low", "sh", "nm", "patr", "dot", "gb", "ga", "first", "last")

    def __init__(self, start: int, end: int, text: str):
        self.start, self.end, self.text = start, end, text
        self.low = text.lower()
        self.sh = shape(text)
        self.nm = is_first_name(text)
        self.patr = bool(PATR_RE.search(self.low)) and len(text) > 5
        self.dot = False
        self.gb = "BOS"
        self.ga = "EOS"
        self.first = False
        self.last = False


def tokenize(text: str) -> list[Tok]:
    toks = [Tok(m.start(), m.end(), m.group()) for m in TOKEN_RE.finditer(text)]
    for i, t in enumerate(toks):
        t.dot = text[t.end:t.end + 1] == "."
        if i > 0:
            t.gb = gap_class(text[toks[i - 1].end:t.start])
        if i + 1 < len(toks):
            t.ga = gap_class(text[t.end:toks[i + 1].start])
        t.first = i == 0 or "\n" in text[toks[i - 1].end:t.start]
        t.last = i + 1 == len(toks) or "\n" in text[t.end:toks[i + 1].start]
    return toks


def token_features(toks: list[Tok], i: int) -> list[str]:
    t = toks[i]
    f = ["b", "sh=" + t.sh, "len=" + str(min(len(t.text), 12))]
    low = t.low
    for k in (1, 2, 3, 4):
        if len(low) >= k:
            f.append(f"s{k}=" + low[-k:])
    if t.nm:
        f.append("nm")
        f.append("nm*sh=" + t.sh)
    if t.patr:
        f.append("patr")
    if t.dot:
        f.append("dot")
        f.append("dot*sh=" + t.sh)
    f += ["gb=" + t.gb, "ga=" + t.ga, f"sh*gb={t.sh}|{t.gb}", f"sh*ga={t.sh}|{t.ga}"]
    if t.first:
        f.append("first")
        f.append("first*sh=" + t.sh)
    if t.last:
        f.append("last")
    if len(low) >= 3:
        f.append(f"sh*s3={t.sh}|{low[-3:]}")
    # окно
    for d in (-2, -1, 1, 2):
        j = i + d
        if j < 0 or j >= len(toks):
            f.append(f"o{d}=PAD")
            continue
        n = toks[j]
        f.append(f"o{d}sh={n.sh}")
        if n.nm:
            f.append(f"o{d}nm")
        if n.patr:
            f.append(f"o{d}patr")
        if n.dot:
            f.append(f"o{d}dot")
        if abs(d) == 1:
            f.append(f"o{d}s3={n.low[-3:]}")
            f.append(f"o{d}s2={n.low[-2:]}")
            f.append(f"o{d}sh*sh={n.sh}|{t.sh}")
            f.append(f"o{d}gap=" + (t.gb if d < 0 else t.ga))
    # шаблон форм по строке: заглавные подряд слева/справа
    run_l = 0
    j = i - 1
    while j >= 0 and toks[j].sh in "CIU" and toks[j + 1].gb != "NL" and run_l < 3:
        run_l += 1
        j -= 1
    run_r = 0
    j = i + 1
    while j < len(toks) and toks[j].sh in "CIU" and toks[j].gb != "NL" and run_r < 3:
        run_r += 1
        j += 1
    f.append(f"runl={run_l}")
    f.append(f"runr={run_r}")
    f.append(f"run={run_l}|{run_r}|{t.sh}")
    return f


def doc_features(toks: list[Tok]) -> list[list[str]]:
    return [token_features(toks, i) for i in range(len(toks))]
