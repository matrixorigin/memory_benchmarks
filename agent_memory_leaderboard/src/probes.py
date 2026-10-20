"""Plan or execute isolated source-time and bounded HTTP capacity probes."""

import argparse
import hashlib
import json
import math
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from .checkpoint import save
from .config import Settings
from .diagnostics import RetryAudit
from .evaluation import endpoint
from .models import AddResponse, SearchResponse
from .privacy import apply_policy
from .service import digest
from .smoke import call_adapter


def time_cases(anchor):
    # Diagnostic boundaries for T1=.95, lambda=1/365, minimum=.05.
    # Actual backend configuration and retrieval fallback determine the outcome.
    cutoff = -365 * math.log(0.05 / 0.95)
    dates = {
        "old": anchor - timedelta(days=1500),
        "recent": anchor,
        "before_cutoff": anchor - timedelta(days=cutoff - 2),
        "after_cutoff": anchor - timedelta(days=cutoff + 2),
    }
    return {name: int(value.timestamp() * 1000) for name, value in dates.items()}


def safe_explain(data):
    allowed_paths = {"vector", "hybrid", "fulltext", "graph", "graph+vector", "none"}
    result = {}
    if data.get("path") in allowed_paths:
        result["path"] = data["path"]
    for key in (
        "vector_attempted",
        "vector_hit",
        "fulltext_attempted",
        "fulltext_hit",
        "graph_attempted",
        "graph_hit",
        "result_count",
        "graph_candidates",
        "embedding_ms",
        "vector_ms",
        "fulltext_ms",
        "graph_ms",
        "total_ms",
    ):
        value = data.get(key)
        if isinstance(value, (bool, int, float)) and math.isfinite(value):
            result[key] = value
    return result


def run(args):
    os.umask(0o077)
    if args.output.exists() or args.output.with_suffix(".requests.jsonl").exists():
        raise ValueError("Choose a fresh output for every probe or plan")
    adapter_url = endpoint(args.adapter_url)
    anchor = (
        datetime.fromisoformat(args.reference_time).astimezone(UTC)
        if (args.reference_time)
        else datetime.now(UTC)
    )
    if args.reference_time and datetime.fromisoformat(args.reference_time).tzinfo is None:
        raise ValueError("Reference time must include a timezone")
    report = {
        "mode": args.mode,
        "execute": args.execute,
        "adapter_url": adapter_url,
        "reference_time": anchor.isoformat(),
        "concurrency": args.concurrency,
        "qps": args.qps,
        "requests": args.requests,
        "records": [],
        "long_request_verified": False,
        "official_aml_score": False,
    }
    if args.mode == "source-time":
        report["source_timestamps_ms"] = time_cases(anchor)
        report["scenarios"] = ["old-only", "recent-only", "mixed", "threshold"]
    else:
        report.update(
            messages_per_add=args.messages_per_add,
            message_bytes=args.message_bytes,
            maximum_add_message_bytes=(args.requests * args.messages_per_add * args.message_bytes),
        )
    if not args.execute:
        report["status"] = "plan-only"
        save(args.output, report)
        print(json.dumps(report, indent=2))
        return 0
    key = os.getenv("ADAPTER_API_KEY")
    if not key:
        raise ValueError("Set ADAPTER_API_KEY before executing probes")
    settings = Settings.from_env() if args.explain else None
    report.update(run_id=uuid.uuid4().hex, status="running", users=[])
    apply_policy(report)
    audit = RetryAudit(args.output.with_suffix(".requests.jsonl"))
    audit_lock, gate_lock = threading.Lock(), threading.Lock()
    next_request = [0.0]
    long_request = [False]
    started = time.monotonic()
    save(args.output, report)

    def call(path, body, identity):
        with gate_lock:
            now = time.monotonic()
            if now - started > args.duration or next_request[0] - started > args.duration:
                return {"route": path, "skipped": "scheduling_deadline"}, None
            delay = max(0, next_request[0] - now)
            if delay:
                time.sleep(delay)
            next_request[0] = time.monotonic() + 1 / args.qps
        if time.monotonic() - started > args.duration:
            return {"route": path, "skipped": "scheduling_deadline"}, None
        observer = audit.observer(hashlib.sha256(identity.encode()).hexdigest())

        def observe(event):
            with audit_lock:
                observer(event)
                if event["status"] == 200 and event["elapsed_seconds"] > 300:
                    long_request[0] = True

        begin = time.monotonic()
        try:
            with httpx.Client(
                timeout=httpx.Timeout(
                    args.timeout, connect=min(30, args.timeout), pool=min(30, args.timeout)
                ),
                trust_env=False,
            ) as client:
                result = call_adapter(
                    client,
                    adapter_url,
                    {"Authorization": f"Bearer {key}"},
                    path,
                    body,
                    observe=observe,
                )
            if path == "/add":
                validated = AddResponse.model_validate(result)
                if validated.success is not True or any(
                    getattr(validated, field) != body[field]
                    for field in ("user_id", "session_id", "request_id")
                ):
                    raise ValueError("Invalid Add acknowledgement")
            else:
                result = SearchResponse.model_validate(result)
            return {"route": path, "ok": True, "seconds": time.monotonic() - begin}, result
        except Exception as exc:
            record = {
                "route": path,
                "ok": False,
                "seconds": time.monotonic() - begin,
                "error_type": type(exc).__name__,
            }
            if isinstance(exc, httpx.HTTPStatusError):
                record["http_status"] = exc.response.status_code
            return record, None

    def explain(user, query):
        if not settings:
            return None
        with httpx.Client(
            timeout=httpx.Timeout(
                args.timeout, connect=min(30, args.timeout), pool=min(30, args.timeout)
            ),
            trust_env=False,
        ) as client:
            response = client.post(
                settings.memoria_url + "/v1/memories/search",
                headers={
                    "Authorization": f"Memoria-Owner {settings.memoria_master_key}",
                    "X-User-Id": digest([settings.namespace, user]),
                },
                json={"query": query, "top_k": 100, "explain": "analyze"},
            )
            response.raise_for_status()
            return safe_explain(response.json()["explain"])

    def add(user, messages, identity):
        return call(
            "/add",
            {"request_id": identity, "user_id": user, "session_id": "probe", "messages": messages},
            identity,
        )

    try:
        if args.mode == "source-time":
            dates = report["source_timestamps_ms"]
            scenarios = {
                "old-only": ["old"],
                "recent-only": ["recent"],
                "mixed": ["old", "recent"],
                "threshold": ["before_cutoff", "after_cutoff"],
            }
            for scenario, names in scenarios.items():
                user = f"aml-time:{report['run_id']}:{scenario}"
                report["users"].append(user)
                save(args.output, report)  # Record ownership before any attempted write.
                messages = [
                    {
                        "role": "user",
                        "timestamp": dates[name],
                        "content": f"Orion probe {name} launch code is violet-{name}.",
                    }
                    for name in names
                ]
                record, _ = add(user, messages, user + ":add")
                report["records"].append(record | {"scenario": scenario})
                save(args.output, report)
                if not record.get("ok"):
                    continue
                queries = [
                    "Orion",
                    "Which launch code was recorded earlier?",
                    messages[0]["content"],
                ]
                for index, query in enumerate(queries):
                    record, result = call(
                        "/search",
                        {
                            "user_id": user,
                            "query": query,
                            "top_k": 100,
                        },
                        f"{user}:q{index}",
                    )
                    if result is not None:
                        record["hit_sources"] = [
                            name
                            for name in names
                            if any(
                                messages[names.index(name)]["content"] in item.content
                                for item in result.data
                            )
                        ]
                        record["explain"] = explain(user, query)
                    report["records"].append(record | {"scenario": scenario, "query_index": index})
                    save(args.output, report)
        else:
            user = f"aml-capacity:{report['run_id']}"
            report["users"].append(user)
            save(args.output, report)
            message = "Orion capacity probe. " + "x" * (args.message_bytes - 22)
            messages = [{"role": "user", "content": message}] * args.messages_per_add
            if args.route == "search":
                seed, _ = add(user, messages, user + ":seed")
                report["records"].append(seed | {"seed": True})
                save(args.output, report)
                if not seed.get("ok"):
                    raise ValueError("Capacity seed Add failed")

            def request(index):
                if args.route == "add":
                    record, _ = add(user, messages, f"{user}:add:{index}")
                else:
                    record, _ = call(
                        "/search",
                        {"user_id": user, "query": "Orion", "top_k": 100},
                        f"{user}:search:{index}",
                    )
                return record

            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                for record in pool.map(request, range(args.requests)):
                    report["records"].append(record)
                    save(args.output, report)
        report["skipped_requests"] = sum("skipped" in r for r in report["records"])
        report["status"] = "partial" if report["skipped_requests"] else "completed"
    except (Exception, KeyboardInterrupt) as exc:
        report.update(status="failed", error_type=type(exc).__name__)
    finally:
        elapsed = time.monotonic() - started
        successes = [r for r in report["records"] if r.get("ok") and not r.get("seed")]
        latencies = sorted(r["seconds"] for r in successes)
        report.update(
            elapsed_seconds=elapsed,
            request_diagnostics=audit.summary(),
            successful_requests=len(successes),
            failed_requests=sum(r.get("ok") is False for r in report["records"]),
            successful_requests_per_second=len(successes) / elapsed,
            p50_seconds=latencies[math.ceil(len(latencies) * 0.5) - 1] if latencies else None,
            p95_seconds=latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None,
        )
        apply_policy(report, datetime.now(UTC))
        # Only one successful HTTP attempt over five minutes establishes this.
        report["long_request_verified"] = long_request[0]
        report["route_metrics"] = {}
        for route in ("/add", "/search"):
            records = [r for r in report["records"] if r["route"] == route]
            latencies = sorted(r["seconds"] for r in records if r.get("ok"))
            report["route_metrics"][route] = {
                "successful": len(latencies),
                "failed": sum(r.get("ok") is False for r in records),
                "p50_seconds": latencies[math.ceil(len(latencies) * 0.5) - 1]
                if latencies
                else None,
                "p95_seconds": latencies[math.ceil(len(latencies) * 0.95) - 1]
                if latencies
                else None,
            }
        save(args.output, report)
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "successful_requests",
                    "failed_requests",
                    "p95_seconds",
                    "long_request_verified",
                )
            },
            indent=2,
        )
    )
    return 0 if report["status"] == "completed" and report["failed_requests"] == 0 else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["source-time", "capacity"])
    parser.add_argument("--adapter-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Send requests; otherwise plan only")
    parser.add_argument(
        "--explain", action="store_true", help="Source-time only; needs backend auth"
    )
    parser.add_argument("--reference-time", help="ISO8601 timezone-aware timestamp")
    parser.add_argument("--route", choices=["add", "search"], default="search")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--qps", type=float, default=1)
    parser.add_argument("--requests", type=int, default=10)
    parser.add_argument("--messages-per-add", type=int, default=1)
    parser.add_argument("--message-bytes", type=int, default=256)
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()
    if (
        not 1 <= args.concurrency <= 8
        or not 1 <= args.requests <= 1000
        or not 0.1 <= args.qps <= 20
        or not 1 <= args.messages_per_add <= 100
        or not 32 <= args.message_bytes <= 32768
        or not 1 <= args.duration <= 3600
        or not 1 <= args.timeout <= 1800
        or args.requests * args.messages_per_add * args.message_bytes > 128 * 1024 * 1024
    ):
        parser.error("Invalid workload bounds; see --help and the benchmark guide")
    if args.explain and args.mode != "source-time":
        parser.error("--explain applies only to source-time")
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
