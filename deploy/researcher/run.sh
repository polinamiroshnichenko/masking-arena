#!/usr/bin/env bash
# Запуск исследователя арены (Claude Code в контейнере) для одной кампании.
#   bash deploy/researcher/run.sh mask start      # первый запуск по start.md кампании
#   bash deploy/researcher/run.sh mask continue   # продолжить ту же сессию (после лимита подписки Pro)
# Токен подписки: `claude setup-token`, сохранить в файл (по умолчанию ~/.config/arena/claude-token).
# В контейнер монтируются только workspace, журнал и submit-токены train и public (ADR-0007).
# Private-токена у исследователя нет: проверку готовности и отправку финалистов на private делает оператор
# (deploy/local/arena_ops.py readiness / submit-private) по пакету, который исследователь фиксирует `finalize`.
set -euo pipefail
C=$1; MODE=${2:-start}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
O=$ROOT/operator/arena/$C
TOKEN_FILE=${CLAUDE_TOKEN_FILE:-$HOME/.config/arena/claude-token}
MODEL=${ARENA_RESEARCH_MODEL:-claude-sonnet-5-5}
# High: AA Intelligence Index v5.0.1 = 42 ≥ DeepSeek V4.1 Flash (33); Medium (31) правилу курса не удовлетворяет (ADR-0007)
EFFORT=${ARENA_RESEARCH_EFFORT:-high}
[ -s "$TOKEN_FILE" ] || { echo "нет токена в $TOKEN_FILE (claude setup-token)"; exit 1; }
CD=/workspace/cases/pii-masking-ru/campaigns/$C
if [ "$MODE" = start ]; then
  PROMPT=$(cat "$O/workspace/cases/pii-masking-ru/campaigns/$C/start.md"); RESUME=()
else
  PROMPT="Продолжай кампанию $C по $CD/start.md: восстанови состояние из журнала, board/methods.md и findings, затем продолжай поиск. ВАЖНО про среду: это неинтерактивный режим claude -p — как только ты завершаешь свой ход, процесс заканчивается и все фоновые субагенты, мониторы и ожидания останавливаются (так уже потеряна работа двух исследователей). Поэтому не завершай ход, пока поиск не дошёл до лимита или пока есть незавершённые проверки: запускай субагентов синхронно (без run_in_background) и дожидайся их результата в том же ходе; незавершённые train-проверки из прошлых сессий забери командой evaluator.train result по evaluation_id из runs/."
  RESUME=(--continue)
fi
TS=$(date +%Y%m%d-%H%M%S)
mkdir -p "$O/logs"
docker run --rm -i --name "pii-researcher-$C-$TS" \
  -v "$O/workspace:/workspace" -v "$O/search:/search" \
  -v "$O/secrets/train-token:/run/secrets/train-token:ro" \
  -v "$O/secrets/public-token:/run/secrets/public-token:ro" \
  -v "pii-researcher-$C-home:/home/researcher/.claude" \
  -e CLAUDE_CODE_OAUTH_TOKEN="$(cat "$TOKEN_FILE")" \
  pii-researcher:1 ${RESUME[@]+"${RESUME[@]}"} -p "$PROMPT" --model "$MODEL" --effort "$EFFORT" \
  --permission-mode bypassPermissions --output-format stream-json --verbose \
  > "$O/logs/researcher-$TS.jsonl" 2> "$O/logs/researcher-$TS.err" || true
echo "лог: $O/logs/researcher-$TS.jsonl"
tail -c 600 "$O/logs/researcher-$TS.jsonl"
