# Кампания `mask` — маскирование ПДн (кейс pii-masking-ru)

Каталог кампании: `cases/pii-masking-ru/campaigns/mask`. Условия заморожены оператором до поиска (ADR-0006, ADR-0007 репозитория проекта).

## Задача
Только примеры `task = mask` (описание кейса — `cases/pii-masking-ru/README.md`). Ответ — JSON `{"masked_text", "replacements"}`; точный round-trip обязателен.

## Фиксированные условия
- Метрика: замороженный пакет `metric/` (`pii-mask-v1`), направление `maximize`. На наборе только из mask итог = H(recall по классам) · φ(precision).
- Данные `pii-ru-synth-v1-mask`: train 1200 (`cases/pii-masking-ru/dataset/train/dataset.jsonl`, открыт), public 420 (только общий score), private 630 (только статус).
- Решатель: `solve.py` в немодельном runner, образ `pii-mask-runner:1` (Python 3.12 + natasha 1.6.0, razdel 0.5.0; полный список — pip freeze у оператора). Без сети, без LLM, read-only, 1 ГБ памяти, таймаут 60 с на пример.
- Условия сравнения (`conditions` журнала): `pii-mask-v1+pii-mask-runner-1`; `dataset` — `pii-ru-synth-v1-mask`.
- Модель исследователей: Claude (Claude Code в контейнере исследователя, подписка Pro), одна на всю кампанию.

## Разрешённые изменения
Только содержимое каталога своего кандидата `ways/<id>/`: `solve.py`, модули, правила, словари и параметры, в том числе выученные на train. Зашивать значения ПДн, документы или формулировки train запрещено (фамилии, улицы и шаблоны public/private другие).

## Лимит и финализация
- Лимит предложенных решений: **12** (включая baseline как S01 и все неудачные версии).
- Каждое предложение получает public-оценку: AUC считается только по public.
- Лимит минимальный осознанно: исследователь работает на подписке Claude Pro, лимит выбран так, чтобы кампанию можно было довести до конца (ADR-0007).
- Private: ровно **3** финалистов, итоговых **1**; release `pii-mask-c1`.
- Правило выбора финалистов: качество на public и train, разнообразие механизмов, устойчивость на подвыборках train, отсутствие подгонки (вывод ревьюера на каждый).

## Адреса (из контейнера исследователя)
- train: `http://host.docker.internal:8111`, токен `/run/secrets/train-token`
- public: `http://host.docker.internal:8112`, токен `/run/secrets/public-token`
- private: `http://host.docker.internal:8113`, токен `/run/secrets/private-token` (только `search-readiness` и финальная отправка)
- журнал: `/search/search.sqlite3`

## Состояние поиска (ведёт оркестратор)
- 2026-11-20: `GET /v1/search-readiness` (private, без токена в записи): `{"mode":"campaign","gate_enabled":true,"journal_ready":true,"max_solutions":12,"finalists":3,"output_count":1,"evaluation_enabled":true,"release_id":"pii-mask-c1"}` — совпадает с условиями. MLflow кампанией не используется.
- S01 (baseline) зарегистрирован; train полный 0.6455 (`runs/S01-train-full.result.json`), public 0.5346. Осталось мест: 11.
- Раунд 1: два исследователя (A — структурные классы `.wt/rules`, B — ФИО/теггер `.wt/tagger`), квота до 4 версий каждому; резерв оркестратора 3. Замеры — `tools/measure.sh`.
- Возобновление: `uv run python -m evaluator.search --journal /search/search.sqlite3 status`, `board/methods.md`, `findings/`.
