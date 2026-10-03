"""Replay the exact five failed requests, retaining Add idempotency IDs."""

import argparse
import json
import os
import time
from pathlib import Path

import httpx

from src.benchmark import batches, save
from src.datasets import ADAPTERS, normalize
from src.models import AddResponse, SearchResponse
from src.smoke import call_adapter
from src.suite import CATALOG


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind")
    parser.add_argument("--output", type=Path, default=Path("state/failure-regressions.json"))
    args = parser.parse_args()
    output = args.output
    results = {}
    headers = {"Authorization": "Bearer " + os.environ["ADAPTER_API_KEY"]}
    with httpx.Client(timeout=1800, trust_env=False) as http:
        for name, directory, kind, filename in CATALOG:
            if kind in {None, "locomo"}:
                continue
            if args.kind and kind != args.kind:
                continue
            report = json.loads(Path(f"benchmarks/{directory}/results/{filename}").read_text())
            if report["status"] != "failed":
                continue
            adapter = ADAPTERS[kind]
            source = Path(adapter.DEFAULT_PATH)
            data = (
                adapter.load(source) if hasattr(adapter, "load") else json.loads(source.read_text())
            )
            route, body = None, None
            for sample in normalize(data, kind):
                user = f"{kind}:{report['run_id']}:{sample['sample_id']}"
                if report["stage"] == "add" and report["current"].startswith(user + ":"):
                    for session, index, messages in batches(sample["conversation"]):
                        rid = f"{user}:{session}:{index}"
                        if rid == report["current"]:
                            route = "/add"
                            body = dict(
                                request_id=rid, user_id=user, session_id=session, messages=messages
                            )
                            break
                elif report["stage"] == "search":
                    for i, question in enumerate(sample["qa"]):
                        if (
                            question.get("id", f"{sample['sample_id']}#q{i:04d}")
                            == report["current"]
                        ):
                            route = "/search"
                            body = dict(
                                user_id=user,
                                query=question["question"],
                                top_k=report["contract"]["top_k"],
                            )
                            break
                if body is not None:
                    break
            if body is None:
                raise ValueError(f"Cannot reconstruct failed request for {name}")
            start = time.monotonic()
            row = {
                "route": route,
                "request": report["current"],
                "previous_http_status": report["error"].get("http_status"),
            }
            try:
                response = call_adapter(
                    http, report["contract"]["adapter_url"], headers, route, body
                )
                if route == "/add":
                    result = AddResponse.model_validate(response)
                    assert response.get("success") is True
                    assert all(
                        getattr(result, k) == body[k]
                        for k in ("request_id", "user_id", "session_id")
                    )
                else:
                    result = SearchResponse.model_validate(response)
                    assert len(result.data) <= body["top_k"]
                    row["returned"] = len(result.data)
                row.update(status="passed", http_status=200)
            except Exception as exc:
                row.update(status="failed", error_type=type(exc).__name__)
                if isinstance(exc, httpx.HTTPStatusError):
                    row["http_status"] = exc.response.status_code
            row["seconds"] = time.monotonic() - start
            results[name] = row
            save(output, results)
            print(json.dumps({name: row}), flush=True)
    raise SystemExit(0 if all(r["status"] == "passed" for r in results.values()) else 1)


if __name__ == "__main__":
    main()
