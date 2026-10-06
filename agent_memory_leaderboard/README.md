# Memoria endpoint benchmarks

This project tests public memory datasets against Memoria-backed Add/Search
endpoints. Benchmarks are grouped into **Textual Datasets** (`benchmarks/textual/`) and
**Coding Datasets** (`benchmarks/coding/`). Each dataset has a standalone directory:

- [LoCoMo-Refined](benchmarks/textual/locomo_refined/README.md): dialogue evidence recall.
- [ScriptMem](benchmarks/textual/scriptmem/README.md): original conversations omitted upstream.
- [LongMemEval-Refined](benchmarks/textual/longmemeval_refined/README.md): exact release unverified.
- [LongMemEval-S](benchmarks/textual/longmemeval_s/README.md): session evidence recall.
- [CLBench](benchmarks/textual/clbench/README.md): context-learning endpoint diagnostics.
- [PersonaMem-v2](benchmarks/textual/personamem_v2/README.md): 32k text-split endpoint diagnostics.
- [BEAM](benchmarks/textual/beam/README.md): all four public history-length variants.
- [CAMBench Coding](benchmarks/coding/cambench/README.md): corpus/verifier unreleased.
- [SWEContextBench](benchmarks/coding/swecontextbench/README.md): historical-issue evidence recall.

Each directory owns its parser, runner entrypoint, data, and results. Shared
HTTP validation, bounded retries, checkpointing, and reporting live in
`src/benchmark.py`; `src/datasets.py` registers the dataset adapters. Astra is
optional for future answer/judge stages; endpoint retrieval tests require only
the Add/Search service and its Memoria backend.

Implements `POST /add` and `POST /search` from the
[Agent Memory Leaderboard API guide](https://agentmemories.ai/api-guide).
Supports the **Textual and Coding** tracks. Ordered text-only content arrays are
accepted; image parts are rejected with HTTP 422.

## Run

Requires Python 3.12+, Linux/macOS, and a running Memoria HTTP server supporting
`Memoria-Owner` authentication, `extra_metadata`, and `/v1/memories/query` (as in
the adjacent `memoria` checkout). Configure Memoria's database and embedding
provider before using this adapter. A Memoria master key is required; an ordinary
personal API key cannot provide isolation for multiple leaderboard users.

```sh
uv sync --locked
export MEMORIA_URL=http://127.0.0.1:8100
export MEMORIA_MASTER_KEY='your-memoria-master-key'
export ADAPTER_API_KEY='a-separate-leaderboard-access-key'
uv run uvicorn src.app:create_app --factory --host 0.0.0.0 --port 8000
```

The `.env.example` lists all settings. Environment files are not loaded
automatically. `ADAPTER_API_KEY` accepts `Authorization: Bearer <key>`,
`Authorization: Token <key>`, or `X-Api-Key: <key>`. Omitting it disables adapter
authentication for local development/public smoke. Deploy behind HTTPS for the
formal evaluation. Interactive endpoint documentation is at `/docs`.

`GET /health` is unauthenticated and returns HTTP 200 with `{"status":"ok"}`.
It checks adapter liveness, not Memoria/database/embedding readiness.

`GET /ready` checks journal readability and the upstream Memoria health endpoint,
without writing memories or invoking a model. It is a dependency-availability
check, not evidence that Add/Search or the embedding provider works correctly.
The deployment target is the Aliyun dev unit cluster, using a dedicated Memoria
backend and database login. Local evaluation remains available.
See [competition design](docs/competition-plan.md) for runtime scope and workload
budgets. The offline CI checks code and builds the adapter and Memoria images
without publishing or deploying them.
The [dev deployment workflow](../deploy/README.md) defaults to building Memoria
from its `main` branch; another branch, tag, commit, or existing image can be selected.
It publishes the selected images and deploys both services by digest through an
independent Pulumi stack, recording the actual source commit for each source build.
Public access requires DNS; the existing cert-manager ClusterIssuer automatically
issues and renews the TLS Secret.

## Contract verification

Checked against the [English integration contract](https://agentmemories.ai/docs#doc-en-contract)
on 2026-10-03 for Textual/Coding: synchronous Add, exact ID echoes, user-only
isolation, original queries, optional options, ranked evidence, `top_k`, empty
results, authentication, and health. Multimodal remains outside the selected scope.

For submission, use public endpoint URLs without embedded credentials; the
localhost examples below are for development. Enable authentication for Full.
Configure proxy/load-balancer request timeouts for the documented 30-minute
request window. `MEMORIA_TIMEOUT_SECONDS` controls each upstream call (default
120 seconds), not the total Add duration. Live Memoria retrieval and the platform
Smoke test still need deployment verification.

```sh
curl http://localhost:8000/add \
  -H "Authorization: Bearer $ADAPTER_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"chunk-1","user_id":"alice","session_id":"session-1","messages":[{"role":"user","timestamp":1704067200000,"content":"I work on the moon."}]}'

curl http://localhost:8000/search \
  -H "Authorization: Bearer $ADAPTER_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"alice","query":"Where do I work?","top_k":100}'
```

Add returns HTTP 200 with `success: true` and the three original IDs. Search
returns `{"data": [{"id": "...", "content": "...", "score": 0.9, "created_at": "..."}]}`;
optional values are omitted when unavailable. No matches returns `{"data": []}`.

## Behavior

- Each exact `user_id` is mapped with SHA-256 and `MEMORIA_NAMESPACE` into a
  Memoria owner ID. IDs are never trimmed, so long and Unicode IDs remain
  distinct. Outbound requests use `Memoria-Owner` with `X-User-Id`, and responses
  are checked for the expected owner. Older servers fail closed.
- Messages are written sequentially with their role and optional UTC timestamp
  included in the evidence. Large messages are split at UTF-8 boundaries to fit
  Memoria's 32 KiB limit. Session, message/part order, timestamp, and retry markers
  are kept in metadata. Each chunk has its own `subject_id` so Memoria's
  near-duplicate handling does not supersede other evidence chunks.
- Add waits for every synchronous Memoria write, a read-back of its active
  record, and a retrieval probe that returns that record before reporting
  success. If retrieval has not caught up, Add returns 503; retrying checks the
  existing record without duplicating it. No background write queue is used.
- Search forwards the original text query to `/v1/memories/search` without a
  session filter. It preserves ranking, removes repeated IDs, and returns at
  most `top_k` evidence records. Memoria caps retrieval at 100; larger requests
  may therefore return fewer records. Options are accepted but are not used to
  generate answers or written into memory.

## Retries and deployment state

`ADAPTER_STATE_DIR` (default `./var`) contains the SQLite request journal and
process-safe lock files. Preserve this directory across restarts and mount the
same **local** directory for every worker. Separate hosts/independent state
directories and network filesystems are not supported. Keep the Memoria URL,
namespace, and adapter version stable during a run. Never delete active lock
files or the journal to recover a request.

Idempotency is keyed by user and `request_id`. Exact retries reuse completed
writes; changed payloads return HTTP 400, and concurrent copies return HTTP 409. Partial requests
resume from the last confirmed chunk. If a write response is lost, the adapter
looks up its persisted metadata marker instead of sending it again.

Memoria currently has no atomic request-id write API. A timeout or crash with no
discoverable record has an ambiguous outcome: the adapter returns HTTP 503 and
keeps checking on retries, rather than risk a duplicate write. If Memoria never
committed, that request needs operator reconciliation after confirming the old
write cannot still complete. This also applies to a crash between journaling
the attempt and sending it. An upstream atomic idempotency API would be needed
to retry such cases automatically with an exactly-once guarantee. Definite
connection failures and pre-write rejections remain safely retryable.

Error handling follows the [AML error reference](https://agentmemories.ai/docs#doc-en-errors):

- Invalid requests return 422 with structured field details; reusing a request ID
  with a changed payload returns 400. Neither is automatically retryable.
- Invalid adapter credentials return 401; Memoria policy/scope denials return
  403. Unknown adapter routes return 404; unsupported methods return 405.
- An Add already in progress returns 409. Memoria conflicts also remain 409;
  Search conflicts are not automatically retryable.
- Memoria validation rejections remain 400/422; temporary 408/425/429/503/504
  statuses are preserved. Rate-limited writes can retry without getting stuck
  in ambiguous-write recovery. Numeric `Retry-After` hints are forwarded, capped
  at 60 seconds.
- Memoria authentication, routing, malformed responses and other upstream
  failures return 502. These indicate an adapter/backend issue, rather than a
  bad participant key or endpoint. Upstream 524 timeouts become 504. Unexpected
  adapter failures return 500; unavailable journal storage returns 503.

Business errors use `{"detail":{"reason":"..."}}`; 422 errors use a `detail`
array containing `loc`, `msg`, and `type`. Responses do not relay upstream bodies,
exception text, credentials, or validation input values. A rejected write is
never reported as success. The local smoke client uses the documented Add/Search
retry status sets and transport retries, bounded to six attempts with the same
payload. It does not retry Search 409/524 or malformed successful responses.

## Verify

```sh
uv run pytest -q
uv run ruff check src tests benchmarks
```

Tests exercise the endpoints against a simulated Memoria HTTP transport,
including isolation, restart/concurrent retries, partial writes, lost replies,
read-after-write checks, ranking, UTF-8 chunking, validation, and authentication.
They do not require a live database or embedding service.

## Local Memoria integration

`./astra-local.sh --astra-dir /path/to/astra health` uses the checkout's
`target/debug/astra` binary with the `local` profile and this project's working
directory for MCP discovery. Put `--astra-dir PATH` before Astra's arguments,
or set `ASTRA_BIN` to an executable path; the argument takes precedence.
Relative paths are resolved from the caller's directory. There is no default
checkout path. The API defaults to `http://127.0.0.1:17001` and the state directory
to `$HOME/.astra-local`; override these with `ASTRA_API_URL` (or `--api-url`)
and `ASTRA_LOCAL_STATE_ROOT` respectively.
`ASTRA_STATE_ROOT` remains a fallback alias when `ASTRA_LOCAL_STATE_ROOT` is unset
or empty. `health` checks the existing server; `serve` starts
one with the supplied server environment. The wrapper does not load the
adjacent Astra checkout's `.env`. The retrieval benchmark driver runs separately
through `./scripts/run-local.sh benchmark`.

The current local setup uses the Memoria 0.5.2 binary built from the adjacent
checkout and the MatrixOne connection settings supplied in
`~/workspace/astra/.env`. The legacy URL names `aml_memoria`, but the current
Memoria CLI automatically selects its multi-database topology and routes each
benchmark owner into a separate database. The shared registry is shared
infrastructure and must not be deleted during benchmark cleanup. Astra's source
configuration was not changed.

The generated `var/local.env` is ignored and mode 0600. It contains the selected
runtime settings and a separate generated adapter key; never commit it.
Settings were copied from Astra, so later edits to Astra's `.env` are not loaded
automatically. To start the services in separate terminals and run the live test:

```sh
./scripts/run-local.sh memoria  # http://127.0.0.1:8100
./scripts/run-local.sh adapter  # http://127.0.0.1:8000
./scripts/run-local.sh smoke
```

The smoke command talks over real HTTP to both services, uses fresh disposable
users, checks Add/Search, isolation, session coverage, retry deduplication and
Top K, and deactivates its test memories on exit. Deactivation is not physical
deletion and does not satisfy the retention obligation by itself. It can also target another
configured deployment with `python -m src.smoke --adapter-url URL`.

**Current limitation:** Astra's supplied `MEMORIA_EMBEDDING_BASE_URL` is empty.
The running Memoria therefore uses full-text retrieval without an embedding
provider. One full live smoke run passed, but repeated runs showed intermittent
missing full-text results immediately after writes (including at `top_k=1`).
This setup is not yet validated for leaderboard evaluation. Supply the embedding
endpoint associated with the configured key, set `EMBEDDING_BASE_URL` in the
private runtime file, restart Memoria, and rerun the live check. Do not assume
that configuring embeddings alone resolves the observed failure without testing.

## Public LoCoMo-Refined retrieval test

The complete nine-dataset catalog follows the
[AML benchmark documentation](https://agentmemories.ai/docs#doc-en-benchmarks).
Inspect saved results or run/resume all available datasets:

```sh
./scripts/run-local.sh suite
./scripts/run-local.sh suite --run
```

The suite writes `state/suite-results.json`. It skips completed and running
reports, preserves checkpoints for failures, and records missing public releases
explicitly. A failed ingestion stops that dataset; the suite continues to the
next dataset. `--run` exits nonzero if any catalog entry is incomplete. A report
marked running after a process crash must be resumed with the individual runner's
`--resume` option. Do not start two writers against the same report.
These are endpoint retrieval diagnostics, not native answer or coding scores.

Run the downloaded public dataset through the local adapter's `/add` and
`/search` endpoints:

```sh
./benchmarks/textual/locomo_refined/run.sh --limit 10
# Resume the same run after an interruption or service failure:
./benchmarks/textual/locomo_refined/run.sh --limit 10 --resume
```

The entrypoint is `benchmarks/textual/locomo_refined/benchmark.py`. It defaults to all ten conversations and
Top K 100; `--conversations 1` selects the first complete history for a pilot.
Use `--dataset PATH` or `--adapter-url URL` to override the defaults. Credentials
come from the same private environment as the local adapter. The source dataset
is `data/raw/locomo_refined.json` in
[LoCoMo-Refined](https://github.com/mem-eval-suite/LoCoMo_refined).

Only original dialogue text, speakers, source dates, and dialogue IDs enter Add.
Questions retain their original wording; answers and evidence labels remain
outside ingestion and retrieval. The report measures the fraction of annotated
dialogue IDs present in retrieved evidence, with undefined recall for questions
without evidence labels. Questions marked multimodal are skipped. This is a
text-only retrieval diagnostic, not answer accuracy or an official AML score;
it does not invoke Astra's answer model or an LLM judge.

The private JSON report records the dataset hash, isolated run users, successful Add
chunks, evidence IDs and content hashes, query latency, and failures. Retrieved
payloads are used in memory for scoring, without duplicate content logs. Each history is fully
ingested before its questions run. Failed ingestion stops the run, and partial
results must not be treated as full-dataset scores. Resume reuses identical IDs
and payloads; keep the backend, adapter configuration, and dataset unchanged.
Run-scoped memories are retained for diagnosis and resume. Do not delete them
while resuming that run or resume past its deletion deadline. Raw downloaded data and reports stay under each benchmark’s ignored
`data/` and `results/` directories. Local chunking uses up to 20 messages or 2,000 whitespace-counted words
(an individually oversized message stays intact); it does not claim parity
with AML's frozen dataset adapter.

## Data handling

Follow the [AML security policy](https://agentmemories.ai/docs#doc-en-security):
use evaluation data only for the current job, preserve exact user isolation,
keep service credentials out of reports, limit access, and delete data and
derived copies within 30 days after completion. Dataset payloads are unchanged;
no credential-redacted variant is enabled. Public local datasets stay on this
machine; only required history chunks and original queries reach Add/Search.
Gold answers and relevance labels remain outside those endpoints.

Data and results directories use mode 0700 and their files use mode 0600. Reports
record `privacy.delete_by` and `privacy.cleanup_after` (one day earlier), and
resume never extends an existing deadline. A daily thread follow-up checks these
deadlines. Physical cleanup must include Memoria data, snapshots and derived
copies: the current Delete APIs only deactivate records. No physical cleanup has
been claimed complete. Local loopback endpoints are for development; AML's public
endpoint validation applies before any formal hosted evaluation.

Backend fixes and live reproduction outcomes are recorded locally in
`state/runtime-fixes.json`, `state/failure-regressions.json`, and
`state/retrieval-fix-smoke.json`. A resumed report's `attempts` identify backend
revision changes; pre-fix observations remain distinguishable.

## Docker deployment with Memoria

`compose.adapter.yaml` is an overlay for the Memoria repository's own Compose
file, as described in its `skills/deployment/SKILL.md`. It attaches the adapter
to Memoria's network and persists the retry journal in a named volume:

```sh
export AML_ADAPTER_DIR="$PWD"
export ADAPTER_API_KEY='your-separate-adapter-key'
docker compose --env-file /path/to/memoria/.env \
  -f /path/to/memoria/docker-compose.yml \
  -f "$AML_ADAPTER_DIR/compose.adapter.yaml" up -d --build
```

Use a populated Memoria deployment `.env`, including its database and embedding
configuration. This starts the upstream Compose database too; do not run it
against an already occupied database port without configuring the upstream
deployment appropriately. The overlay publishes the adapter on loopback port
8000 for a reverse proxy. The Compose configuration was validated; the Docker
images have not been built or exercised in this local integration.

`requirements.lock` is exported from `uv.lock` for the adapter image. Regenerate
it after dependency changes with:

```sh
uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file requirements.lock
```
