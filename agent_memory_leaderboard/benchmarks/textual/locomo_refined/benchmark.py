"""Run with python -m benchmarks.textual.locomo_refined.benchmark from the project root."""

from src.benchmark import main

if __name__ == "__main__":
    main(default_kind="locomo", default_output="benchmarks/textual/locomo_refined/results/run.json")
