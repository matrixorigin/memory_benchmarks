# CLBench

Official public source: https://huggingface.co/datasets/tencent/CL-bench

`data/CL-bench.jsonl` contains all 1,899 released tasks. Run with
`bash benchmarks/textual/clbench/run.sh --limit 1899` after registry integration.

The final user message is the Search query. Earlier user/assistant messages are
Add history; system instructions and rubrics are never added. The official AML
pipeline explicitly keeps reference documents within the question:
https://github.com/AML-memory/agent-memory-leaderboard/blob/main/data/clbench/pipeline.py

There are 1,278 single-turn tasks with no preceding conversation: these perform
Search only. The other 621 tasks exercise Add and Search. Documents embedded in a
single-turn query are not silently moved into memory or heuristically split.
There are no released evidence IDs, so evidence recall is undefined. Native
rubric scoring requires answer generation plus a rubric judge and is not computed
by this retrieval-only adapter.

Download: `curl -L --fail https://huggingface.co/datasets/tencent/CL-bench/resolve/main/CL-bench.jsonl -o benchmarks/textual/clbench/data/CL-bench.jsonl`
