"""Run with python -m benchmarks.textual.longmemeval_s.benchmark from the project root."""

from src.benchmark import main

if __name__ == "__main__":
    main(
        default_kind="longmemeval",
        default_output="benchmarks/textual/longmemeval_s/results/run.json",
    )
