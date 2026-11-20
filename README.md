# Барьер ПДн: маскирование персональных данных, выращенное на арене

Барьер стоит между агентом и внешней LLM. Перед отправкой он заменяет персональные данные метками (`[ФИО_1]`, `[ТЕЛЕФОН_1]`), а в ответе модели возвращает реальные значения в правильной грамматической форме («Уважаемому [ФИО_1]» → «Уважаемому Иванову Ивану Ивановичу»). Решение выращивается эволюцией на арене DeepPavlov и упаковывается в навык агента.

**Статус: ЧП2** — подготовка двух арен завершена (метрика `pii-mask-v1` заморожена, оценщики и готовность private проверены, приёмка контура пройдена), проверка метрики на реальных документах, исследователь в изолированном контейнере. Материалы ЧП2: блок-схема решения и потоков данных — [docs/architecture.md](docs/architecture.md); разбор эволюции (запуски, кривая, родословная, эпизоды трейсов, проблемы) — [docs/evolution.md](docs/evolution.md); текущее лучшее решение — [docs/solution.md](docs/solution.md); подготовка — [docs/preparation.md](docs/preparation.md); графики — [results/evolution/mask/](results/evolution/mask/report.md), [разбор по классам](results/evolution/mask/train_analysis/report.md), [реальные документы](results/real_check/summary.md); ответы на вопросы — [docs/checkpoint-2.md](docs/checkpoint-2.md).

## Итог в одной таблице
Две арены (ADR-0006, ADR-0007), этапы по очереди: сначала маскирование, затем восстановление.

| | Train | Public | Private | Реальные документы (29, вне поиска) |
|---|---|---|---|---|
| **mask**: baseline (H·φ) | 0.645 (1200) | 0.535 (420) | — (финал) | 0.000 — одна пропущенная дата рождения; R по классам 0.78 ([сводка](results/real_check/summary.md)) |
| **mask**: лучшее на сейчас (S02, кампания идёт) | 0.956 | 0.969 | — (финал) | 0.538; адреса 0.12 |
| **unmask**: baseline (A) | 0.443 (500) | 0.542 (180) | — (финал) | — |
| **unmask**: выращенное решение | — | — | — | — |

Метрика: `score = H(recall по 10 классам) · φ(precision) · точность восстановления`, [0, 1], maximize ([ADR-0003](docs/adr/0003-metric.md)). На наборе одного типа примеров множитель другого опускается: в арене mask score = H·φ, в арене unmask score = A. Лимиты: mask — 12 предложений (K = 3, N = 1), unmask — 6 (K = 2, N = 1).

## Структура
```
docs/domain.md        обзор домена и классических подходов
docs/arena.md         арена своими словами + smoke-проверки
docs/adr/             решения: методология, формат задачи, метрика, данные и split, решатель без LLM, две кампании, исследователь
docs/preparation.md   подготовка арен по prepare.md: версии, проверки, готовность private, отличия развёртывания
docs/real-check-guidelines.md   правила разметки реальных документов (проверочная выборка вне поиска)
docs/evolution.md, solution.md, talk.md   разбор эволюции, решение, рассказ (ЧП2–ЧП3)
case/                 кейс арены (по cases/template): задача, данные, метрика, кампания, baseline
  dataset/            train.jsonl, manifest.json, generator/ (код генератора)
  metric/             замораживаемый пакет метрики: metric.json, score.py, aggregate.py, pii_metric.py, policy.json
  tests/              тесты метрики (включая жульнические ответы), данных и baseline
  ways/baseline/      стартовый кандидат
tools/                оценка кандидатов, бутстрэп, стратегии, параметры метрики, разметка, отчёт эволюции, smoke
deploy/local/         операторский контур двух арен (arena_ops.py), условия и стартовые промпты кампаний
deploy/runner/        образы решателя mask / unmask (закреплённые библиотеки)
deploy/researcher/    контейнер исследователя (Claude Code) и скрипт запуска
results/              сырые результаты замеров
skill/                итоговый навык (ЧП3)
operator/             закрытый контур оператора (вне Git): шаблоны генератора, public/private, состояние арен, реальная выборка
data_idd/             реальные документы от автора (вне Git)
```

## Как воспроизвести
Нужны [uv](https://docs.astral.sh/uv/) и (для smoke runner) Docker. Python 3.12 и зависимости закреплены в `uv.lock`.

```bash
uv sync
uv run pytest                                   # 44 теста; операторские пропускаются без operator/

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
git clone https://gitlab.deeppavlov.ai/deeppavlov/arena.git external/arena   # проверено на 7cb90b8
(cd external/arena && uv sync --frozen && uv run python -m evaluator.smoke)
(cd external/arena && PYTHONDONTWRITEBYTECODE=1 uv run python -m evaluator.prepare freeze \
    --metric ../../case/metric --output /tmp/pii-metric && uv run python -m evaluator.prepare verify --metric /tmp/pii-metric \
    && PYTHONPATH=. uv run python ../../tools/arena_smoke.py --frozen /tmp/pii-metric \
       --data ../../case/dataset/train.jsonl --answers ../../results/baseline/train/answers.jsonl --limit 200)
bash tools/runner_smoke.sh case/ways/baseline case/dataset/train.jsonl 20

# чувствительность метрики к её параметрам (ADR-0003)
uv run python tools/metric_params.py --data case/dataset/train.jsonl \
    --baseline results/baseline/train/answers.jsonl --out results/metric_sensitivity/params.md

# только у оператора (нужен operator/): две арены
docker build -f deploy/runner/Dockerfile --build-context arena=external/arena --build-arg CAMPAIGN=mask -t pii-mask-runner:1 .
docker build -f deploy/runner/Dockerfile --build-context arena=external/arena --build-arg CAMPAIGN=unmask -t pii-unmask-runner:1 .
docker build -f deploy/researcher/Dockerfile --build-context arena=external/arena -t pii-researcher:1 .
uv run python deploy/local/arena_ops.py prepare mask && uv run python deploy/local/arena_ops.py start mask
uv run python deploy/local/arena_ops.py readiness mask
bash deploy/researcher/run.sh mask start          # затем: run.sh mask continue
uv run python tools/evolution_report.py mask      # кривая, AUC, родословная → results/evolution/mask/

# только у оператора: проверка на реальных документах
uv run python tools/annotate.py apply --docs operator/real_check/docs --spec operator/real_check/spec.json
uv run python tools/annotate.py build --docs operator/real_check/docs --out operator/real_check/real.jsonl
uv run python tools/evaluate.py --candidate case/ways/baseline --data operator/real_check/real.jsonl

# только у оператора (нужен operator/): генерация split и public-оценка
uv run python case/dataset/generator/generate.py --split public --n-mask 420 --n-unmask 180 --out operator/data/public.jsonl
uv run python tools/evaluate.py --candidate case/ways/baseline --data operator/data/public.jsonl --out operator/results/baseline/public
```

## Модели
- Решатель и метрика — без LLM (ADR-0005).
- Исследователи и оркестратор арены — **Claude Sonnet 5.5** (`claude-sonnet-5-5`, effort High) в Claude Code 2.1.318 по подписке Pro. Artificial Analysis Intelligence Index v5.0.1: 42 против 33 у DeepSeek V4.1 Flash; проверено 2026-11-20. На effort Medium индекс 31, ниже порога курса, поэтому High закреплён (ADR-0007).
- Для написания шаблонов генератора и кода использовался Claude Code.
