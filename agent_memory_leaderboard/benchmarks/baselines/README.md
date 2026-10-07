# Recorded self-run baselines

These reports contain aggregate metrics, nonsecret configuration, source IDs and
synthetic probe observations. Full datasets, retrieved text, gold answers, model
predictions, private cache and credentials are excluded.

| Report | Evidence |
|---|---|
| [LoCoMo / Qwen Plus, 2026-10-07](2026-10-07-locomo-qwen-plus.json) | [Actions run](https://github.com/matrixorigin/memory_benchmarks/actions/runs/37594716119): one complete history, 87 scored text questions, 100% coverage; local judge 49/87, Recall@100 0.8023 |
| [Dev probes, 2026-10-07](2026-10-07-dev-probes.json) | Four source-time scenarios and 20 bounded Search calls, followed by separate owner-scoped Explain reads |

These are self-run diagnostics, not AML scores or native evaluator equivalents.
The judge and answer model both use the mutable `qwen-plus` alias. One history
does not represent the full benchmark or all AML capability dimensions. Gold
source presence does not guarantee sufficient answer context.

Reproduce the baseline with **Actions → AML benchmark**, `main`, `locomo`,
`answer-eval`, `histories=1`, `top_k=100`, and the recorded model/prompt/budget
configuration. Reproduction also requires the recorded deployed service versions;
the workflow always calls the current service and does not select a past image.
Use the [workflow guide](../CI.md), [pinned local data commands](../textual/locomo_refined/README.md)
and [design/acceptance plan](../../docs/competition-plan.md#干净端到端基线2026-10-07)
for details, diagnostic interpretation and pending work.
