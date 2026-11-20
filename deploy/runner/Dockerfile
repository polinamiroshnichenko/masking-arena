# Образ решателя кейса pii-masking-ru: runner арены (тот же main.py и контракт stdin/stdout)
# + закреплённые библиотеки своей кампании. Сборка из корня репозитория:
#   docker build -f deploy/runner/Dockerfile --build-context arena=external/arena \
#       --build-arg CAMPAIGN=mask   -t pii-mask-runner:1 .
#   docker build -f deploy/runner/Dockerfile --build-context arena=external/arena \
#       --build-arg CAMPAIGN=unmask -t pii-unmask-runner:1 .
FROM python:3.12.12-slim
ARG CAMPAIGN
COPY deploy/runner/requirements-${CAMPAIGN}.txt /opt/requirements.txt
RUN pip install --no-cache-dir -r /opt/requirements.txt && pip freeze > /opt/pip-freeze.txt
WORKDIR /opt/runner
COPY --from=arena runner/main.py /opt/runner/main.py
USER 10001:10001
ENTRYPOINT ["python", "-I", "/opt/runner/main.py"]
