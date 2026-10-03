"""Run historical-issue retrieval through Add/Search, not native coding evaluation."""

from src.benchmark import main

if __name__ == "__main__":
    main(default_kind="swecontextbench",
         default_output="benchmarks/coding/swecontextbench/results/run.json")
