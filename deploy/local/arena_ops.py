"""Операторский контур двух арен (mask, unmask) на одном узле: подготовка, оценщики, готовность.

    uv run python deploy/local/arena_ops.py prepare  mask     # данные, заморозка метрики, токены, журнал, workspace
    uv run python deploy/local/arena_ops.py start    mask     # train / public / private оценщики (127.0.0.1)
    uv run python deploy/local/arena_ops.py readiness mask    # GET /v1/search-readiness private-сервиса
    uv run python deploy/local/arena_ops.py status   mask
    uv run python deploy/local/arena_ops.py stop     mask

Всё закрытое создаётся в operator/arena/<campaign>/ (вне Git): данные split, состояние оценщиков, журнал,
токены. Исследователь получает только workspace/, журнал и токены через mounts контейнера (deploy/researcher).
Решатель и метрика без LLM: модельный gateway не запускается (deploy/README арены, «Сервис без модели»).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARENA = ROOT / "external" / "arena"
OPS = ROOT / "operator" / "arena"
CONFIG = json.loads((Path(__file__).parent / "campaigns.json").read_text())
CASE_ID = "pii-masking-ru"
SPLITS = ("train", "public", "private")
ARENA_ENV = {**os.environ, "UV_PROJECT_ENVIRONMENT": ".venv312"}
ARENA_ENV.pop("VIRTUAL_ENV", None)


def arena(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", "--python", "3.12", "python", *args],
        cwd=ARENA,
        env=ARENA_ENV,
        text=True,
        capture_output=True,
        **kw,
    )


def paths(c: str) -> dict[str, Path]:
    base = OPS / c
    return {
        "base": base,
        "data": base / "data",
        "metric": base / "metric",
        "search": base / "search",
        "journal": base / "search" / "search.sqlite3",
        "control": base / "control",
        "state": base / "state",
        "secrets": base / "secrets",
        "logs": base / "logs",
        "workspace": base / "workspace",
        "slots": OPS / "slots",
        "campaign_dir": base / "workspace" / "cases" / CASE_ID / "campaigns" / c,
    }


def prepare(c: str) -> None:
    cfg, p = CONFIG[c], paths(c)
    if p["journal"].exists():
        raise SystemExit(f"{c}: журнал уже существует — подготовка не повторяется (возобновление: start)")
    for k in ("data", "control", "state", "secrets", "logs", "search", "slots"):
        p[k].mkdir(parents=True, exist_ok=True)
    for d in (p["secrets"], p["state"], p["control"]):
        d.chmod(0o700)
    sources = {
        "train": ROOT / "case/dataset/train.jsonl",
        "public": ROOT / "operator/data/public.jsonl",
        "private": ROOT / "operator/data/private.jsonl",
    }
    sizes = {}
    for split, src in sources.items():
        out = p["data"] / f"{split}.jsonl"
        if cfg.get("acceptance"):  # приёмка: только открытые train-примеры, закрытые данные не трогаются
            src = ROOT / "case/dataset/train.jsonl"
        r = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/select_task.py"),
                "--task",
                cfg["task"],
                "--data",
                str(src),
                "--out",
                str(out),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        sizes[split] = json.loads(r.stdout)["examples"]
        if cfg.get("acceptance"):
            lines = out.read_text(encoding="utf-8").splitlines()[: {"train": 12, "public": 8, "private": 8}[split]]
            out.write_text("\n".join(lines) + "\n", encoding="utf-8")
            sizes[split] = len(lines)
    # метрика: тот же пакет для всех split
    env = {**ARENA_ENV, "PYTHONDONTWRITEBYTECODE": "1"}
    for cmd in (
        ["-m", "evaluator.prepare", "freeze", "--metric", str(ROOT / "case/metric"), "--output", str(p["metric"])],
        ["-m", "evaluator.prepare", "verify", "--metric", str(p["metric"])],
    ):
        r = subprocess.run(
            ["uv", "run", "--python", "3.12", "python", *cmd], cwd=ARENA, env=env, text=True, capture_output=True
        )
        if r.returncode:
            raise SystemExit(r.stderr)
        print(r.stdout.strip())
    for split in SPLITS:
        tok = p["secrets"] / f"{split}-token"
        tok.write_text(secrets.token_urlsafe(32))
        tok.chmod(0o600)
        (p["state"] / split).mkdir(exist_ok=True)
    r = arena(
        "-m", "evaluator.search", "--journal", str(p["journal"]), "init", "--max-solutions", str(cfg["max_solutions"])
    )
    if r.returncode:
        raise SystemExit(r.stderr)
    print(r.stdout.strip())
    make_workspace(c, sizes)
    print(json.dumps({"campaign": c, "examples": sizes}, ensure_ascii=False))


def make_workspace(c: str, sizes: dict[str, int]) -> None:
    """Чистая копия для исследователя: арена (git) + кейс без operator/, только train своей кампании."""
    p, cfg = paths(c), CONFIG[c]
    ws = p["workspace"]
    if ws.exists():
        raise SystemExit(f"{ws} уже существует")
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(ARENA), str(ws)], check=True)
    case = ws / "cases" / CASE_ID
    for rel in (
        "README.md",
        "prompts",
        "metric",
        "ways/baseline",
        "dataset/README.md",
        "dataset/manifest.json",
        "dataset/generator",
    ):
        src, dst = ROOT / "case" / rel, case / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, dst)
    (case / "dataset" / "train").mkdir(parents=True)
    shutil.copy2(p["data"] / "train.jsonl", case / "dataset" / "train" / "dataset.jsonl")
    cd = p["campaign_dir"]
    shutil.copytree(p["metric"], cd / "metric")
    tpl = ROOT / "deploy" / "local" / "campaign" / ("mask" if cfg.get("acceptance") else c)
    for f in tpl.rglob("*"):
        if f.is_file():
            dst = cd / f.relative_to(tpl)
            dst.parent.mkdir(parents=True, exist_ok=True)
            text = f.read_text(encoding="utf-8")
            for k, v in {
                "MAX_SOLUTIONS": cfg["max_solutions"],
                "FINALISTS": cfg["finalists"],
                "OUTPUT_COUNT": cfg["output_count"],
                "RELEASE_ID": cfg["release_id"],
                "DATASET_ID": cfg["dataset_id"],
                "CONDITIONS_ID": cfg["conditions_id"],
                "N_TRAIN": sizes["train"],
                "N_PUBLIC": sizes["public"],
                "N_PRIVATE": sizes["private"],
                "TRAIN_PORT": cfg["ports"]["train"],
                "PUBLIC_PORT": cfg["ports"]["public"],
                "PRIVATE_PORT": cfg["ports"]["private"],
            }.items():
                text = text.replace("{{" + k + "}}", str(v))
            dst.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(ws),
            "-c",
            "user.name=operator",
            "-c",
            "user.email=operator@local",
            "commit",
            "-q",
            "-m",
            f"case {CASE_ID}, campaign {c}: prepared",
        ],
        check=True,
    )


def service_env(c: str, split: str) -> dict[str, str]:
    cfg, p = CONFIG[c], paths(c)
    env = {
        **ARENA_ENV,
        "ARENA_DATASET_PATH": str(p["data"] / f"{split}.jsonl"),
        "ARENA_METRIC_PATH": str(p["metric"]),
        "ARENA_STATE_DIR": str(p["state"] / split),
        "ARENA_TOKEN_FILE": str(p["secrets"] / f"{split}-token"),
        "ARENA_RUNNER_IMAGE": cfg["runner_image"],
        "ARENA_RELEASE_ID": cfg["release_id"],
        "ARENA_USES_MODEL": "false",
        "ARENA_RUNNER_NETWORK": "none",
        "ARENA_MODEL": "",
        "ARENA_MODEL_BASE_URL": "",
        "ARENA_SEARCH_MODE": "campaign",
        "ARENA_SEARCH_JOURNAL": str(p["journal"]),
        "ARENA_SEARCH_MAX_SOLUTIONS": str(cfg["max_solutions"]),
        "ARENA_SEARCH_STOP_PATH": str(p["control"] / "stop.json"),
        "ARENA_FINALISTS": str(cfg["finalists"]),
        "ARENA_OUTPUT_COUNT": str(cfg["output_count"]),
        # ARENA_SLOT_DIR не задаётся: на macOS общий файловый пул слотов падал с ENOENT на slot-N.lock при
        # параллельных примерах (results/acceptance). Параллельность ограничена внутри процесса EXECUTION_SLOTS,
        # а арены mask и unmask работают поэтапно, не одновременно (ADR-0007).
        "ARENA_EXECUTION_SLOTS": "4",
        "ARENA_RUNNER_TIMEOUT": "60",
        "ARENA_RUNNER_MEMORY": "1g",
        "ARENA_CASE_ID": CASE_ID,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if split == "train":
        env |= {
            "ARENA_CAMPAIGN_ID": cfg["release_id"],
            "ARENA_DATASET_ID": cfg["dataset_id"],
            "ARENA_CONDITIONS_ID": cfg["conditions_id"],
            "ARENA_TRAIN_TRACKING": "false",
        }
    else:
        env["ARENA_SPLIT"] = split
    return env


def start(c: str) -> None:
    cfg, p = CONFIG[c], paths(c)
    for split in SPLITS:
        pid_file = p["logs"] / f"{split}.pid"
        if pid_file.exists() and alive(int(pid_file.read_text())):
            print(f"{split}: уже запущен")
            continue
        port = cfg["ports"][split]
        if split == "train":
            cmd = ["-m", "evaluator.train", "serve", "--host", "127.0.0.1", "--port", str(port)]
        else:
            cmd = [
                "-c",
                "import uvicorn; from evaluator.service import Settings, create_app; "
                f"uvicorn.run(create_app(Settings.from_env()), host='127.0.0.1', port={port}, access_log=False)",
            ]
        log = (p["logs"] / f"{split}.log").open("a")
        proc = subprocess.Popen(
            ["uv", "run", "--python", "3.12", "python", *cmd],
            cwd=ARENA,
            env=service_env(c, split),
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        pid_file.write_text(str(proc.pid))
        print(f"{split}: pid {proc.pid}, http://127.0.0.1:{port}")
    for split in SPLITS:
        wait_health(cfg["ports"][split], split)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_health(port: int, split: str, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
                print(f"{split}: healthz {r.status}")
                return
        except OSError:
            time.sleep(1)
    raise SystemExit(f"{split}: сервис не ответил за {timeout} с — см. logs/{split}.log")


def stop(c: str) -> None:
    p = paths(c)
    for split in SPLITS:
        pid_file = p["logs"] / f"{split}.pid"
        if pid_file.exists():
            pid = int(pid_file.read_text())
            if alive(pid):
                os.killpg(pid, signal.SIGTERM)
            pid_file.unlink()
            print(f"{split}: остановлен")


def status(c: str) -> None:
    p = paths(c)
    for split in SPLITS:
        pid_file = p["logs"] / f"{split}.pid"
        state = "запущен" if pid_file.exists() and alive(int(pid_file.read_text())) else "остановлен"
        print(f"{split}: {state}")
    print(arena("-m", "evaluator.search", "--journal", str(p["journal"]), "status").stdout)


def readiness(c: str) -> None:
    cfg, p = CONFIG[c], paths(c)
    r = arena(
        "-m",
        "evaluator.client",
        "search-readiness",
        "--url",
        f"http://127.0.0.1:{cfg['ports']['private']}",
        "--token-file",
        str(p["secrets"] / "private-token"),
        "--require-campaign",
        "--max-solutions",
        str(cfg["max_solutions"]),
        "--finalists",
        str(cfg["finalists"]),
        "--output-count",
        str(cfg["output_count"]),
        "--release-id",
        cfg["release_id"],
    )
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode:
        raise SystemExit(r.returncode)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("prepare", "start", "stop", "status", "readiness"))
    ap.add_argument("campaign", choices=tuple(CONFIG))
    a = ap.parse_args()
    {"prepare": prepare, "start": start, "stop": stop, "status": status, "readiness": readiness}[a.command](a.campaign)


if __name__ == "__main__":
    main()
