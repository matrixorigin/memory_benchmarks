# SWEContextBench

Public upstream data: [Hugging Face](https://huggingface.co/datasets/jiayuanz3/SWEContextBench).
Native evaluator: [GitHub](https://github.com/jiayuanz3/SWEContextBench).

The standalone runner tests **historical-issue retrieval through Memoria Add/Search**.
It does not generate code patches, run repository tests, or report coding success.
AML lists SWEContextBench as a reference catalog entry, not its current scored
coding suite. Results are not official AML scores.

Three full public parquet files are downloaded in `data/`: 1,007 experience rows,
362 related task rows and 362 relationship rows. `prepare.py` checks SHA256 hashes
against the public Hugging Face LFS manifest before conversion. It produces 40
repository scopes covering every related task. Scopes include repositories named
by upstream relationships when those differ from the target repository name.
The one experience overlapping a target task (`django__django-13658`) is excluded
from every memory pool. Gold patches, test patches, hints and test expectations
never enter Add or Search; only historical issue statements and source dates are
ingested. Related issue statements are queries; relationship IDs remain offline
as retrieval relevance labels. The complete supplied experience pool is used,
without imposing an additional chronological cutoff.

From the project root:

```sh
uv run --with pyarrow python -m benchmarks.coding.swecontextbench.prepare
./scripts/run-local.sh benchmark --kind swecontextbench --all --top-k 100 \
  --output benchmarks/coding/swecontextbench/results/full-run.json
```

Use `--resume` to resume the same report without changing its contract. The
standalone entrypoint is `python -m benchmarks.coding.swecontextbench.benchmark`
and uses the same common runner. Source parquet files and generated data/results
are gitignored. `data/preparation.json` records provenance and field exclusions.

Native evaluation additionally requires a coding agent to solve each related
issue using retrieved experience, generate `{instance_id}_preds.json` containing
`model_patch`, and run the upstream Docker test harness. The upstream
`evaluation.sh` was inspected, not executed. Docker availability was verified
(`linux/aarch64`), but native task images and generated patches are not prepared.
A retrieval score cannot substitute for the native patch success rate.
