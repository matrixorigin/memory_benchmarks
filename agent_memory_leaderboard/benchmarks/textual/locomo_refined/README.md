# LoCoMo-Refined

This directory owns the dataset parser (`dataset.py`), entrypoint
(`benchmark.py` / `run.sh`), downloaded data (`data/`), and run reports (`results/`).
The shared driver calls the configured Memoria-backed Add/Search endpoints.

```sh
# From the project root; download public data once:
mkdir -p benchmarks/textual/locomo_refined/data
curl -fL 'https://raw.githubusercontent.com/mem-eval-suite/LoCoMo_refined/887091190789e8d6760e70b9edd696539923dc4f/data/raw/locomo_refined.json' -o benchmarks/textual/locomo_refined/data/locomo_refined.json
# Verify against the same size/hash manifest used by Actions:
python3 - <<'PY'
import hashlib, json
from pathlib import Path
source = json.loads(Path('benchmarks/ci-datasets.json').read_text())['locomo']
path = Path('benchmarks/textual/locomo_refined/data/locomo_refined.json')
if path.stat().st_size != source['bytes'] or hashlib.file_digest(path.open('rb'), 'sha256').hexdigest() != source['sha256']:
    raise SystemExit('Dataset does not match the pinned Actions input')
print('Pinned dataset verified')
PY
# Pilot on one complete history:
./benchmarks/textual/locomo_refined/run.sh --limit 1
# Resume using exactly the same selection:
./benchmarks/textual/locomo_refined/run.sh --limit 1 --resume
```

Use `--output PATH` for another run. Existing reports are never overwritten
without `--resume`. The launcher loads `var/local.env` or `AML_ENV_FILE` for
endpoint authentication. Use `--adapter-url URL` for a different Add/Search
base URL. Data and results are ignored by Git.

Default reports measure evidence recall at Top K 100. Optional `--mode answer-eval`
adds local answer diagnostics; it is not an official AML score or native evaluator parity. All history is ingested before search; labels never enter Add/Search.
Records remain isolated by run and sample for diagnosis and resume.

Full dataset: `./benchmarks/textual/locomo_refined/run.sh --limit 10`.
The downloaded release contains 10 histories and 1,382 questions. The text-only
run skips 521 questions marked multimodal. Recall uses annotated dialogue IDs.

See [local evaluation](../../EVALUATION.md) for model settings, score definitions,
label-free source attribution, resume behavior and private-cache handling.
