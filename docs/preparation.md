# Подготовка кампаний (по prompts/prepare.md арены) — свидетельства готовности

Дата: 2026-11-20. Две отдельные арены, этапы идут по очереди: сначала `mask`, затем `unmask` (ADR-0006, ADR-0007). Baseline общий — `case/ways/baseline` без изменений.

## Условия
| | mask | unmask |
|---|---|---|
| Каталог кампании | `cases/pii-masking-ru/campaigns/mask` | `cases/pii-masking-ru/campaigns/unmask` |
| Примеры | только `task = mask` | только `task = unmask` |
| train / public / private | 1200 / 420 / 630 | 500 / 180 / 270 |
| Итог метрики на наборе | H(recall по классам) · φ(precision) | доля верно восстановленных меток A |
| Лимит предложений | 12 | 6 |
| Финалистов K / итоговых N | 3 / 1 | 2 / 1 |
| release | `pii-mask-c1` | `pii-unmask-c1` |
| Образ решателя | `pii-mask-runner:1` (natasha 1.6.0, razdel 0.5.0) | `pii-unmask-runner:1` (pymorphy3 2.0.4 + словарь ru, razdel 0.5.0) |
| `conditions` журнала | `pii-mask-v1+pii-mask-runner-1` | `pii-mask-v1+pii-unmask-runner-1` |

Полные условия: `deploy/local/campaign/<c>/campaign.md` (в workspace исследователя — с подставленными числами), конфигурация — `deploy/local/campaigns.json`.

## Версии
- Арена: коммит `7cb90b8` (2026-11), Python 3.12.16 (окружение `.venv312`).
- Пакет метрики `pii-mask-v1`: `evaluator.prepare freeze` + `verify` из `case/metric`, sha256(`metric.lock.json`) = `a9e1b6962bb3c72e…`, одинаковый в обеих аренах и в копиях исследователей; `aggregation_input: report`.
- Образы: `pii-mask-runner:1` `39713e2b3f40`, `pii-unmask-runner:1` `87eca831d625` (основа — `runner/main.py` арены), `pii-researcher:1` `354cdb0c71d4` (Claude Code 2.1.318, uv 0.13.7). Состав зависимостей решателя — `deploy/runner/pip-freeze-*.txt`.
- Модель исследователей: Claude Sonnet 5.5 (`claude-sonnet-5-5`, effort High) через Claude Code по подписке Pro; AA Intelligence Index v5.0.1 = 42 при DeepSeek V4.1 Flash = 33 (проверено 2026-11-20); одна на обе кампании (ADR-0007).

## Проверки
| Проверка | Результат |
|---|---|
| Семантические тесты метрики | `uv run pytest` — все тесты проходят (метрика, жульнические ответы, данные, baseline, разметка) |
| Анализ чувствительности параметров | `results/metric_sensitivity/params.md`, ADR-0003 |
| Проверка на реальных документах | `results/real_check/summary.md` — порядок стратегий сохраняется |
| Smoke арены | `evaluator.smoke` — passed; pytest арены 2007 passed (падают только многопоточные тесты с жёсткими таймаутами и 3 теста ФС macOS) |
| Приёмка контура на отдельном состоянии и открытых данных (`acceptance`) | propose S01 → train (submit/status/result → журнал) → public (begin, pack, submit, wait → только общий score, finish) → лимит → finalize → private (только статус; оценки и победители — в закрытом record оператора) → другой пакет на private — HTTP 409, тот же пакет — прежний статус без повторного запуска, без токена — 401; `curve` помечает отсутствующие public-оценки |
| Контейнер исследователя | видит только `/workspace`, `/search`, `/run/secrets` (токены train и public; private-токен с 2026-11-20 только у оператора); файлов оператора нет (`/Users` отсутствует); запись в workspace и журнал работает; оценщики доступны по `host.docker.internal` |
| Журналы | инициализированы пустыми: mask — лимит 12, unmask — лимит 6; предложено 0 |

## Готовность private (`GET /v1/search-readiness`, 2026-11-20)
```
uv run python deploy/local/arena_ops.py readiness mask
{"mode":"campaign","gate_enabled":true,"journal_ready":true,"max_solutions":12,"finalists":3,"output_count":1,"evaluation_enabled":true,"release_id":"pii-mask-c1"}
uv run python deploy/local/arena_ops.py readiness unmask
{"mode":"campaign","gate_enabled":true,"journal_ready":true,"max_solutions":6,"finalists":2,"output_count":1,"evaluation_enabled":true,"release_id":"pii-unmask-c1"}
```
Перед стартом и каждым возобновлением этапа проверка повторяется.

## Адреса для исследователя
train / public: mask — `http://host.docker.internal:8111/8112`, unmask — `…:8121/8122`; токены — `/run/secrets/{train,public}-token`. Private (8113 / 8123) и его токен — только у оператора; журнал — `/search/search.sqlite3`. Сервисы слушают только 127.0.0.1 хоста.

## Отличия развёртывания от эталонного и найденные проблемы
- Оценщики — процессы на хосте (раздел «Сервис без модели» deploy/README), без Compose и gateway: решатель и метрика без LLM.
- `ARENA_SLOT_DIR` не используется: на macOS общий файловый пул слотов падал с ENOENT на `slot-N.lock` при параллельных примерах; параллельность ограничена `ARENA_EXECUTION_SLOTS=4` внутри процесса, арены работают по очереди.
- Docker Desktop на macOS после удаления и пересоздания каталога какое-то время считал его несуществующим для bind mount → runner падал; не пересоздавать каталоги состояния работающей кампании.
- Train-клиент сам делает `begin`; ручной `begin` перед `evaluator.train submit` приводит к ошибке «measurement already exists» — это написано в стартовом промпте исследователя.
- Гармоническое среднее на маленьких подвыборках train обнуляется, если один класс пропущен целиком (12 примеров приёмки → 0.0): подвыборки train должны быть представительными по классам.

## Открытые ограничения
- Расход токенов исследователей не идёт через gateway арены: сохраняются логи Claude Code (`operator/arena/<c>/logs/researcher-*.jsonl`), в журнале расход подготовки — «неизвестен» (ADR-0007).
- Разметка реальной проверочной выборки выполнена агентом; автор проверяет её выборочно.
