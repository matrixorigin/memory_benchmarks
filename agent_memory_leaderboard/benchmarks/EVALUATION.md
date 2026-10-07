# Local evaluation and diagnostics

## Modes and scope

The default `retrieval` mode calls only Add/Search. `answer-eval` adds an
OpenAI-compatible answer call and a separate dataset-aware judge call for
LoCoMo-Refined and LongMemEval-S. Both modes are local diagnostics: neither
consumes AML Full quota nor produces an official AML score.

The local judge uses versioned, independently maintained rubrics inspired by
[LoCoMo-Refined](https://github.com/mem-eval-suite/LoCoMo_refined/tree/887091190789e8d6760e70b9edd696539923dc4f)
and [LongMemEval](https://github.com/xiaowu0162/LongMemEval/tree/9e0b455f4ef0e2ab8f2e582289761153549043fc).
It does **not** reproduce those repositories' native evaluator prompts, output
handling or model runtimes. Reports set `native_evaluator_equivalent=false`.
Use a fixed answer model, judge model and dataset for comparisons; choosing
Qwen3-14B alone does not make this local judge equivalent to the refined judge.
Native evaluation and official AML validation remain separate acceptance work.

## Label-free ingestion and evidence attribution

Only the original message text, speaker, role and source date enter Add. No
`[dialogue:ID]`, evidence label, answer-source ID, gold answer, summary or rubric
is added to message content. LongMemEval session evidence IDs become neutral
local `S1`, `S2`, etc.; these IDs are also kept out of model-visible content.
Search receives the original question, user ID and Top K only.

`run.sources/` contains a local manifest of source IDs and exact content SHA256
hashes. It includes each part produced by the adapter's shared UTF-8 renderer,
so splitting a long message does not lose attribution. The manifest has no
message body or gold answers and is never sent to the adapter or answer model.
The wire Add/Search schemas are unchanged.

Attribution requires an exact content match. Identical text from multiple
source units is ambiguous and receives no source credit; duplicate turns from
the same LongMemEval session still identify that session. Rewritten, summarized,
or otherwise unmatched returned content is uncredited. Reports expose ambiguous
and unmapped counts so low recall is not automatically blamed on retrieval.
This mapping targets this repository's content-preserving Memoria adapter;
another memory system may need a separate auditable attribution implementation.
A matching fragment establishes partial source visibility, not that every fact in
the original message is available to Answer; answer correctness remains separate.

Runner contract version 2 uses new run-scoped users. Version 1 labeled runs
remain readable but cannot be resumed by this runner: their ingestion contract
differs. Do not compare the new quality baseline directly with labeled scores.
Malformed source gold labels remain disclosed and unchanged.

## Local commands

From `agent_memory_leaderboard/`:

```sh
uv sync --locked --extra evaluation
# ADAPTER_API_KEY authenticates against the already running adapter.
uv run --no-sync python -m src.benchmark --kind locomo \
  --dataset benchmarks/textual/locomo_refined/data/locomo_refined.json \
  --adapter-url http://127.0.0.1:8000 --limit 1 --top-k 100 \
  --output var/evaluation/retrieval.json
```

For Answer/Eval, copy [evaluation.env.example](evaluation.env.example) to a
private, ignored path such as `var/evaluation.env`, fill it, and load it into the
process environment. Both answer and judge settings are explicit; no credentials
are taken from the adapter's backend or forwarded between providers.

```sh
set -a
. ./var/evaluation.env
set +a
uv run --no-sync python -m src.benchmark --kind locomo \
  --dataset benchmarks/textual/locomo_refined/data/locomo_refined.json \
  --adapter-url http://127.0.0.1:8000 --limit 1 --top-k 100 \
  --mode answer-eval --private-dir var/evaluation-private \
  --output var/evaluation/answers.json
# Resume with the same command, configuration and dataset, adding --resume.
```

Use `--kind longmemeval` with its dataset path for LongMemEval-S. Gold comes
from the downloaded dataset; no handwritten gold is required. Gold is used only
by the scorer/judge, never by Add, Search or the answer model. LongMemEval's
question date is supplied to the answer model when present. Missing gold is
reported as an evaluation skip, not scored as a failure.

### Metrics and budget

- **Token F1**: token overlap precision/recall harmonic mean for LoCoMo; 0–1,
  higher is better. Case-insensitive word/punctuation tokenization.
- **BLEU-1**: clipped unigram precision with a brevity penalty for LoCoMo; 0–1,
  higher is better. A short answer missing reference information is penalized.
- **Local judge accuracy**: mean binary correctness under the local dataset
  rubric; 0–1, higher is better. LME abstention, knowledge update, preference and
  temporal questions have distinct rules. It is not a native/public leaderboard score.
- **Within-budget evidence recall**: source recall using only the ranked prefix
  actually passed to the answer model; 0–1, higher is better.

The default answer input budget is 117760, counting serialized instructions,
roles, question, optional options/date and retrieved evidence with the configured
`tiktoken` encoding, plus a 256-token safety margin. Evidence is retained as a
whole-record ranked prefix: the first oversized record ends selection; lower
records are not substituted and no answer-aware selection is performed.
The default encoding is `o200k_base`. This is a fixed **local accounting rule**,
not a claim about every provider's tokenizer/chat template or AML's frozen
adapter. A Qwen model may tokenize differently. Report provider prompt usage
when available; a reported over-budget request fails evaluation. That check is
after the provider call and does not guarantee avoidance of billing or over-limit
requests. Confirm the actual model tokenizer/template before formal comparisons.
Output length has a separate limit (default 1024); temperature is zero.

### Checkpoints and privacy

During execution, `run.json` is a small header. Each processed question is saved
atomically in `run.questions/`; final reports also include all question records
for existing readers. Resume and CI Summary merge sidecars after a hard stop.
Question records use private permissions and atomic replacement. Torn final
request-log appends are repaired on resume; completed question files are reused.

`run.requests.jsonl` records each HTTP attempt: route, attempt number, status,
allowlisted reason code, elapsed time and hashed request identity. It contains no
headers, payload, arbitrary response text or exception message. The original
six-attempt, route-specific retry policy and Add identity are unchanged. Summary
shows intermediate retries as well as terminal failures; `unclassified` is not
a diagnosis of the failure's cause.

Answer/Eval keeps retrieved content and generated answers in a separate **private
cache** to resume after model/judge failures. Locally the default is `.private/`
under the output directory; prefer an explicit ignored `--private-dir`. Do not
upload this directory. CI uses `var/ci-benchmark/private/`, outside its artifact
root. Artifacts contain answer hashes and scores, not generated answer text or
retrieved bodies. Keep both private cache and backend/journal state within the
run's retention policy; artifact expiry does not erase them. A crash before a
model response is durably saved may incur another model call on resume.

## Source-time regression tool

The tool produces a plan without contacting any service:

```sh
uv run --no-sync python -m src.probes source-time \
  --output var/probes/time-plan.json
```

Run it against a local/dev adapter with a fresh output path:

```sh
uv run --no-sync python -m src.probes source-time \
  --adapter-url http://127.0.0.1:8000 --execute \
  --output var/probes/time-run.json
```

It writes only synthetic, UUID-scoped evidence with actual AML millisecond
`timestamp` fields. Cases cover old-only, recent-only, mixed old/recent and dates
either side of the diagnostic T1 confidence cutoff. Queries include lexical,
semantic and exact source text. `--reference-time` accepts a timezone-aware
ISO8601 anchor to make requested dates reproducible; it does not freeze the
backend clock. The threshold assumes T1=.95, lambda=1/365 and minimum=.05 and
must be checked against the deployed backend configuration.

Optional `--explain` needs `MEMORIA_URL`, `MEMORIA_MASTER_KEY` and the same
`MEMORIA_NAMESPACE` as the adapter. It performs owner-scoped retrieval directly
and stores only allowlisted path flags/counts/timings. This is a separate read
and may differ from the earlier public Search; it does not prove that both calls
used the same path. Missing evidence is recorded for diagnosis rather than
asserted to be a universal decay bug. No decay or timestamp semantics are changed.

## Capacity and long HTTP requests

```sh
# Plan first; no traffic:
uv run --no-sync python -m src.probes capacity --route search \
  --concurrency 2 --qps 2 --requests 20 --output var/probes/load-plan.json
# Execute the agreed workload against local/dev, with a fresh output:
uv run --no-sync python -m src.probes capacity --route search \
  --concurrency 2 --qps 2 --requests 20 --execute \
  --output var/probes/load-run.json
```

`--route add` measures repeated unique Add requests. `--messages-per-add` and
`--message-bytes` control synthetic input size. Limits cap concurrency, request
count and input bytes; the scheduling duration does not cancel an in-flight Add.
QPS limits logical calls, not internal retries or Memoria/model operations.
Search mode first seeds the scoped user; reports label seed traffic separately.
Per-route P50/P95, successful/failed counts and retry details are client-side
measurements, not maximum sustainable capacity. Collect Pod/DB/model metrics
separately. Estimated input bytes are not measured physical storage growth.

`--timeout` can allow a real request longer than five minutes. The report marks
`long_request_verified=true` only after an individual successful HTTP attempt
exceeds 300 seconds. Multiple short retries do not establish that the SLB accepts
one long request. A fast run leaves this field false; the tool does not force the
backend to hold a request or claim the SLB timeout has been validated.

Probe users and writes are retained for inspection. The tool never deletes data,
resets journals, deploys services or runs automatically on PRs. Coordinate actual
load and retention before using `--execute` against shared dev infrastructure.
