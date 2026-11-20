"""Разбор train-результатов кандидатов (файлы `evaluator.train result`): recall по классам, precision,
пропущенные и лишние фрагменты с примерами, график сравнения.

    uv run python tools/train_analysis.py --result S01=path/S01-train.json --result S02=path/S02-train.json \
        --out results/evolution/mask/train_analysis

Train открыт исследователю, поэтому разбор и примеры по нему можно публиковать (данные синтетические).
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "case" / "metric"))
sys.dont_write_bytecode = True

from pii_metric import aggregate, load_policy, reconstruct, tokens  # noqa: E402

COLORS = ["#9aa5b1", "#0f5c6e", "#c8902a", "#6b56b8"]


def misses(example: dict, policy: dict) -> tuple[list[tuple[str, str]], list[str]]:
    inp, ref = json.loads(example["input"]), json.loads(example["reference"])
    text = inp["text"]
    try:
        iv = reconstruct(text, example["answer"], policy)
    except Exception:
        iv = []
    covered = [False] * len(text)
    for a, b, _ in iv:
        for i in range(a, b):
            covered[i] = True
    missed = [(s["label"], text[s["start"]:s["end"]]) for s in ref["spans"]
              if not all(covered[i] for i in range(s["start"], s["end"]) if text[i].isalnum())]
    gold = [False] * len(text)
    for s in ref["spans"] + ref.get("ignore", []):
        for i in range(s["start"], s["end"]):
            gold[i] = True
    extra = [text[a:b] for a, b in tokens(text) if all(covered[a:b]) and not any(gold[a:b])]
    return missed, extra


def bars_svg(names: list[str], recalls: dict[str, dict[str, float]]) -> str:
    classes = list(next(iter(recalls.values())).keys())
    W, row, L, T = 640, 26, 130, 34
    H = T + row * len(classes) + 30
    x = lambda v: L + (W - L - 60) * v  # noqa: E731
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" aria-label="recall по классам">',
           "<style>text{font:12px system-ui,sans-serif;fill:#4a5563}.g{stroke:#e3e7ec}</style>",
           '<text x="10" y="16" font-weight="600">Recall по классам на train (полный набор)</text>']
    for k, n in enumerate(names):
        out.append(f'<rect x="{L + k * 90}" y="22" width="10" height="8" fill="{COLORS[k % 4]}"/>'
                   f'<text x="{L + 14 + k * 90}" y="30">{n}</text>')
    for t in (0, 0.5, 1):
        out.append(f'<line class="g" x1="{x(t):.0f}" x2="{x(t):.0f}" y1="{T}" y2="{H - 26}"/>'
                   f'<text x="{x(t):.0f}" y="{H - 10}" text-anchor="middle">{t}</text>')
    bh = (row - 6) / len(names)
    for i, c in enumerate(classes):
        y0 = T + i * row + 3
        out.append(f'<text x="{L - 8}" y="{y0 + row / 2}" text-anchor="end">{c}</text>')
        for k, n in enumerate(names):
            v = recalls[n].get(c, 0.0)
            out.append(f'<rect x="{L}" y="{y0 + k * bh:.1f}" width="{max(1, x(v) - L):.1f}" height="{bh - 1:.1f}" '
                       f'rx="2" fill="{COLORS[k % 4]}"><title>{n} {c}: {v:.3f}</title></rect>')
        out.append(f'<text x="{x(max(recalls[n].get(c, 0) for n in names)) + 4:.0f}" y="{y0 + row / 2}">'
                   + " / ".join(f"{recalls[n].get(c, 0):.2f}" for n in names) + "</text>")
    out.append("</svg>")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", action="append", required=True, metavar="NAME=PATH")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    policy = load_policy()
    a.out.mkdir(parents=True, exist_ok=True)
    summary, recalls, md = {}, {}, []
    for item in a.result:
        name, path = item.split("=", 1)
        r = json.loads(Path(path).read_text(encoding="utf-8"))
        ex = r["examples"]
        agg = aggregate([e["statistics"] for e in ex], policy)
        st = agg["statistics"]
        recalls[name] = st["recall_by_class"]
        miss_by = collections.Counter()
        miss_ex = collections.defaultdict(list)
        extra_c = collections.Counter()
        for e in ex:
            m, x = misses(e, policy)
            for lbl, frag in m:
                miss_by[lbl] += 1
                if len(miss_ex[lbl]) < 4:
                    miss_ex[lbl].append(frag.replace("\n", "⏎"))
            extra_c.update(x)
        summary[name] = {"examples": len(ex), "score": agg["score"], **{k: st[k] for k in (
            "recall_class_hmean", "recall_macro", "precision", "invalid_mask")},
            "recall_by_class": st["recall_by_class"], "missed_by_class": dict(miss_by),
            "top_extra_tokens": extra_c.most_common(15)}
        md += [f"## {name}: score {agg['score']:.4f} · H {st['recall_class_hmean']:.3f} · "
               f"P {st['precision']:.3f} · невалидных {st['invalid_mask']}", "",
               "| Класс | recall | пропущено сущностей | примеры пропусков |", "|---|---|---|---|"]
        for c, v in st["recall_by_class"].items():
            md.append(f"| {c} | {v:.3f} | {miss_by.get(c, 0)} | {'; '.join(f'`{f}`' for f in miss_ex[c])} |")
        md += ["", "Чаще всего лишне замаскированные токены: "
               + ", ".join(f"`{t}`×{n}" for t, n in extra_c.most_common(12)), ""]
    (a.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (a.out / "recall_by_class.svg").write_text(bars_svg(list(recalls), recalls), encoding="utf-8")
    (a.out / "report.md").write_text("# Разбор train-результатов\n\n![recall](recall_by_class.svg)\n\n"
                                     + "\n".join(md), encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
