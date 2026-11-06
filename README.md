# Барьер ПДн: маскирование персональных данных, выращенное на арене

Барьер стоит между агентом и внешней LLM. Перед отправкой он заменяет персональные данные метками (`[ФИО_1]`, `[ТЕЛЕФОН_1]`), а в ответе модели возвращает реальные значения в правильной грамматической форме («Уважаемому [ФИО_1]» → «Уважаемому Иванову Ивану Ивановичу»). Решение выращивается эволюцией на арене DeepPavlov и упаковывается в навык агента.

**Статус: ЧП1** — домен, кейс, данные, метрика с тестами, baseline, smoke арены.

## Итог в одной таблице
| | Train (1700) | Public (600) | Private (900) |
|---|---|---|---|
| Baseline (regex + замена без склонения) | 0.286 | 0.289 | — (оценивается в финале) |
| Выращенное решение | — | — | — |

Метрика: `score = H(recall по 10 классам) · φ(precision) · точность восстановления`, [0, 1], maximize ([ADR-0003](docs/adr/0003-metric.md)). Две кампании ([ADR-0006](docs/adr/0006-two-campaigns.md)): маскирование (baseline H·φ: train 0.645 / public 0.535) и восстановление (baseline A: train 0.443 / public 0.542). Лимит 90 + 30 решений (предварительно). Кандидатов в журналах: 0. AUC: —.

## Структура
```
docs/domain.md        обзор домена и классических подходов
docs/arena.md         арена своими словами + smoke-проверки
docs/adr/             решения: методология, формат задачи, метрика, данные и split, решатель без LLM, две кампании
docs/evolution.md, solution.md, talk.md   разбор эволюции, решение, рассказ (ЧП2–ЧП3)
case/                 кейс арены (по cases/template): задача, данные, метрика, кампания, baseline
  dataset/            train.jsonl, manifest.json, generator/ (код генератора)
  metric/             замораживаемый пакет метрики: metric.json, score.py, aggregate.py, pii_metric.py, policy.json
  tests/              тесты метрики (включая жульнические ответы), данных и baseline
  ways/baseline/      стартовый кандидат
tools/                прогон и оценка кандидатов, бутстрэп, эталонные стратегии, smoke через арену и runner
results/              сырые результаты замеров
skill/                итоговый навык (ЧП3)
operator/             закрытый контур оператора: шаблоны генератора, public/private — вне Git до финализации
```

## Как воспроизвести
Нужны [uv](https://docs.astral.sh/uv/) и (для smoke runner) Docker. Python 3.12 и зависимости закреплены в `uv.lock`.

```bash
uv sync
uv run pytest                                   # 38 тестов; 3 операторских пропускаются без operator/

# baseline на train → results/baseline/train/{report.json, per_example.jsonl, answers.jsonl}
uv run python tools/evaluate.py --candidate case/ways/baseline --data case/dataset/train.jsonl --out results/baseline/train

# поведение метрики на эталонных и жульнических стратегиях (таблица ADR-0003)
uv run python tools/reference_strategies.py --data case/dataset/train.jsonl \
    --baseline results/baseline/train/answers.jsonl --out results/metric_sensitivity

# шум метрики для обоснования размеров split
uv run python tools/bootstrap.py --per-example results/baseline/train/per_example.jsonl \
    --sizes 200,400,600,900 --reps 300 --out results/data/bootstrap_baseline.json
uv run python tools/evaluate.py --candidate results/probes/robust_placeholders --data case/dataset/train.jsonl --out /tmp/probe
uv run python tools/bootstrap.py --per-example results/baseline/train/per_example.jsonl \
    --paired /tmp/probe/per_example.jsonl --sizes 200,400,600,900 --reps 300 --out results/data/bootstrap_paired_probe.json

# арена: smoke и прогон кейса через её код
git clone https://gitlab.deeppavlov.ai/deeppavlov/arena.git external/arena   # проверено на 669d9ed
(cd external/arena && uv sync --frozen && uv run python -m evaluator.smoke)
(cd external/arena && PYTHONDONTWRITEBYTECODE=1 uv run python -m evaluator.prepare freeze \
    --metric ../../case/metric --output /tmp/pii-metric && uv run python -m evaluator.prepare verify --metric /tmp/pii-metric \
    && PYTHONPATH=. uv run python ../../tools/arena_smoke.py --frozen /tmp/pii-metric \
       --data ../../case/dataset/train.jsonl --answers ../../results/baseline/train/answers.jsonl --limit 200)
bash tools/runner_smoke.sh case/ways/baseline case/dataset/train.jsonl 20

# только у оператора (нужен operator/): генерация split и public-оценка
uv run python case/dataset/generator/generate.py --split public --n-mask 420 --n-unmask 180 --out operator/data/public.jsonl
uv run python tools/evaluate.py --candidate case/ways/baseline --data operator/data/public.jsonl --out operator/results/baseline/public
```

## Модели
- Решатель и метрика — без LLM (ADR-0005).
- Исследователи и оркестратор арены — **выбор до старта кампании**: модель, версия, Intelligence Index Artificial Analysis (не ниже DeepSeek Flash) и дата проверки будут записаны здесь и в `case/campaign/campaign.md`.
- Для написания шаблонов генератора и кода использовался Claude Code.
