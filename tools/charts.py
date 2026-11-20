"""Графики для разбора показателей (SVG, только stdlib): results/charts/*.svg.

    uv run python tools/charts.py

Источники — уже сохранённые результаты: results/metric_sensitivity, results/data/bootstrap_baseline.json,
results/evolution/mask/train_analysis/summary.json, у оператора — operator/real_check/results (только агрегаты).
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "charts"
COLORS = ["#9aa5b1", "#0f5c6e", "#3fa3b8", "#c8902a", "#6b56b8", "#c8402a"]
STYLE = "<style>text{font:12px system-ui,sans-serif;fill:#4a5563}.g{stroke:#e3e7ec}.t{font-weight:600}</style>"


def grouped_hbars(
    title: str, rows: list[str], series: dict[str, list[float | None]], vmax: float = 1.0, fmt: str = "{:.2f}"
) -> str:
    W, L, T = 680, 170, 44
    k = len(series)
    row = 10 + 11 * k
    H = T + row * len(rows) + 30
    x = lambda v: L + (W - L - 70) * v / vmax  # noqa: E731
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" aria-label="{title}">',
        STYLE,
        f'<text class="t" x="10" y="16">{title}</text>',
    ]
    for i, name in enumerate(series):
        out.append(
            f'<rect x="{10 + i * 110}" y="25" width="10" height="9" fill="{COLORS[i % len(COLORS)]}"/>'
            f'<text x="{24 + i * 110}" y="34">{name}</text>'
        )
    for t in (0, vmax / 2, vmax):
        out.append(
            f'<line class="g" x1="{x(t):.0f}" x2="{x(t):.0f}" y1="{T}" y2="{H - 24}"/>'
            f'<text x="{x(t):.0f}" y="{H - 8}" text-anchor="middle">{fmt.format(t)}</text>'
        )
    for r, label in enumerate(rows):
        y0 = T + r * row + 4
        out.append(f'<text x="{L - 8}" y="{y0 + row / 2}" text-anchor="end">{label}</text>')
        for i, (name, vals) in enumerate(series.items()):
            v = vals[r]
            y = y0 + i * 11
            if v is None:
                out.append(f'<text x="{L + 4}" y="{y + 9}">нет данных</text>')
                continue
            out.append(
                f'<rect x="{L}" y="{y}" width="{max(1.5, x(v) - L):.1f}" height="9" rx="2" '
                f'fill="{COLORS[i % len(COLORS)]}"><title>{name}, {label}: {fmt.format(v)}</title></rect>'
                f'<text x="{x(v) + 4:.1f}" y="{y + 9}">{fmt.format(v)}</text>'
            )
    out.append("</svg>")
    return "\n".join(out)


def line_chart(title: str, xs: list[float], series: dict[str, list[float]], xlabel: str, ylabel: str) -> str:
    W, H, L, R, T, B = 680, 300, 60, 20, 44, 44
    ys = [v for s in series.values() for v in s]
    lo, hi = 0.0, max(ys) * 1.15
    x = lambda v: L + (W - L - R) * (v - xs[0]) / (xs[-1] - xs[0])  # noqa: E731
    y = lambda v: T + (H - T - B) * (1 - (v - lo) / (hi - lo))  # noqa: E731
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" aria-label="{title}">',
        STYLE,
        f'<text class="t" x="10" y="16">{title}</text>',
    ]
    for i, name in enumerate(series):
        out.append(
            f'<rect x="{10 + i * 260}" y="25" width="10" height="9" fill="{COLORS[(i + 1) % len(COLORS)]}"/>'
            f'<text x="{24 + i * 260}" y="34">{name}</text>'
        )
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        out.append(
            f'<line class="g" x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
            f'<text x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{v:.3f}</text>'
        )
    for v in xs:
        out.append(f'<text x="{x(v):.1f}" y="{H - B + 16}" text-anchor="middle">{v:g}</text>')
    out.append(f'<text x="{(L + W - R) / 2}" y="{H - 6}" text-anchor="middle">{xlabel}</text>')
    out.append(f'<text x="14" y="{T - 4}">{ylabel}</text>')
    for i, (name, vals) in enumerate(series.items()):
        pts = " ".join(f"{x(a):.1f},{y(b):.1f}" for a, b in zip(xs, vals, strict=True))
        c = COLORS[(i + 1) % len(COLORS)]
        out.append(f'<polyline points="{pts}" fill="none" stroke="{c}" stroke-width="2"/>')
        out += [
            f'<circle cx="{x(a):.1f}" cy="{y(b):.1f}" r="4" fill="{c}"><title>{name}: n={a:g}, {b:.4f}</title></circle>'
            for a, b in zip(xs, vals, strict=True)
        ]
    out.append("</svg>")
    return "\n".join(out)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # 1. стратегии: F2 против нашей метрики (mask-часть, train)
    strat = json.loads((ROOT / "results/metric_sensitivity/strategies.json").read_text())
    names = {
        "mask_all_tokens + oracle": "замаскировать всё",
        "mask_all_digits_and_capitals + oracle": "все числа и заглавные",
        "oracle_plus_all_digits + oracle": "эталон + все числа",
        "oracle_fio_surname_only + oracle": "ФИО без фамилии",
        "oracle_drop_КАРТА + oracle": "без класса «карта»",
        "baseline (regex + plain_replace)": "baseline",
        "oracle + oracle": "эталон",
    }
    rows = [r for r in strat if r["strategy"] in names]
    (OUT / "metric_vs_f2.svg").write_text(
        grouped_hbars(
            "Наша метрика (H·φ) против токенного F2 на эталонных стратегиях, train",
            [names[r["strategy"]] for r in rows],
            {"F2 (токены)": [r["F2"] for r in rows], "H·φ (pii-mask-v1)": [r["S_mask"] for r in rows]},
        ),
        encoding="utf-8",
    )
    # 2. шум метрики от размера выборки
    boot = json.loads((ROOT / "results/data/bootstrap_baseline.json").read_text())
    paired = json.loads((ROOT / "results/data/bootstrap_paired_probe.json").read_text())
    (OUT / "noise.svg").write_text(
        line_chart(
            "Шум метрики (бутстрэп по документам) от размера набора",
            [b["n"] for b in boot],
            {
                "SE score одного кандидата": [b["se"] for b in boot],
                "SE разности двух кандидатов (парно)": [p["diff_se"] for p in paired],
            },
            "примеров в наборе",
            "стандартная ошибка",
        ),
        encoding="utf-8",
    )
    # 3. recall по классам: синтетика (train) и реальные документы
    ta = json.loads((ROOT / "results/evolution/mask/train_analysis/summary.json").read_text())
    classes = list(ta["S01"]["recall_by_class"])
    series = {f"{c} train": [ta[c]["recall_by_class"][k] for k in classes] for c in ta}
    saved = ROOT / "results/real_check/recall_real.json"
    real = {}
    for cand in ("baseline", "S02", "S03"):
        f = ROOT / "operator/real_check/results" / cand / "report.json"
        if f.exists():
            real[cand] = json.loads(f.read_text())["recall_by_class"]
    if real:
        saved.write_text(json.dumps(real, ensure_ascii=False, indent=1) + "\n")
    elif saved.exists():
        real = json.loads(saved.read_text())
    for cand, rec in real.items():
        series[f"{'S01' if cand == 'baseline' else cand} реальные"] = [rec.get(k) for k in classes]
    (OUT / "recall_synthetic_vs_real.svg").write_text(
        grouped_hbars("Recall по классам: синтетика (train) и реальные документы (29, вне поиска)", classes, series),
        encoding="utf-8",
    )
    print(sorted(p.name for p in OUT.glob("*.svg")))


if __name__ == "__main__":
    main()
