"""Инварианты данных и генератора. Тесты с пометкой operator требуют operator/ (вне Git) и пропускаются
в чистом клоне."""
import json
import random
import re
from pathlib import Path

import pytest
from generator import values as V
from generator.morph import Person, decline_first, decline_patronymic, decline_surname

ROOT = Path(__file__).resolve().parents[2]
TRAIN = ROOT / "case" / "dataset" / "train.jsonl"
OPERATOR = ROOT / "operator"
needs_operator = pytest.mark.skipif(not (OPERATOR / "data" / "public.jsonl").exists(),
                                    reason="нужны операторские данные (operator/ вне Git)")
TOKEN = re.compile(r"[^\W_]+")


def rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# --------------------------------------------------------------- морфология (вручную выписанные формы)

def test_declension_hand_checked():
    assert decline_surname("Иванов", "ov", "f")["ins"] == "Ивановой"
    assert decline_surname("Иванов", "ov", "m")["ins"] == "Ивановым"
    assert decline_surname("Высоцкий", "sky", "f")["acc"] == "Высоцкую"
    assert decline_surname("Мельник", "cons", "m")["dat"] == "Мельнику"
    assert decline_surname("Мельник", "cons", "f")["dat"] == "Мельник"
    assert decline_surname("Шевченко", "indecl", "m")["gen"] == "Шевченко"
    assert decline_first("Дмитрий", "m_iy")["prep"] == "Дмитрии"
    assert decline_first("Мария", "f_iya")["dat"] == "Марии"
    assert decline_first("Ольга", "f_a_velar")["gen"] == "Ольги"
    assert decline_first("Павел", "explicit")["gen"] == "Павла"
    assert decline_patronymic("Ивановна", "f")["ins"] == "Ивановной"
    p = Person("Петров", "ov", "Анна", "f_a", "Сергеевич", "Сергеевна", "f")
    assert p.form("full", "dat") == "Петровой Анне Сергеевне"
    assert p.form("short", "gen") == "Петровой А.С."


def test_checksums():
    rng = random.Random(1)
    for _ in range(200):
        assert V.luhn_valid(V.luhn_complete("2200", 16, rng))
        s = V.inn12(rng)
        assert len(s) == 12 and s.isdigit()
    assert V.snils_checksum("112233445") == "95"  # пример из описания алгоритма СНИЛС


# --------------------------------------------------------------- train.jsonl (в Git)

def test_train_rows_follow_arena_example_format():
    data = rows(TRAIN)
    assert len(data) == 1700
    for r in data:
        assert set(r) == {"id", "input", "reference"}
        assert isinstance(r["input"], str) and isinstance(r["reference"], str)
        assert len(r["input"]) <= 500_000


def test_gold_spans_align_with_tokens():
    for r in rows(TRAIN):
        inp, ref = json.loads(r["input"]), json.loads(r["reference"])
        if inp["task"] != "mask":
            continue
        text = inp["text"]
        for s in ref["spans"] + ref["ignore"]:
            a, b = s["start"], s["end"]
            assert text[a:b].strip() == text[a:b] and TOKEN.search(text[a:b])
            assert not (a > 0 and text[a - 1].isalnum() and text[a].isalnum())
            assert not (b < len(text) and text[b - 1].isalnum() and text[b].isalnum())


def test_unmask_reference_reassembles_reply_shape():
    for r in rows(TRAIN):
        inp, ref = json.loads(r["input"]), json.loads(r["reference"])
        if inp["task"] != "unmask":
            continue
        assert len(ref["segments"]) == len(ref["golds"]) + 1
        reply = inp["reply"]
        assert reply.startswith(ref["segments"][0]) and reply.endswith(ref["segments"][-1])


def test_every_class_present_in_train():
    seen = set()
    for r in rows(TRAIN):
        ref = json.loads(r["reference"])
        if ref["task"] == "mask":
            seen.update(s["label"] for s in ref["spans"])
    assert seen == set(V.PII_LABELS)


# --------------------------------------------------------------- граница split (только у оператора)

def _families(path: Path) -> set[str]:
    return {f for r in rows(path) for f in json.loads(r["reference"])["meta"]["families"]}


def _surnames(path: Path) -> set[str]:
    out = set()
    for r in rows(path):
        inp, ref = json.loads(r["input"]), json.loads(r["reference"])
        if inp["task"] == "mask":
            for s in ref["spans"]:
                if s["label"] == "ФИО":
                    out.update(w.lower() for w in TOKEN.findall(inp["text"][s["start"]:s["end"]]) if len(w) > 3)
    return out


@needs_operator
def test_families_do_not_leak_between_splits():
    tr, pu, pr = (_families(p) for p in (TRAIN, OPERATOR / "data" / "public.jsonl",
                                         OPERATOR / "data" / "private.jsonl"))
    assert not (tr & pu) and not (tr & pr) and not (pu & pr)


@needs_operator
def test_memorizing_train_values_does_not_transfer():
    """«Подгонка под известные экземпляры»: словарь фамилий train почти не покрывает private."""
    pools_dir = OPERATOR / "content"
    import importlib.util
    spec = importlib.util.spec_from_file_location("pools", pools_dir / "pools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    surnames = {s.lower() for s, _ in mod.SURNAMES}
    train_sur = _surnames(TRAIN) & surnames
    private_sur = _surnames(OPERATOR / "data" / "private.jsonl") & surnames
    assert private_sur and len(train_sur & private_sur) / len(private_sur) < 0.25


@needs_operator
def test_declension_matches_pymorphy():
    """Эталонное склонение генератора совпадает с pymorphy3 на всех именах, отчествах и фамилиях пулов
    (для слов, которые pymorphy3 знает как Name/Patr/Surn в именительном падеже нужного рода)."""
    import importlib.util

    import pymorphy3
    from generator.morph import CASES, surname_nom

    spec = importlib.util.spec_from_file_location("pools", OPERATOR / "content" / "pools.py")
    pools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pools)
    morph = pymorphy3.MorphAnalyzer()
    grams = {"nom": "nomn", "gen": "gent", "dat": "datv", "acc": "accs", "ins": "ablt", "prep": "loct"}

    words = [(n, decline_first(n, p), "Name", "m") for n, p in pools.FIRST_M]
    words += [(n, decline_first(n, p), "Name", "f") for n, p in pools.FIRST_F]
    for pm, pf in pools.PATRONYMICS:
        words += [(pm, decline_patronymic(pm, "m"), "Patr", "m"), (pf, decline_patronymic(pf, "f"), "Patr", "f")]
    for s, par in pools.SURNAMES:
        for g in "mf":
            if par == "indecl" or (par == "cons" and g == "f"):
                continue  # несклоняемые: все формы равны именительному по построению
            words.append((surname_nom(s, par, g), decline_surname(s, par, g), "Surn", g))

    checked, mismatches = 0, []
    for word, ours, tag, g in words:
        gender = "masc" if g == "m" else "femn"
        parse = next((p for p in morph.parse(word) if tag in p.tag and "nomn" in p.tag and gender in p.tag), None)
        if parse is None:
            continue  # pymorphy3 не знает слово — склоняется регулярными правилами генератора
        checked += 1
        for case in CASES:
            ref = parse.inflect({grams[case], gender}) or parse.inflect({grams[case]})
            if ref and ours[case].lower().replace("ё", "е") != ref.word.replace("ё", "е"):
                mismatches.append((word, case, ours[case], ref.word))
    print(f"checked {checked} of {len(words)} words × 6 cases")
    assert checked > 600 and not mismatches, mismatches[:10]
