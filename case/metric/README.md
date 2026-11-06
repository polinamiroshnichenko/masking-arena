# Пакет метрики `pii-mask-v1-draft`

Программная метрика без LLM (`kind: command`, `uses_model: false`), направление `maximize`, диапазон [0, 1]. Обоснование и сравнение с F1/F2 — [ADR-0003](../../docs/adr/0003-metric.md).

| Файл | Роль |
|---|---|
| `metric.json` | контракт арены: `command` = `score.py` на пример, `aggregation` = `aggregate.py` на набор |
| `score.py` | stdin `{"example", "answer"}` → `{"score", "statistics"}`; score примера — диагностика, итог считает агрегатор |
| `aggregate.py` | stdin `{"results": [...]}` → `{"score", "statistics"}` |
| `pii_metric.py` | общая логика: валидация round-trip, токенное покрытие, выравнивание unmask, агрегация |
| `policy.json` | замораживаемые параметры: классы, порог precision 0.9, степень штрафа 2, кредит за частичное покрытие 0.5 |

## Правила
- **mask.** Ответ валиден, если метки `[КЛАСС_n]` из закрытого списка, `replacements` перечисляет каждое вхождение по порядку, подстановка восстанавливает исходный текст символ в символ и номера идут 1..k в каждом классе. Невалидный ответ = ничего не замаскировано.
- **Кредит сущности:** 1 при полном покрытии её токенов, иначе 0.5 × доля покрытых токенов. **Recall класса** — средний кредит; **H** — гармоническое среднее по классам.
- **Precision** — доля замаскированных токенов, которые являются ПДн; нейтральная зона (реквизиты организаций, детали выдачи паспорта) исключена. **φ(P) = min(1, P/0.9)²**.
- **unmask.** Ответ выравнивается с эталоном по неизменным сегментам текста (динамическое программирование). Метка верна, если подставленная строка совпадает с эталонной формой с точностью до пробелов и ё/е. **A** — доля верных меток.
- **Итог:** `score = H · φ(P) · A`. Если в наборе нет примеров одного из типов, соответствующий множитель опускается.

## Проверка
```bash
uv run pytest case/tests/test_metric.py        # 25 тестов, в т.ч. жульнические ответы
uv run python tools/reference_strategies.py --data case/dataset/train.jsonl \
    --baseline results/baseline/train/answers.jsonl --out results/metric_sensitivity
```
Заморозка (при подготовке кампании, из каталога арены):
```bash
PYTHONDONTWRITEBYTECODE=1 uv run python -m evaluator.prepare freeze --metric <repo>/case/metric --output <campaign_dir>/metric
uv run python -m evaluator.prepare verify --metric <campaign_dir>/metric
```
Пробная заморозка и сверка с оценщиком арены — `results/arena_smoke_metric.txt`.
