# LongMemEval-S

This directory owns the dataset parser (`dataset.py`), entrypoint
(`benchmark.py` / `run.sh`), downloaded data (`data/`), and run reports (`results/`).
The shared driver calls the configured Memoria-backed Add/Search endpoints.

```sh
# From the project root; download public data once:
mkdir -p benchmarks/textual/longmemeval_s/data
curl -fL 'https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json' -o benchmarks/textual/longmemeval_s/data/longmemeval_s_cleaned.json
# Pilot on one complete history:
./benchmarks/textual/longmemeval_s/run.sh --limit 1
# Resume using exactly the same selection:
./benchmarks/textual/longmemeval_s/run.sh --limit 1 --resume
```

Use `--output PATH` for another run. Existing reports are never overwritten
without `--resume`. The launcher loads `var/local.env` or `AML_ENV_FILE` for
endpoint authentication. Use `--adapter-url URL` for a different Add/Search
base URL. Data and results are ignored by Git.

Default reports measure evidence recall at Top K 100. Optional `--mode answer-eval`
adds local answer diagnostics; it is not an official AML score or native evaluator parity. All history is ingested before search; labels never enter Add/Search.
Records remain isolated by run and sample for diagnosis and resume.

The full release has 500 histories, one question per history; use `--limit 500`
for a full run. It is much larger than LoCoMo. Recall uses unique evidence
session IDs. Abstention questions are retained with undefined retrieval recall;
they require answer generation and judging to assess abstention quality.

See [local evaluation](../../EVALUATION.md) for model settings, score definitions,
label-free source attribution, resume behavior and private-cache handling.
