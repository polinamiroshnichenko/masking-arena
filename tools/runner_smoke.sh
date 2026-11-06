#!/usr/bin/env bash
# Прогон кандидата через образ runner арены (немодельный режим): по контейнеру на пример, без сети,
# read-only корень, кандидат смонтирован в /candidate. Замеряет время на пример (стоимость оценки).
#   bash tools/runner_smoke.sh case/ways/baseline case/dataset/train.jsonl 20
set -euo pipefail
CANDIDATE=$(cd "$1" && pwd); DATA=$2; N=${3:-20}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
ARENA=${ARENA_DIR:-$ROOT/external/arena}
docker build -q -f "$ARENA/runner/Dockerfile" -t arena-runner:local "$ARENA" >/dev/null
echo "image: arena-runner:local ($(docker image inspect arena-runner:local --format '{{.Id}}' | cut -c1-19))"
start=$(python3 -c 'import time; print(time.time())')
ok=0
while IFS= read -r line; do
  task=$(python3 -c 'import json,sys; r=json.loads(sys.argv[1]); print(json.dumps({"input": r["input"], "uses_model": False}, ensure_ascii=False))' "$line")
  out=$(printf '%s' "$task" | docker run --rm -i --network none --read-only --tmpfs /tmp \
        --memory 512m --pids-limit 64 -v "$CANDIDATE:/candidate:ro" arena-runner:local)
  python3 -c 'import json,sys; a=json.loads(sys.argv[1])["answer"]; json.loads(a)' "$out" && ok=$((ok+1))
done < <(head -n "$N" "$DATA")
python3 -c "import time; t=time.time()-$start; print(f'examples: $N, valid JSON answers: $ok, total {t:.1f}s, {t/$N:.2f}s per example')"
