"""Инструмент ручной разметки: разбор [[КЛАСС|фрагмент]] в формат train. Данные вымышленные."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from annotate import parse  # noqa: E402

LABELS = {"ФИО", "ПАСПОРТ", "ТЕЛЕФОН"}


def test_parse_offsets_groups_and_ignore():
    ann = "Я, [[ФИО|Петрова А.С.]], паспорт [[ПАСПОРТ#1|45 12]] № [[ПАСПОРТ#1|345678]], [[~ORG|ООО «Ромашка»]]."
    plain, spans, ignore = parse(ann, LABELS)
    assert plain == "Я, Петрова А.С., паспорт 45 12 № 345678, ООО «Ромашка»."
    assert [plain[s["start"]:s["end"]] for s in spans] == ["Петрова А.С.", "45 12", "345678"]
    assert spans[1]["entity"] == spans[2]["entity"] == 1 and spans[0]["entity"] != 1
    assert plain[ignore[0]["start"]:ignore[0]["end"]] == "ООО «Ромашка»"


@pytest.mark.parametrize("ann", ["[[СНИЛС|123]]", "[[ФИО|Петрова]]ой", "[[ФИО| Петрова]]", "[[ФИО|Петрова]"])
def test_parse_rejects_bad_markup(ann):
    with pytest.raises(ValueError):
        parse(ann, LABELS)


def test_pre_and_build_roundtrip(tmp_path):
    (tmp_path / "doc1.txt").write_text("Звоните Анне Сергеевне по тел. +7 (912) 345-67-89.", encoding="utf-8")
    run = [sys.executable, str(ROOT / "tools" / "annotate.py")]
    subprocess.run(run + ["pre", "--docs", str(tmp_path)], check=True, capture_output=True)
    ann = (tmp_path / "doc1.ann.txt").read_text(encoding="utf-8")
    assert "[[ТЕЛЕФОН|+7 (912) 345-67-89]]" in ann and "[[ФИО" not in ann  # имя baseline пропускает
    (tmp_path / "doc1.ann.txt").write_text(ann.replace("Анне Сергеевне", "[[ФИО|Анне Сергеевне]]"), encoding="utf-8")
    out = tmp_path / "real.jsonl"
    subprocess.run(run + ["build", "--docs", str(tmp_path), "--out", str(out)], check=True, capture_output=True)
    ref = json.loads(json.loads(out.read_text(encoding="utf-8"))["reference"])
    assert sorted(s["label"] for s in ref["spans"]) == ["ТЕЛЕФОН", "ФИО"]
