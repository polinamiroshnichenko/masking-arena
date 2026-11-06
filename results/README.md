# Результаты замеров (сырые)

| Путь | Что | Команда |
|---|---|---|
| `baseline/train/` | ответы, статистика по примерам, отчёт baseline на train | README → «Как воспроизвести» |
| `baseline/public_score.json` | только общий public-score baseline, в т.ч. по кампаниям mask / unmask (подробности — у оператора) | `tools/evaluate.py` на `operator/data/public.jsonl`; по кампаниям — после `tools/select_task.py` |
| `metric_sensitivity/` | эталонные и жульнические стратегии, сравнение с F1/F2 | `tools/reference_strategies.py` |
| `data/train_stats.json` | статистика генерации train (покрытие классов семействами, частоты) | `generate.py` (stderr) |
| `data/bootstrap_baseline.json` | SE score baseline от размера набора | `tools/bootstrap.py` |
| `data/bootstrap_paired_probe.json` | SE парной разности baseline и пробы | `tools/bootstrap.py --paired` |
| `probes/robust_placeholders/` | проба (не кандидат кампании): baseline + нормализация искажённых меток; train 0.344 | — |
| `arena_smoke_metric.txt` | freeze/verify пакета и сверка оценки с кодом арены | `tools/arena_smoke.py` |
| `arena_smoke_runner.txt` | baseline через Docker-образ runner арены | `tools/runner_smoke.sh` |
