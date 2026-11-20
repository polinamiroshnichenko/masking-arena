"""Отчёт по эволюции одной арены из канонического журнала: кривая public, AUC, родословная, таблица кандидатов.

    uv run python tools/evolution_report.py mask
    # → results/evolution/mask/{export.json,curve.json,curve.svg,report.md}

Журнал читается командами арены (`evaluator.search export` и `curve`), в отчёт попадают только данные журнала:
public — общий score, private в журнале отсутствует. Без зависимостей кроме стандартной библиотеки.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARENA = ROOT / "external" / "arena"
CONFIG = json.loads((ROOT / "deploy" / "local" / "campaigns.json").read_text())


def arena(*args: str) -> dict:
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": ".venv312"}
    env.pop("VIRTUAL_ENV", None)
    out = subprocess.run(
        ["uv", "run", "--python", "3.12", "python", "-m", "evaluator.search", *args],
        cwd=ARENA,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def svg_curve(points: list[tuple[int, float | None]], scores: dict[int, float], horizon: int, title: str) -> str:
    """Ступенчатая кривая лучшего public-score (right_step) + точки отдельных предложений."""
    W, H, L, R, T, B = 640, 300, 52, 16, 28, 40
    known = [v for _, v in points if v is not None] + list(scores.values())
    lo = min([0.0] + known)
    hi = max([1e-9] + known)
    hi = hi + (hi - lo) * 0.08 or 1.0

    def x(i: float) -> float:
        return L + (W - L - R) * (i - 0.5) / horizon

    def y(v: float) -> float:
        return T + (H - T - B) * (1 - (v - lo) / (hi - lo))

    css = (
        "text{font:12px system-ui,sans-serif;fill:#4a5563}.g{stroke:#e3e7ec}.s{fill:none;stroke:#0f5c6e;stroke-width:2}"
        ".p{fill:#0f5c6e}.f{fill:#c8402a}"
    )
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" aria-label="{title}">',
        f"<style>{css}</style>",
        f'<text x="{L}" y="16" font-weight="600">{title}</text>',
    ]
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        out.append(
            f'<line class="g" x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
            f'<text x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{v:.2f}</text>'
        )
    for i in range(1, horizon + 1):
        out.append(f'<text x="{x(i):.1f}" y="{H - B + 16}" text-anchor="middle">S{i:02d}</text>')
    out.append(f'<text x="{(L + W - R) / 2}" y="{H - 6}" text-anchor="middle">номер предложенного решения</text>')
    d, prev = "", None
    for i, v in points:
        if v is None:
            continue
        if prev is None:
            d += f"M{x(i) - 0.5 * (W - L - R) / horizon:.1f},{y(v):.1f}"
        else:
            xi = x(i) - 0.5 * (W - L - R) / horizon
            d += f"L{xi:.1f},{y(prev):.1f}L{xi:.1f},{y(v):.1f}"
        prev = v
    if prev is not None:
        last = max(i for i, v in points if v is not None)
        d += f"L{x(last) + 0.5 * (W - L - R) / horizon:.1f},{y(prev):.1f}"
        out.append(f'<path class="s" d="{d}"/>')
    for i, v in scores.items():
        out.append(f'<circle class="p" cx="{x(i):.1f}" cy="{y(v):.1f}" r="4"><title>S{i:02d}: {v:.4f}</title></circle>')
    out.append("</svg>")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("campaign", choices=list(CONFIG))
    ap.add_argument("--public-dataset", help="dataset public-замеров (по умолчанию из журнала)")
    a = ap.parse_args()
    cfg = CONFIG[a.campaign]
    journal = str(ROOT / "operator" / "arena" / a.campaign / "search" / "search.sqlite3")
    out = ROOT / "results" / "evolution" / a.campaign
    out.mkdir(parents=True, exist_ok=True)
    exp = arena("--journal", journal, "export")
    (out / "export.json").write_text(json.dumps(exp, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    cands = exp["candidates"]
    meas = exp["measurements"]
    pub = [m for m in meas if m["comparison"]["split"] == "public" and m["state"] == "succeeded"]
    keys = {
        (m["comparison"]["dataset_id"], m["comparison"]["subset_id"], m["comparison"]["conditions_id"]) for m in pub
    }
    if a.public_dataset:
        keys = {k for k in keys if k[0] == a.public_dataset}
    curve = None
    if len(keys) == 1:
        ds, sub, cond = keys.pop()
        curve = arena(
            "--journal",
            journal,
            "curve",
            "--split",
            "public",
            "--dataset",
            ds,
            "--subset",
            sub,
            "--conditions",
            cond,
            "--direction",
            "maximize",
        )
        (out / "curve.json").write_text(json.dumps(curve, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    by_id = {c["candidate_id"]: c for c in cands}
    pscore = {by_id[m["candidate_id"]]["proposal"]: m["score"] for m in pub}
    best, points = None, []
    for i in range(1, cfg["max_solutions"] + 1):
        if i in pscore:
            best = pscore[i] if best is None else max(best, pscore[i])
        points.append((i, best if i <= len(cands) else None))
    title = f"Кампания {a.campaign}: лучший public-score от номера решения"
    (out / "curve.svg").write_text(svg_curve(points, pscore, cfg["max_solutions"], title), encoding="utf-8")

    def train_of(cid: str) -> str:
        ms = [
            m
            for m in meas
            if m["candidate_id"] == cid and m["comparison"]["split"] == "train" and m["state"] == "succeeded"
        ]
        return "; ".join(f"{m['score']:.3f}" for m in ms) or "—"

    rows = ["| Решение | Родители | Гипотеза | Изменение | train | public |", "|---|---|---|---|---|---|"]
    for c in sorted(cands, key=lambda c: c["proposal"]):
        p = pscore.get(c["proposal"])
        rows.append(
            f"| {c['solution_id']} | {', '.join(c['parents']) or '—'} | {c['hypothesis'][:140]} | "
            f"{c['change'][:140]} | {train_of(c['candidate_id'])} | {'—' if p is None else f'{p:.3f}'} |"
        )
    mer = ["```mermaid", "graph LR"]
    for c in sorted(cands, key=lambda c: c["proposal"]):
        p = pscore.get(c["proposal"])
        mer.append(f'  {c["solution_id"]}["{c["solution_id"]}<br/>{"—" if p is None else f"{p:.3f}"}"]')
        mer += [f"  {par} --> {c['solution_id']}" for par in c["parents"]]
    mer.append("```")
    st = exp["campaign"] if isinstance(exp.get("campaign"), dict) else {}
    auc = None if curve is None else curve.get("auc")
    md = [
        f"# Эволюция: кампания `{a.campaign}`",
        "",
        f"Предложено: {len(cands)} из {cfg['max_solutions']}. Public-оценок: {len(pub)}. "
        f"Лучший public: {max(pscore.values()) if pscore else '—'}. "
        f"AUC (public, right_step, горизонт {cfg['max_solutions']}): "
        f"{auc if auc is not None else 'неполная — нужны public-оценки всех предложений'}.",
        "",
        "![кривая](curve.svg)",
        "",
        "## Родословная",
        "",
        *mer,
        "",
        "## Кандидаты",
        "",
        *rows,
        "",
        f"Состояние журнала: {json.dumps({k: st.get(k) for k in ('stopped', 'finalization')}, ensure_ascii=False)}",
    ]
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[:4]))


if __name__ == "__main__":
    main()
