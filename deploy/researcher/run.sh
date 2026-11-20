#!/usr/bin/env bash
# Запуск исследователя арены (Claude Code в контейнере) для одной кампании.
#   bash deploy/researcher/run.sh mask start      # первый запуск по start.md кампании
#   bash deploy/researcher/run.sh mask continue   # продолжить ту же сессию (после лимита подписки Pro)
# Токен подписки: `claude setup-token`, сохранить в файл (по умолчанию ~/.config/arena/claude-token).
# В контейнер монтируются только workspace, журнал и submit-токены кампании (ADR-0007).
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
  PROMPT="Продолжай кампанию $C по $CD/start.md: восстанови состояние из журнала, board/methods.md и findings, затем продолжай поиск."
  RESUME=(--continue)
fi
TS=$(date +%Y%m%d-%H%M%S)
mkdir -p "$O/logs"
docker run --rm -i --name "pii-researcher-$C-$TS" \
  -v "$O/workspace:/workspace" -v "$O/search:/search" \
  -v "$O/secrets/train-token:/run/secrets/train-token:ro" \
  -v "$O/secrets/public-token:/run/secrets/public-token:ro" \
  -v "$O/secrets/private-token:/run/secrets/private-token:ro" \
  -v "pii-researcher-$C-home:/home/researcher/.claude" \
  -e CLAUDE_CODE_OAUTH_TOKEN="$(cat "$TOKEN_FILE")" \
  pii-researcher:1 ${RESUME[@]+"${RESUME[@]}"} -p "$PROMPT" --model "$MODEL" --effort "$EFFORT" \
  --permission-mode bypassPermissions --output-format stream-json --verbose \
  > "$O/logs/researcher-$TS.jsonl" 2> "$O/logs/researcher-$TS.err" || true
echo "лог: $O/logs/researcher-$TS.jsonl"
tail -c 600 "$O/logs/researcher-$TS.jsonl"
