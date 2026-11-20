#!/usr/bin/env bash
# Использование: measure.sh train|public <S..> [subset-id: примеры через пробел для train]
# Замер зарегистрированной версии по ID (снимок берётся из журнала). Блокирует до завершения;
# запускать в фоне (run_in_background), результат — в runs/<S..>.<split>.*.json
set -euo pipefail
cd /workspace
J=/search/search.sqlite3
R=cases/pii-masking-ru/campaigns/mask/runs
C=pii-mask-v1+pii-mask-runner-1
D=pii-ru-synth-v1-mask
split=$1; S=$2; shift 2
if [ "$split" = train ]; then
  U=http://host.docker.internal:8111; T=/run/secrets/train-token
  M=${S}-train-full
  ex=(); for e in "$@"; do ex+=(--example-id "$e"); done
  [ $# -gt 0 ] && M=${S}-train-sub-$(echo "$@" | sha1sum | cut -c1-8)
  out=$(uv run python -m evaluator.train submit --candidate "$S" --measurement "$M" --dataset $D --conditions $C \
    --train-data cases/pii-masking-ru/dataset/train/dataset.jsonl --url $U --token-file $T --journal $J "${ex[@]}")
  eid=$(echo "$out" | python3 -c 'import json,sys;print(json.load(sys.stdin)["evaluation_id"])')
  while true; do
    st=$(uv run python -m evaluator.train result --evaluation-id "$eid" --measurement "$M" --output $R/$M.result.json \
      --url $U --token-file $T --journal $J | python3 -c 'import json,sys;print(json.load(sys.stdin)["status"])')
    case $st in FINISHED|FAILED) break;; esac
    sleep 20
  done
  echo "$M $st -> $R/$M.result.json"
else
  U=http://host.docker.internal:8112; T=/run/secrets/public-token
  M=${S}-public
  uv run python -m evaluator.search --journal $J begin --id "$M" --candidate "$S" --split public --dataset $D --subset all --conditions $C --job public-service >/dev/null
  d=$(mktemp -d); uv run python -m evaluator.search --journal $J restore --candidate "$S" --directory "$d/c" >/dev/null
  uv run python -m evaluator.client pack --candidate "$S=$d/c" --output $R/$S.bundle.json >/dev/null
  eid=$(uv run python -m evaluator.client submit --bundle $R/$S.bundle.json --url $U --token-file $T | python3 -c 'import json,sys;print(json.load(sys.stdin)["evaluation_id"])')
  uv run python -m evaluator.client wait --evaluation-id "$eid" --output $R/$S.public.wait.json --url $U --token-file $T >/dev/null
  sc=$(python3 -c 'import json;d=json.load(open("'$R/$S.public.wait.json'"));print(d["scores"][0]["score"])')
  uv run python -m evaluator.search --journal $J finish --id "$M" --score "$sc" >/dev/null
  echo "$M public score=$sc"
fi
