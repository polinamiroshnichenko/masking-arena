"""Baseline соблюдает контракт runner арены и даёт валидные ответы на всём train."""
import json
import subprocess
import sys
from pathlib import Path

from pii_metric import load_policy, score_example

ROOT = Path(__file__).resolve().parents[2]
SOLVE = ROOT / "case" / "ways" / "baseline" / "solve.py"
TRAIN = ROOT / "case" / "dataset" / "train.jsonl"

sys.path.insert(0, str(SOLVE.parent))
import solve as baseline  # noqa: E402


def test_runner_contract_via_subprocess(tmp_path):
    ex = json.loads(TRAIN.read_text(encoding="utf-8").splitlines()[0])
    out = subprocess.run([sys.executable, "-I", str(SOLVE)], cwd=tmp_path, capture_output=True, check=True,
                         input=json.dumps({"input": ex["input"], "assets_dir": "/assets"}).encode()).stdout
    assert isinstance(json.loads(out)["answer"], str)


def test_baseline_answers_are_valid_on_train():
    policy = load_policy()
    for line in TRAIN.read_text(encoding="utf-8").splitlines():
        ex = json.loads(line)
        st = score_example(ex, baseline.solve(ex["input"]), policy)["statistics"]
        assert st["valid"], (ex["id"], st["error"])
