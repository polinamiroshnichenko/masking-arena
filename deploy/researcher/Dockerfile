# Исследователь арены: Claude Code + окружение арены. Видит только то, что оператор смонтировал:
# /workspace (чистая копия арены с кейсом и train), /search (журнал), /run/secrets (submit-токены).
#   docker build -f deploy/researcher/Dockerfile --build-context arena=external/arena -t pii-researcher:1 .
FROM python:3.12.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates nodejs npm sqlite3 \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @anthropic-ai/claude-code@2.1.318 \
 && pip install --no-cache-dir uv==0.13.7
COPY --from=arena pyproject.toml uv.lock /opt/arena/
ENV UV_PROJECT_ENVIRONMENT=/opt/arena-venv UV_PYTHON_DOWNLOADS=never
RUN cd /opt/arena && uv sync --frozen --no-install-project --python /usr/local/bin/python3.12 \
 && useradd -m -u 10001 researcher && chown -R researcher /opt/arena-venv
USER researcher
WORKDIR /workspace
RUN git config --global user.name "arena-researcher" && git config --global user.email "researcher@arena.local" \
 && git config --global --add safe.directory '*'
ENTRYPOINT ["claude"]
