# Benchmark datasets

- **Textual Datasets** (`textual/`): conversational and long-term text memory.
- **Coding Datasets** (`coding/`): memory for software-engineering tasks.

Each dataset owns its parser, benchmark entrypoint, launcher, documentation,
ignored `data/`, and ignored `results/`. Shared endpoint transport and reporting
live in `src/benchmark.py`.

GitHub Actions: [AML benchmark usage and result interpretation](CI.md).
The manual workflow supports pinned LoCoMo-Refined and LongMemEval-S pilots,
publishes a run summary, and saves reports/checkpoints as artifacts.
