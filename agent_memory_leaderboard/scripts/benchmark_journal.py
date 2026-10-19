"""Isolated journal benchmark with synthetic data; never contacts a backend.

Run with `uv run python scripts/benchmark_journal.py`. An optional injected
commit delay models slow storage but is not a measurement of any deployment.
Use --source-root to run this same harness against another source checkout.
"""

import argparse
import json
import logging
import math
import sqlite3
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace


class UnsupportedCommitError(RuntimeError):
    """The source checkout uses a commit path this harness cannot measure."""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--requests", type=int, default=64)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 16, 32, 64])
    parser.add_argument("--commit-delay-ms", type=float, default=0)
    args = parser.parse_args()
    if (
        args.requests < 1
        or any(c < 1 for c in args.concurrency)
        or not math.isfinite(args.commit_delay_ms)
        or args.commit_delay_ms < 0
    ):
        parser.error("counts must be positive and commit delay finite and nonnegative")
    sys.path.insert(0, str(args.source_root))
    from src.config import Settings
    from src.errors import AdapterError
    from src.journal import Journal
    from src.models import AddRequest
    from src.service import MemoryAdapter

    logging.disable(logging.CRITICAL)

    class Backend:
        def __init__(self):
            self.lock = threading.Lock()
            self.receipts = {}

        def store_idempotent(self, owner, body, marker):
            with self.lock:
                return self.receipts.setdefault((owner, marker), SimpleNamespace(memory_id=marker))

        def wait_for_visibility(self, owner, memory_id, marker):
            with self.lock:
                memory = self.receipts[owner, marker]
                assert memory.memory_id == memory_id
                return memory

    original_connect = sqlite3.connect
    for concurrency in args.concurrency:
        with tempfile.TemporaryDirectory(prefix="journal-benchmark-") as directory:
            settings = Settings(
                memoria_url="http://synthetic.invalid",
                memoria_master_key="synthetic",
                state_dir=Path(directory),
            )
            journal = Journal(settings.state_dir)
            backend = Backend()
            adapter = MemoryAdapter(settings, backend, journal)
            commits = 0
            commit_lock = threading.Lock()

            class Connection(sqlite3.Connection):
                def __exit__(self, exc_type, exc_value, traceback):
                    if exc_type is None and self.in_transaction:
                        # sqlite3's context-manager commit bypasses this subclass's
                        # commit() override, so neither its count nor delay is valid.
                        self.rollback()
                        raise UnsupportedCommitError(
                            "Benchmark aborted: implicit context-manager commits are not "
                            "measured. Use a source checkout with explicit db.commit() calls."
                        )
                    return super().__exit__(exc_type, exc_value, traceback)

                def commit(self):
                    nonlocal commits
                    if args.commit_delay_ms:
                        time.sleep(args.commit_delay_ms / 1000)
                    super().commit()
                    with commit_lock:
                        commits += 1

            def connect(*a, **kw):
                return original_connect(*a, factory=Connection, **kw)

            def add(index):
                request = AddRequest(
                    request_id=f"request-{index}",
                    user_id=f"user-{index}",
                    session_id="session",
                    messages=[{"role": "user", "content": "A synthetic fact for journal timing."}],
                )
                started = time.perf_counter()
                status = 200
                try:
                    adapter.add(request)
                except AdapterError as exc:
                    status = exc.status
                return status, (time.perf_counter() - started) * 1000

            sqlite3.connect = connect
            started = time.perf_counter()
            try:
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    results = list(pool.map(add, range(args.requests)))
            except UnsupportedCommitError as exc:
                parser.exit(1, f"{exc}\n")
            finally:
                elapsed = time.perf_counter() - started
                sqlite3.connect = original_connect
            latencies = sorted(ms for _, ms in results)
            statuses = {str(s): sum(code == s for code, _ in results) for s, _ in results}
            print(
                json.dumps(
                    {
                        "concurrency": concurrency,
                        "requests": args.requests,
                        "injected_commit_delay_ms": args.commit_delay_ms,
                        "statuses": statuses,
                        "commits": commits,
                        "unique_backend_records": len(backend.receipts),
                        "success_rps": round(statuses.get("200", 0) / elapsed, 3),
                        "p95_ms": round(latencies[math.ceil(len(latencies) * 0.95) - 1], 3),
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
