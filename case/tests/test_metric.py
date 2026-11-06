"""Тесты метрики, включая «жульнические» ответы, которые она обязана ловить (B-4.4).

Часть тестов идёт через настоящие score.py / aggregate.py подпроцессом — тем же контрактом, что у арены.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pii_metric import aggregate, load_policy, score_example

METRIC = Path(__file__).resolve().parents[1] / "metric"
POLICY = load_policy()

TEXT = "Договор с Ивановым Иваном Ивановичем, тел. +7 (912) 345-67-89, паспорт 4512 № 345678, ООО «Ромашка»."


def _span(sub: str, label: str, entity: int, start: int = 0) -> dict:
    a = TEXT.index(sub, start)
    return {"start": a, "end": a + len(sub), "label": label, "entity": entity}


SPANS = [_span("Ивановым Иваном Ивановичем", "ФИО", 0), _span("+7 (912) 345-67-89", "ТЕЛЕФОН", 1),
         _span("4512", "ПАСПОРТ", 2), _span("345678", "ПАСПОРТ", 2)]
IGNORE = [_span("ООО «Ромашка»", "ORG", 3)]
MASK_EX = {"id": "m1", "input": json.dumps({"task": "mask", "text": TEXT}, ensure_ascii=False),
           "reference": json.dumps({"task": "mask", "spans": SPANS, "ignore": IGNORE}, ensure_ascii=False)}

UNMASK_REF = {"task": "unmask", "segments": ["Уважаемому ", "! Звоните: ", "."],
              "golds": ["Иванову Ивану Ивановичу", "+7 (912) 345-67-89"], "labels": ["ФИО", "ТЕЛЕФОН"]}
UNMASK_EX = {"id": "u1",
             "input": json.dumps({"task": "unmask", "reply": "Уважаемому [ФИО_1]! Звоните: [телефон_1].",
                                  "mapping": {"[ФИО_1]": "Иванов Иван Иванович",
                                              "[ТЕЛЕФОН_1]": "+7 (912) 345-67-89"}}, ensure_ascii=False),
             "reference": json.dumps(UNMASK_REF, ensure_ascii=False)}


def mask_answer(subs: list[tuple[str, str]], text: str = TEXT) -> str:
    """Заменяет подстроки (по первому вхождению, слева направо) метками."""
    out, reps, pos, counters = [], [], 0, {}
    for sub, label in subs:
        a = text.index(sub, pos)
        counters[label] = counters.get(label, 0) + 1
        ph = f"[{label}_{counters[label]}]"
        out += [text[pos:a], ph]
        reps.append({"placeholder": ph, "original": sub})
        pos = a + len(sub)
    out.append(text[pos:])
    return json.dumps({"masked_text": "".join(out), "replacements": reps}, ensure_ascii=False)


ORACLE = [("Ивановым Иваном Ивановичем", "ФИО"), ("+7 (912) 345-67-89", "ТЕЛЕФОН"), ("4512", "ПАСПОРТ"),
          ("345678", "ПАСПОРТ")]


def st(answer: str, ex: dict = MASK_EX) -> dict:
    return score_example(ex, answer, POLICY)["statistics"]


def total(stats: list[dict]) -> float:
    return aggregate(stats, POLICY)["score"]


def run_script(name: str, payload: dict) -> dict:
    out = subprocess.run([sys.executable, "-I", str(METRIC / name)], input=json.dumps(payload).encode(),
                         capture_output=True, check=True, cwd="/").stdout
    return json.loads(out)


# --------------------------------------------------------------- корректные ответы

def test_oracle_gets_full_score_via_arena_scripts():
    r1 = run_script("score.py", {"example": MASK_EX, "answer": mask_answer(ORACLE)})
    r2 = run_script("score.py", {"example": UNMASK_EX, "answer": json.dumps(
        {"text": "Уважаемому Иванову Ивану Ивановичу! Звоните: +7 (912) 345-67-89."}, ensure_ascii=False)})
    agg = run_script("aggregate.py", {"results": [{"example_id": "m1", **r1}, {"example_id": "u1", **r2}]})
    assert agg["score"] == pytest.approx(1.0)


def test_ignore_zone_is_neither_required_nor_penalized():
    with_org = mask_answer(ORACLE + [("ООО «Ромашка»", "ФИО")])
    assert total([st(with_org)]) == pytest.approx(total([st(mask_answer(ORACLE))])) == pytest.approx(1.0)


def test_better_answer_scores_higher():
    partial = mask_answer([("Ивановым Иваном Ивановичем", "ФИО"), ("+7 (912) 345-67-89", "ТЕЛЕФОН")])
    assert total([st(partial)]) < total([st(mask_answer(ORACLE))])


def test_partial_entity_gets_at_most_half_credit():
    # замаскирована только фамилия: 1 из 3 токенов ФИО → кредит 0.5 * 1/3
    s = st(mask_answer([("Ивановым", "ФИО")] + ORACLE[1:]))
    assert s["per_class"]["ФИО"]["credit"] == pytest.approx(0.5 / 3)
    assert s["per_class"]["ФИО"]["strict"] == 0


def test_passport_series_and_number_are_one_entity():
    s = st(mask_answer(ORACLE))
    assert s["per_class"]["ПАСПОРТ"]["entities"] == 1


# --------------------------------------------------------------- «жульнические» и невалидные ответы

@pytest.mark.parametrize("answer", ["", "not json", "[]", json.dumps({"masked_text": TEXT}),
                                    json.dumps({"masked_text": 1, "replacements": []})])
def test_wrong_format_is_invalid_and_counts_as_nothing_masked(answer):
    s = st(answer)
    assert not s["valid"] and s["masked_tokens"] == 0
    assert total([s]) == 0.0


def test_dropping_text_breaks_round_trip():
    # «замаскировать» удалением: ПДн выкинуты, но обратная подстановка невозможна
    masked = TEXT.replace("Ивановым Иваном Ивановичем", "[ФИО_1]").replace("+7 (912) 345-67-89", "")
    ans = json.dumps({"masked_text": masked, "replacements": [
        {"placeholder": "[ФИО_1]", "original": "Ивановым Иваном Ивановичем"}]}, ensure_ascii=False)
    s = st(ans)
    assert not s["valid"] and "round trip" in s["error"]


def test_editing_non_pii_text_is_invalid():
    ans = json.loads(mask_answer(ORACLE))
    ans["masked_text"] = ans["masked_text"].replace("Договор", "Контракт")
    assert not st(json.dumps(ans, ensure_ascii=False))["valid"]


def test_placeholder_that_leaks_value_is_not_a_placeholder():
    # метка со значением внутри не распознаётся как метка → round trip ломается
    masked = TEXT.replace("Ивановым Иваном Ивановичем", "[ФИО_Иванов]")
    ans = json.dumps({"masked_text": masked, "replacements": []}, ensure_ascii=False)
    assert not st(ans)["valid"]


def test_value_encoded_in_placeholder_number_is_rejected():
    # номер метки как канал утечки (цифры телефона) — нумерация обязана быть 1..k
    masked = TEXT.replace("+7 (912) 345-67-89", "[ТЕЛЕФОН_9123]")
    ans = json.dumps({"masked_text": masked, "replacements": [
        {"placeholder": "[ТЕЛЕФОН_9123]", "original": "+7 (912) 345-67-89"}]}, ensure_ascii=False)
    s = st(ans)
    assert not s["valid"] and "numbered" in s["error"]


def test_replacements_out_of_order_are_invalid():
    ans = json.loads(mask_answer(ORACLE))
    ans["replacements"].reverse()
    assert not st(json.dumps(ans, ensure_ascii=False))["valid"]


def test_mask_everything_is_penalized_by_precision():
    subs = [(w, "ФИО") for w in re.findall(r"[^\W_]+", TEXT)]
    s = st(mask_answer(subs))
    assert s["valid"]
    res = aggregate([s], POLICY)["statistics"]
    assert res["recall_macro"] == pytest.approx(1.0)
    # recall 1, но precision 10/14 ниже порога 0.9 → квадратичный штраф (на train такая стратегия
    # получает 0.15, см. results/metric_sensitivity/strategies.md)
    assert res["precision"] == pytest.approx(10 / 14)
    assert total([s]) == pytest.approx((10 / 14 / 0.9) ** 2)


def test_missing_whole_class_zeroes_score():
    # гармоническое среднее: пропуск всего класса нельзя «добрать» остальными
    s = st(mask_answer(ORACLE[:2]))
    assert total([s]) == 0.0


def test_identity_answer_is_valid_but_zero():
    s = st(json.dumps({"masked_text": TEXT, "replacements": []}, ensure_ascii=False))
    assert s["valid"] and total([s]) == 0.0


# --------------------------------------------------------------- unmask

def ua(text: str) -> str:
    return json.dumps({"text": text}, ensure_ascii=False)


def test_unmask_plain_replacement_misses_inflection():
    s = st(ua("Уважаемому Иванов Иван Иванович! Звоните: +7 (912) 345-67-89."), UNMASK_EX)
    assert s["valid"] and s["correct"] == 1 and s["slots"] == 2


def test_unmask_yo_and_spacing_are_normalized():
    ex = json.loads(json.dumps(UNMASK_EX))
    ref = dict(UNMASK_REF, golds=["Фёдорову Ивану", "+7 (912) 345-67-89"])
    ex["reference"] = json.dumps(ref, ensure_ascii=False)
    s = st(ua("Уважаемому Федорову  Ивану! Звоните: +7 (912) 345-67-89."), ex)
    assert s["correct"] == 2


def test_unmask_changing_text_outside_slots_fails_alignment():
    s = st(ua("Дорогому Иванову Ивану Ивановичу! Звоните: +7 (912) 345-67-89."), UNMASK_EX)
    assert s["correct"] == 0 and not s["valid"]


def test_unmask_leaving_placeholders_scores_zero():
    s = st(ua("Уважаемому [ФИО_1]! Звоните: [телефон_1]."), UNMASK_EX)
    assert s["valid"] and s["correct"] == 0


def test_unmask_bad_json_is_invalid():
    assert not st("Уважаемому Иванову", UNMASK_EX)["valid"]


# --------------------------------------------------------------- агрегация

def test_aggregate_hand_computed():
    s_mask = st(mask_answer([("Ивановым", "ФИО")] + ORACLE[1:]))  # ФИО 0.5/3, остальные 1
    s_un = st(ua("Уважаемому Иванов Иван Иванович! Звоните: +7 (912) 345-67-89."), UNMASK_EX)  # A=0.5
    rec = [0.5 / 3, 1.0, 1.0]
    h = 3 / sum(1 / r for r in rec)
    assert total([s_mask, s_un]) == pytest.approx(h * 1.0 * 0.5)


def test_aggregate_requires_statistics_via_script():
    with pytest.raises(subprocess.CalledProcessError):
        run_script("aggregate.py", {"results": [{"example_id": "x", "score": 1.0}]})


# --------------------------------------------------------------- отдельные кампании (ADR-0006)

def test_aggregate_mask_only_is_recall_times_penalty():
    s = st(mask_answer([("Ивановым", "ФИО")] + ORACLE[1:]))
    rec = [0.5 / 3, 1.0, 1.0]
    res = aggregate([s], POLICY)
    assert res["statistics"]["restore_accuracy"] is None
    assert res["score"] == pytest.approx(3 / sum(1 / r for r in rec))


def test_aggregate_unmask_only_is_restore_accuracy():
    s = st(ua("Уважаемому Иванов Иван Иванович! Звоните: +7 (912) 345-67-89."), UNMASK_EX)
    res = aggregate([s], POLICY)
    assert res["statistics"]["recall_class_hmean"] is None
    assert res["score"] == pytest.approx(0.5)
