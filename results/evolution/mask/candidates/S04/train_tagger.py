"""Обучение и group-CV токен-теггера ФИО на train (офлайн-инструмент; в решение не входит).

Использование:
  python train_tagger.py cv     <train.jsonl> <S02-train-result.json> [--folds 4]   — group-CV по meta.families
  python train_tagger.py fit    <train.jsonl> <out weights.json>                    — обучение на всём train

Метка токена: 1 — токен внутри gold-спана ФИО; 0 — все остальные (в т.ч. прочие классы ПДн и ORG).
Модель — логистическая регрессия (AdaGrad, L2), чистый Python. Группы CV: семейства шаблонов `meta.families`,
разбитые детерминированно на K групп; тест фолда — примеры с любым семейством группы, обучение — примеры
без семейств группы (ни одного общего шаблона).
"""
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tagfeat import doc_features, tokenize  # noqa: E402

METRIC_DIR = "/workspace/cases/pii-masking-ru/campaigns/mask/metric"


def load(train_path: str):
    out = []
    for line in open(train_path, encoding="utf-8"):
        r = json.loads(line)
        inp = json.loads(r["input"])
        if inp["task"] != "mask":
            continue
        ref = json.loads(r["reference"])
        text = inp["text"]
        toks = tokenize(text)
        fio = [s for s in ref["spans"] if s["label"] == "ФИО"]
        y = []
        for t in toks:
            y.append(int(any(s["start"] <= t.start and t.end <= s["end"] for s in fio)))
        out.append({"id": r["id"], "text": text, "toks": toks, "y": y, "ref": ref, "raw": r,
                    "families": ref["meta"]["families"], "doc_type": ref["meta"]["doc_type"]})
    return out


def make_groups(examples, k: int) -> dict[str, int]:
    """Семейства -> группа 0..k-1: внутри (doc_type, часть) по кругу, детерминированно."""
    fams = sorted({f for e in examples for f in e["families"]})
    groups: dict[str, int] = {}
    c = 0
    for f in fams:  # сортировка по имени: doc.part.NN -> соседние шаблоны попадают в разные группы
        groups[f] = c % k
        c += 1
    return groups


def featurize(examples):
    return [doc_features(e["toks"]) for e in examples]


EPOCHS = int(os.environ.get('T_EPOCHS', 6))
L2 = float(os.environ.get('T_L2', 1e-2))
LR = float(os.environ.get('T_LR', 0.3))
MINF = int(os.environ.get('T_MINF', 2))


def fit(examples, epochs=EPOCHS, l2=L2, lr=LR, min_feat=MINF, seed=0, neg_weight=1.0):
    docs = featurize(examples)
    fc = Counter()
    for d in docs:
        for fs in d:
            fc.update(fs)
    index = {f: i for i, f in enumerate(sorted(f for f, c in fc.items() if c >= min_feat))}
    data = []
    for d, e in zip(docs, examples, strict=True):
        for fs, y in zip(d, e["y"], strict=True):
            data.append(([index[f] for f in fs if f in index], y))
    w = [0.0] * len(index)
    g = [1e-8] * len(index)
    rnd = random.Random(seed)
    for _ in range(epochs):
        rnd.shuffle(data)
        for idx, y in data:
            z = sum(w[i] for i in idx)
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            err = (p - y) * (1.0 if y else neg_weight)
            for i in idx:
                gi = err + l2 * w[i]
                g[i] += gi * gi
                w[i] -= lr * gi / math.sqrt(g[i])
    weights = {f: round(w[i], 4) for f, i in index.items() if abs(w[i]) >= 0.02}
    return {"weights": weights}


def predict(model, e):
    w = model["weights"]
    out = []
    for fs in doc_features(e["toks"]):
        z = sum(w.get(f, 0.0) for f in fs)
        out.append(1 / (1 + math.exp(-max(-30, min(30, z)))))
    return out


def _fold(args):
    train_ex, test_ex = args
    model = fit(train_ex)
    return [(e["id"], predict(model, e)) for e in test_ex], len(model["weights"])


def s02_masked_chars(example, answer_str):
    sys.path.insert(0, METRIC_DIR)
    import pii_metric as pm
    pol = pm.load_policy()
    return pm.reconstruct(example["text"], answer_str, pol)


def simulate(examples, probs, s02_answers, thr):
    """Метрика на объединении S02-спанов и токенов теггера (p>=thr). Возвращает агрегат метрики пакета."""
    sys.path.insert(0, METRIC_DIR)
    import pii_metric as pm
    pol = pm.load_policy()
    stats = []
    for e, p in zip(examples, probs, strict=True):
        text = e["text"]
        ints = pm.reconstruct(text, s02_answers[e["id"]], pol)
        masked = [False] * len(text)
        for a, b, _ in ints:
            for i in range(a, b):
                masked[i] = True
        spans = [(a, b, lbl) for a, b, lbl in ints]
        pred = [pi >= thr for pi in p]
        add = []
        for t, ok in zip(e["toks"], pred, strict=True):
            if ok and not any(masked[t.start:t.end]):
                add.append(t)
        # слить соседние токены (разделитель — пробел, точка, дефис в одной строке)
        merged = []
        for t in add:
            if merged and all(c in " .-" for c in text[merged[-1][1]:t.start]) and text[merged[-1][1]:t.start] != "":
                merged[-1][1] = t.end
            else:
                merged.append([t.start, t.end])
        for a, b in merged:
            spans.append((a, b, "ФИО"))
        spans.sort()
        # ответ
        out, reps, pos, cnt = [], [], 0, Counter()
        for a, b, lbl in spans:
            if a < pos:
                continue
            cnt[lbl] += 1
            ph = f"[{lbl}_{cnt[lbl]}]"
            out.append(text[pos:a])
            out.append(ph)
            reps.append({"placeholder": ph, "original": text[a:b]})
            pos = b
        out.append(text[pos:])
        ans = json.dumps({"masked_text": "".join(out), "replacements": reps}, ensure_ascii=False)
        ex = {"input": e["raw"]["input"], "reference": e["raw"]["reference"]}
        stats.append(pm.score_example(ex, ans, pol)["statistics"])
    return pm.aggregate(stats, pol)["statistics"]


def surname_stems(e) -> set[str]:
    """Основы фамилий-подобных токенов gold-ФИО (не имя из словаря, не отчество): служат только для разбиения CV."""
    from names_ru import is_first_name
    return {t.low[:5] for t, y in zip(e["toks"], e["y"], strict=True)
            if y and len(t.text) >= 4 and not is_first_name(t.text) and not t.patr}


def stem_group(stem: str, k: int) -> int:
    import hashlib
    return int(hashlib.sha1(stem.encode()).hexdigest(), 16) % k


def main():
    mode = sys.argv[1]
    examples = load(sys.argv[2])
    if mode == "fit":
        model = fit(examples)
        Path(sys.argv[3]).write_text(json.dumps(model, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print("weights", len(model["weights"]))
        return
    res = json.load(open(sys.argv[3]))
    s02 = {r["example_id"]: r["answer"] for r in res["examples"]}
    k = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    groups = make_groups(examples, k)
    folds = []
    by_name = mode == "cvname"
    for e in examples:
        e["stems"] = surname_stems(e)
    for g in range(k):
        if by_name:
            hit = lambda e: any(stem_group(s, k) == g for s in e["stems"])  # noqa: E731
        else:
            hit = lambda e: any(groups[f] == g for f in e["families"])  # noqa: E731
        test = [e for e in examples if hit(e)]
        train = [e for e in examples if not hit(e)]
        folds.append((train, test))
        print(f"fold {g}: train {len(train)} test {len(test)}")
    with Pool(4) as pool:
        results = pool.map(_fold, folds)
    thrs = [float(x) for x in os.environ.get("T_THRS", "0.03,0.05,0.1,0.2,0.3").split(",")]
    # пары (пример, вероятности) по всем фолдам: каждый тестовый пример оценён моделью, не видевшей его шаблонов
    pairs = []
    held = []  # (p, y) токенов фамилий-подобных слов held-out группы (только для cvname)
    for g, ((_, test), (preds, _)) in enumerate(zip(folds, results, strict=True)):
        pr = dict(preds)
        for e in test:
            pairs.append((dict(e, id=e["id"]), pr[e["id"]]))
            if by_name:
                for t, y, p in zip(e["toks"], e["y"], pr[e["id"]], strict=True):
                    if y and len(t.text) >= 4 and t.low[:5] in e["stems"] and stem_group(t.low[:5], k) == g:
                        held.append(p)
    if by_name:
        print("held-out surname tokens:", len(held), " recall@thr:",
              {thr: round(sum(p >= thr for p in held) / len(held), 4) for thr in (0.05, 0.1, 0.2, 0.3, 0.5)})
    print("pooled (example, fold) pairs:", len(pairs))
    zero = simulate([p[0] for p in pairs], [[0.0] * len(p[1]) for p in pairs], s02, 2.0)
    print("  S02 only: ФИО", round(zero["recall_by_class"]["ФИО"], 4), "P", round(zero["precision"], 4),
          "score", round(zero["recall_class_hmean"] * zero["precision_penalty"], 4))
    for thr in thrs:
        st = simulate([p[0] for p in pairs], [p[1] for p in pairs], s02, thr)
        print(f"  thr {thr}: ФИО {st['recall_by_class']['ФИО']:.4f} P {st['precision']:.4f} "
              f"score {st['recall_class_hmean'] * st['precision_penalty']:.4f}")
    Path("/tmp/t/cv_pairs.json").write_text(json.dumps([[e["id"], p] for e, p in pairs]))


if __name__ == "__main__":
    main()
