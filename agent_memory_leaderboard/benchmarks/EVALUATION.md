# Local evaluation and diagnostics

## Modes and scope

The default `retrieval` mode calls only Add/Search. `answer-eval` adds an
OpenAI-compatible answer call and a separate dataset-aware judge call for
LoCoMo-Refined and LongMemEval-S. Both modes are local diagnostics: neither
consumes AML Full quota nor produces an official AML score.

The self-run judge uses the complete, pinned upstream prompts from
[LoCoMo-Refined](https://github.com/mem-eval-suite/LoCoMo_refined/tree/887091190789e8d6760e70b9edd696539923dc4f)
and [LongMemEval](https://github.com/xiaowu0162/LongMemEval/tree/9e0b455f4ef0e2ab8f2e582289761153549043fc).
LoCoMo uses the unmodified `refined` template, one request for each acceptable
gold candidate, and the maximum candidate score. LongMemEval uses its original
category-specific `get_anscheck_prompt`, including abstention. Sources, revisions,
prompt hashes, request options, aggregation and the scorer version are recorded
in the evaluation contract. The HTTP client retains bounded retries/timeouts
rather than copying upstream's runtime: LoCoMo upstream retries evaluation errors
up to ten times, while this client retries transient transport/status failures
up to four times and leaves malformed JSON pending for explicit resume. Compare
coverage separately when failures occur. Vendored material and its attribution/license notices
are under `src/judges/`.

Use Qwen3-14B for the LoCoMo-Refined judge. LongMemEval's pinned evaluator supports
`gpt-4o-2024-08-06`, `gpt-4o-mini-2024-07-18`, and
`meta-llama/Meta-Llama-3.1-70B-Instruct`; select and record the intended model.
A different configured model produces a warning and
`judge_model_matches_upstream=false` in each evaluation. Model aliases do not
verify the weights deployed by a provider.

Official prompts alone do **not** reproduce the complete native benchmark:
retrieval/chunking, answer generation, context selection, lexical metrics and
provider runtime remain this client's implementation. Reports therefore retain
`native_evaluator_equivalent=false`. Use a fixed answer model, judge model and
dataset for comparisons. Official AML validation is separate acceptance work.

"Local" means a self-run evaluation, including GitHub Actions; it does not
require a locally deployed Memoria service. The workflow and CLI share this
evaluator. AML's public documentation describes a platform-controlled Answer/Eval
flow, but the complete official judge prompts and a reproducible implementation
were not found in the documentation checked on 2026-10-07. The prompts in
`src/judges/` come from the public benchmark repositories, not from AML.
See the [official documentation](https://agentmemories.ai/zh-cn/docs) and
[competition readiness plan](../docs/competition-plan.md#正式参评前的工作).

## Opt-in local content diagnostics

Content diagnostics run **in the self-run benchmark client**, on the machine
running that client. They do not add request-body logging to the deployed adapter,
Memoria, or its journal. Official Smoke/Full calls do not pass through this client
and must not be used as the source of diagnostic content. Normal benchmark runs
leave the new feature disabled.

From `agent_memory_leaderboard/`, this one command produces a local demonstration
without contacting an adapter, model provider, or downloading tokenizer data:

```sh
uv run --locked --extra evaluation python -m scripts.content_diagnostics_demo \
  --output-dir var/content-demo
```

Open the `local_report` HTML path printed at the end. This demonstration uses
the independently authored fixture in `examples/content-diagnostics/`, simulated
adapter/model responses, and a byte-counting test tokenizer. Its results verify
the tool, **not Memoria quality or an AML score**. Choose another output directory
for each run. Optional `--scenario missing-evidence`, `budget-exclusion`,
`answer-failure` and `judge-failure` exercise different diagnostic signals;
the latter two intentionally return benchmark exit code 1 with unscored questions.

To evaluate that synthetic fixture against an actual test adapter:

```sh
# Set ADAPTER_API_KEY in the process environment if required by that adapter.
uv run --locked python -m src.benchmark --kind locomo \
  --dataset examples/content-diagnostics/input.json \
  --adapter-url http://127.0.0.1:8000 --limit 1 --top-k 100 \
  --output var/content-real/run.json --content-diagnostics \
  --diagnostic-source examples/content-diagnostics/source.json
```

This is `retrieval` mode: no Answer/Judge calls are made, and the pages explicitly
say those stages did not run. To include local Answer/Eval, configure the existing
`BENCHMARK_ANSWER_*` and `BENCHMARK_JUDGE_*` settings and add `--mode answer-eval`.
The existing model-service authorization and dataset transmission rules apply.

The source declaration is required before any diagnostic run. Its format is
demonstrated by `examples/content-diagnostics/source.json`:

- `source_type` must be `synthetic` or `public`; official and unknown sources are
  rejected. Official interface/database/log-derived copies remain prohibited even
  if similar text is publicly downloadable elsewhere.
- Record the independently obtained source URI/version, license or self-authored
  local-use basis, allowed local evaluation/content-diagnostic uses, and retention
  of 1–30 days. `dataset_sha256` must match the exact input file bytes.
- Explicitly attest `independently_obtained`, `no_official_material`, and
  `no_sensitive_content`. These declarations bind the reviewed input; **they do
  not prove a license or detect misdeclared official/private data**. The operator
  must independently verify permission, privacy and model-transmission conditions.
- Do not put credentials, private endpoints, configuration, or personal details
  in the declaration. Unknown declaration fields are not copied to the report.

The new directory is `<private-dir>/<run-id>/content-diagnostics/`; by default
`private-dir` is `.private` beside the ordinary output JSON. It contains an
`index.html`, per-question pages, shared Add pages, a versioned `manifest.json`,
and atomic gzip JSON records. Add history is recorded once per batch and linked
from questions. Each question shows its actual query, ordered validated returned
evidence, selected/excluded ranks, actual local model messages/outputs when run,
existing metrics, errors, and retries. Prepared-but-unacknowledged Add records
are explicitly not proof that a request was sent. Successful request/response
snapshots come from the execution path, not later database reconstruction.

No invented judge explanation or hidden model reasoning is requested. When the
judge provides only a label, the page states that no explanation was supplied.
For multiple gold candidates, all candidate inputs and outputs are retained;
the per-question score is their maximum, rather than the final displayed label.
Source mapping ambiguity, missing annotated sources, budget exclusion and model
failure are diagnostic signals, not automatic root-cause findings.

The defaults capture the first 100 questions and allow at most 64 MiB **for the
new diagnostic directory**. Set `--diagnostic-max-questions` (1–1000) and
`--diagnostic-max-mib` (2–512). Size accounting includes gzip records and HTML;
individual uncompressed snapshots are bounded too. Reaching a limit omits content
and explicitly marks incompleteness; it does not skip benchmark questions or
silently improve scoring coverage. Existing answer caches, normal benchmark
outputs, the input dataset and backend storage are outside this directory's cap.
The readable pages are derived views and repeat some question/model text; this
is bounded, not a claim of zero duplication.
Content capture adds client disk work; use the payload-free load harness for
capacity measurements. Search latency is sampled before diagnostic content I/O.

Files use owner-only permissions. Known credential environment values, common
token/auth patterns and endpoint URLs are masked in the new diagnostic display;
HTML is escaped and has no scripts or external dependencies. Raw execution hashes
and source/recall measurement remain separate from the redacted display. This is
defense in depth, **not a general personal-data detector**: sensitive input is not
eligible for this feature. Existing private answer caches retain their existing
behavior; do not share them or treat them as sanitized diagnostic files.

The deadline starts with the diagnostic directory's creation and is not extended
by resume. Expired captures cannot resume or be compared. Review `expires_at` and
remove the dedicated local directory by that deadline; this does not schedule an
automatic cleanup job or delete backend memories/the journal. Keep declaration,
limits, dataset and evaluation configuration unchanged for `--resume`. Resuming
pending model work reuses the previous Search evidence and answer cache; a missing
snapshot is disclosed rather than rebuilt from current database contents.
A missing manifest requires a new run instead of restarting the retention clock.
Pending model resume checks cached evidence against the captured content hashes;
signature-verified answer-cache reuse is explicitly identified in the page.

For comparable runs, create a new local paired report:

```sh
uv run --locked python -m src.content_diagnostics \
  var/before/.private/<run-id>/content-diagnostics \
  var/after/.private/<run-id>/content-diagnostics \
  --output var/comparison.html
```

The HTML and adjacent JSON preserve per-question evidence-rank hashes, metrics,
missing/unscored questions and full-run coverage. Dataset bytes, selected
histories, Top K, mode and evaluator/model contracts must match. Code/backend
changes are allowed; `runner_source_sha256` records the runner's actual source
files, and the ordinary run's runtime label should identify the tested backend.
Local-model comparisons are not equivalent to the platform's Answer/Eval.

Content files are not uploaded to GitHub or CI artifacts. The benchmark workflow
uses an explicit metadata-only artifact allowlist. Use ignored `var/` or a local
directory outside the repository; custom `--private-dir` paths remain the
operator's responsibility. Retain/share independently obtained public content
only as permitted by its license and privacy requirements.

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

Before experimenting with redaction, rewriting or multi-message storage chunks,
replace this attribution method with a verified private mapping from stable
backend memory IDs to wire request/message/part identities and their source-unit
sets. A merged chunk may have several sources. Establish stable IDs and mapping
lifecycle/recovery first; never embed those labels in model-visible content or
extend the official Search response with diagnostic fields. This mapping is
planned work, not implemented by the current manifest.

LoCoMo's two human speakers have no assistant/user role annotation. Both use
`user`, with original speaker names retained in content; this does not identify
one speaker as an AI assistant. Explicit roles in other datasets are preserved.

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
reported as an evaluation skip without calling answer/judge models, not scored as a failure.

### Metrics and budget

- **Token F1**: token overlap precision/recall harmonic mean for LoCoMo; 0–1,
  higher is better. Case-insensitive word/punctuation tokenization.
- **BLEU-1**: clipped unigram precision with a brevity penalty for LoCoMo; 0–1,
  higher is better. A short answer missing reference information is penalized.
- **Local judge accuracy**: mean binary correctness under the pinned upstream
  prompt and configured model; 0–1, higher is better. LME abstention, knowledge update, preference and
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
Answer output length has a separate limit (default 1024); temperature is zero.
Judge settings follow upstream: LoCoMo has no explicit output-token override and
requests `enable_thinking=false`; LongMemEval uses `n=1` and `max_tokens=10`.
If a provider explicitly rejects `enable_thinking` as unsupported, it is removed
and the fallback is recorded as `thinking_option_unsupported`. Other 400/422
responses do not trigger this fallback.

Prefix selection uses binary search with memoized full-request token counts,
reducing encoding passes from O(N) to O(log N) for N retrieved records. Each
candidate includes all prompt fields and the selected result is checked as a
complete input; this avoids assuming that BPE counts are additive per record.

### Model failures and scoring coverage

Answer and judge calls each allow at most four transient HTTP attempts. LoCoMo's
explicit unsupported-thinking-option fallback can add one HTTP request. Transport errors,
429 and 5xx use exponential backoff (0.5, 1, 2 seconds); numeric `Retry-After`
on 429 is capped at 30 seconds per delay. Other HTTP errors are not retried.
Safe per-question attempt diagnostics include phase, attempt, HTTP status or
transport marker, duration and retry flag, without provider bodies or credentials.

LoCoMo parses upstream's JSON `label: CORRECT/WRONG` format, including fenced
objects. LongMemEval parses `yes/no`. The previous custom `correct: true/false`
format and abbreviated rubrics are no longer used. Label decisions also follow
upstream: LoCoMo treats a parsed object whose label is not `CORRECT` as zero;
LongMemEval checks whether `yes` appears in the response. These are upstream
behaviors, not newly tightened validation rules. Invalid JSON and malformed
response envelopes fail evaluation. Answer output must be nonblank and finish
normally; Judge text is parsed regardless of finish reason, matching upstream.
Exhausted transient failures and budget overflow also fail the affected question.
It remains `evaluation_pending` with `answer_failed` or `judge_failed`; later
questions continue. Authentication/endpoint errors (401/403/404/405) stop the run
early because they normally affect every question. Storage and programming
errors also remain run failures.

Any remaining pending question makes the final status `partial` with exit code 1.
Summary reports eligible/scored/pending questions, scoring coverage and answer/
judge failure counts. Coverage is successful judge scores divided by eligible
evaluation questions; multimodal and missing-gold skips are excluded. Means use
only available values: a valid answer can contribute Token F1/BLEU-1 even when
its judge fails. Failures are neither silently dropped from coverage nor scored
as incorrect answers. Interpret means with coverage; partial runs are not clean
complete baselines.

With the same contract and private cache, `--resume` skips completed questions,
reuses Search results for pending ones and reuses a saved successful answer when
retrying its judge. This avoids repeated Add/Search and answer calls. Interrupted
calls whose result was not saved may still repeat. Retry, parser and context
selection versions are recorded in the evaluation contract; pre-hardening
Answer/Eval checkpoints do not match this contract and require a fresh run or
their original runner. Changing this scorer version likewise prevents resuming
an old Answer/Eval run with mixed scoring rules. Retrieval-only checkpoint
compatibility is unchanged. Rejudging only previously incorrect predictions is a
subset audit; do not report a new full-run accuracy by mixing old and new judges.

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

The current adapter reason recognizer is a conservative status/text allowlist;
changed or unknown text becomes unclassified. A machine-readable adapter error
code is separate contract work, rather than an implicit wire-schema change here.

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

## Offline regression checks

```sh
uv sync --locked --extra evaluation
uv run --no-sync pytest -q
uv run --no-sync ruff check src tests benchmarks scripts/benchmark_ci.py
```

Dedicated tests cover evidence attribution/ambiguity and UTF-8 parts, ranked
budget selection, malformed/fenced verdicts, bounded model retries, partial-run
continuation and resume, checkpoint recovery, torn request logs, lexical metrics,
Summary coverage and probe safety/long-request accounting. Model and adapter
responses use in-process mocks and deterministic local tokenization; these tests
do not establish a live embedding/model baseline or actual SLB capacity.
