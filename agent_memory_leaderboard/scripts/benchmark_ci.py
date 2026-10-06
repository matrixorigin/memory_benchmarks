"""Prepare pinned public data, execute the existing runner, and render Actions results.

Preparation and reporting use only the standard library, including when uv setup
fails. Credentials are needed only by execute and never enter the artifacts.
"""

import argparse
import hashlib
import html
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "var" / "ci-benchmark"
RESULTS = WORK / "results"
SELECTION = RESULTS / "selection.json"
REPORT = RESULTS / "run.json"
DATA = WORK / "input.json"
CATALOG = ROOT / "benchmarks" / "ci-datasets.json"
ADAPTER_URL = "https://aml.cn-dev.matrixone.tech"


class BenchmarkError(ValueError):
    """Controlled, nonsecret configuration/verification error."""


def now():
    return datetime.now(UTC).isoformat()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def integer(name, default, maximum):
    value = os.getenv(name, default)
    if not re.fullmatch(r"[0-9]{1,3}", value) or not 1 <= int(value) <= maximum:
        raise BenchmarkError(f"{name} must be an integer between 1 and {maximum}")
    return int(value)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare():
    kind = os.getenv("AML_BENCHMARK_DATASET", "locomo")
    catalog = json.loads(CATALOG.read_text())
    if kind not in catalog:
        raise BenchmarkError("Choose locomo or longmemeval")
    source = catalog[kind]
    limit = integer("AML_BENCHMARK_HISTORIES", "1", min(10, source["histories"]))
    top_k = integer("AML_BENCHMARK_TOP_K", "100", 100)
    label = os.getenv("AML_BENCHMARK_RUNTIME_LABEL", "").strip()
    if len(label) > 200 or any(ord(c) < 32 for c in label):
        raise BenchmarkError(
            "Backend revision label: maximum 200 characters, no control characters"
        )
    if REPORT.exists():
        raise BenchmarkError("A checkpoint already exists; use a fresh workflow workspace")
    selection = {
        "dataset": kind, "source": source, "histories": limit, "top_k": top_k,
        "adapter_url": ADAPTER_URL, "runtime_label": label or None,
        "backend_revision_verified": False,
        "benchmark_commit": os.getenv("GITHUB_SHA", "local"),
        "workflow_run_id": os.getenv("GITHUB_RUN_ID"),
        "workflow_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        "prepared_at": now(), "status": "downloading",
    }
    write_json(SELECTION, selection)
    if not DATA.exists() or file_hash(DATA) != source["sha256"]:
        partial = DATA.with_suffix(".part")
        try:
            for attempt in range(3):
                try:
                    size = 0
                    with urlopen(source["url"], timeout=60) as response, partial.open("wb") as out:
                        while chunk := response.read(1024 * 1024):
                            size += len(chunk)
                            if size > source["bytes"]:
                                raise BenchmarkError("Dataset exceeds its pinned size")
                            out.write(chunk)
                    break
                except (OSError, TimeoutError):
                    if attempt == 2:
                        raise
                    time.sleep(attempt + 1)
            if partial.stat().st_size != source["bytes"]:
                raise BenchmarkError("Dataset does not match its pinned size")
            if file_hash(partial) != source["sha256"]:
                raise BenchmarkError("Dataset SHA256 differs from the pinned release")
            partial.replace(DATA)
        finally:
            partial.unlink(missing_ok=True)
    selection["status"] = "prepared"
    write_json(SELECTION, selection)
    print(f"Prepared {source['name']}: {limit} complete histories, Top K {top_k}")


def execute():
    selection = json.loads(SELECTION.read_text())
    if selection["status"] != "prepared" or file_hash(DATA) != selection["source"]["sha256"]:
        raise BenchmarkError("Run prepare with the pinned dataset before execute")
    if not os.getenv("ADAPTER_API_KEY"):
        raise BenchmarkError("AML_ADAPTER_API_KEY repository Secret is required")
    with urlopen(selection["adapter_url"] + "/ready", timeout=20) as response:
        if response.status != 200:
            raise BenchmarkError("The deployed adapter is not ready")
    selection.update(status="running", started_at=now())
    write_json(SELECTION, selection)
    # No shell, local.env, Memoria master key, kubeconfig, or Pulumi access.
    command = [
        sys.executable, "-m", "src.benchmark", "--kind", selection["dataset"],
        "--dataset", str(DATA), "--output", str(REPORT),
        "--adapter-url", selection["adapter_url"],
        "--limit", str(selection["histories"]), "--top-k", str(selection["top_k"]),
    ]
    if label := selection.get("runtime_label"):
        command.extend(["--runtime-label", label])
    outcome = subprocess.run(command, cwd=ROOT, check=False)
    selection.update(
        status="completed" if outcome.returncode == 0 else "failed",
        finished_at=now(), exit_code=outcome.returncode,
    )
    write_json(SELECTION, selection)
    return outcome.returncode


def metrics(questions):
    records = list(questions)
    recalls = [r["evidence_recall"] for r in records if r.get("evidence_recall") is not None]
    returned = [r["returned"] for r in records if "returned" in r]
    audited = [r for r in records if "unmatched_gold_evidence" in r]
    latencies = sorted(
        r["latency_seconds"] for r in records
        if isinstance(r.get("latency_seconds"), (int, float))
        and math.isfinite(r["latency_seconds"]) and r["latency_seconds"] >= 0
    )
    return {
        "processed_questions": len(records),
        "searched_questions": sum("returned" in r for r in records),
        "skipped_questions": sum("skipped" in r for r in records),
        "empty_results": sum(r.get("returned") == 0 for r in records),
        "mean_returned_records": sum(returned) / len(returned) if returned else None,
        "questions_with_evidence": len(recalls),
        "evidence_audited_questions": len(audited),
        "questions_with_unmatched_gold": sum(
            bool(r["unmatched_gold_evidence"]) for r in audited
        ) if audited else None,
        "mean_evidence_recall": sum(recalls) / len(recalls) if recalls else None,
        "all_evidence_hit_rate": sum(r == 1 for r in recalls) / len(recalls) if recalls else None,
        # Nearest-rank percentiles; includes request retries, excludes skipped queries.
        "search_p50_seconds": latencies[math.ceil(len(latencies) * .5) - 1]
        if latencies else None,
        "search_p95_seconds": latencies[math.ceil(len(latencies) * .95) - 1]
        if latencies else None,
    }


def history_metrics(report):
    """Group recorded questions, including unfinished and legacy workflow reports."""
    samples = report.get("contract", {}).get("conversations", [])
    groups = {sample: [] for sample in samples}
    unassigned = []
    kind = report.get("contract", {}).get("kind", "locomo")
    for qid, record in report.get("questions", {}).items():
        sample = record.get("sample_id")
        if sample is None:
            # Existing workflow reports predate the explicit sample_id field.
            if kind == "longmemeval":
                sample = qid
            elif kind == "locomo":
                sample = qid.rpartition("#q")[0]
        if sample in groups:
            groups[sample].append(record)
        else:
            unassigned.append(record)
    return {sample: metrics(records) for sample, records in groups.items()}, metrics(unassigned)


def cell(value):
    if value is None:
        return "N/A"
    return html.escape(str(value)).replace("|", "&#124;").replace("\n", " ").replace("`", "&#96;")


def table(rows):
    return ["| Metric | Value |", "|---|---|"] + [
        f"| {cell(name)} | {cell(value)} |" for name, value in rows
    ]


def summary():
    selection = json.loads(SELECTION.read_text()) if SELECTION.exists() else {}
    report = json.loads(REPORT.read_text()) if REPORT.exists() else {}
    failure_path = RESULTS / "failure.json"
    failure = json.loads(failure_path.read_text()) if failure_path.exists() else None
    if report and not report.get("privacy", {}).get("delete_by"):
        # Hard step timeouts may kill the runner before its finally block. Keep the
        # existing deadline if present; never extend retention during reporting.
        deadline = datetime.now(UTC) + timedelta(days=30)
        report.setdefault("privacy", {}).update(
            retention_days=30, delete_by=deadline.isoformat(),
            cleanup_after=(deadline - timedelta(days=1)).isoformat(),
        )
        write_json(REPORT, report)
    totals = metrics(report.get("questions", {}).values()) if report else {}
    histories, unassigned = history_metrics(report)
    groups = {}
    for record in report.get("questions", {}).values():
        if "skipped" not in record:
            groups.setdefault(str(record.get("category", "unknown")), []).append(record)
    elapsed = None
    if selection.get("started_at"):
        end = selection.get("finished_at") or now()
        elapsed = round((datetime.fromisoformat(end) -
                         datetime.fromisoformat(selection["started_at"])).total_seconds(), 2)
    final = {
        "prepare_outcome": os.getenv("AML_PREPARE_OUTCOME", "unknown"),
        "execute_outcome": os.getenv("AML_EXECUTE_OUTCOME", "unknown"),
        "checkpoint_status": report.get("status", "not_created"),
        "error": report.get("error"), "stage": report.get("stage"),
        "workflow_error": failure,
        "elapsed_seconds": elapsed,
        "completed_add_chunks": len(report["completed_adds"]) if report else None,
        "metrics": totals,
        "categories": {key: metrics(value) for key, value in groups.items()},
        "histories": histories, "unassigned_questions": unassigned,
        "official_aml_score": False,
    }
    write_json(RESULTS / "summary.json", final)
    lines = ["## AML benchmark — retrieval diagnostics", "",
             "Evidence recall only; no answer accuracy or official AML score.", ""]
    lines += table([
        ("Dataset", selection.get("source", {}).get("name")),
        ("Histories / Top K", f"{selection['histories']} / {selection['top_k']}"
         if selection else None),
        ("Prepare / Execute outcome", f"{final['prepare_outcome']} / {final['execute_outcome']}"),
        ("Checkpoint status", final["checkpoint_status"]),
        ("Execution elapsed (seconds)", elapsed),
        ("Completed Add chunks", final["completed_add_chunks"]),
        ("Processed / searched / skipped questions",
         f"{totals['processed_questions']} / {totals['searched_questions']} / "
         f"{totals['skipped_questions']}" if totals else None),
        ("Empty results", totals.get("empty_results")),
        ("Mean returned evidence records", rounded(totals.get("mean_returned_records"))),
        ("Questions with evidence labels", totals.get("questions_with_evidence")),
        ("Evidence-audited Search questions", totals.get("evidence_audited_questions")),
        ("Questions with unmatched gold labels (still scored)",
         totals.get("questions_with_unmatched_gold")),
        ("Mean evidence recall (0–1, higher is better)",
         rounded(totals.get("mean_evidence_recall"))),
        ("All evidence hit rate (0–1, higher is better)",
         rounded(totals.get("all_evidence_hit_rate"))),
        ("Search P50 / P95 (seconds, lower is faster)",
         f"{rounded(totals.get('search_p50_seconds'))} / "
         f"{rounded(totals.get('search_p95_seconds'))}"),
        ("Failure type / HTTP status", json.dumps(final["error"]) if final["error"] else None),
        ("Preparation / execution error", json.dumps(failure) if failure else None),
        ("Last checkpoint stage", final["stage"]),
        ("Benchmark commit", selection.get("benchmark_commit")),
        ("Backend revision (operator supplied, not verified)",
         selection.get("runtime_label") or "Not recorded"),
        ("Dataset revision", selection.get("source", {}).get("revision")),
        ("Dataset SHA256", selection.get("source", {}).get("sha256")),
    ])
    if histories:
        lines += ["", "### By history", "",
                  "| History | Searched | Skipped | Labeled | Recall | All evidence hit rate | "
                  "Mean returned | Search P95 (seconds) |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for sample, stats in histories.items():
            lines.append(
                f"| {cell(sample)} | {stats['searched_questions']} | "
                f"{stats['skipped_questions']} | {stats['questions_with_evidence']} | "
                f"{rounded(stats['mean_evidence_recall'])} | "
                f"{rounded(stats['all_evidence_hit_rate'])} | "
                f"{rounded(stats['mean_returned_records'])} | "
                f"{rounded(stats['search_p95_seconds'])} |"
            )
    if unassigned["processed_questions"]:
        lines += ["", f"{unassigned['processed_questions']} question records could not be "
                  "assigned to a selected history; they remain included in aggregate metrics."]
    if groups:
        lines += ["", "### By dataset category", "",
                  "| Category | Searched | Labeled | Recall | All evidence hit rate |",
                  "|---|---:|---:|---:|---:|"]
        for key, stats in sorted(final["categories"].items()):
            lines.append(
                f"| {cell(key)} | {stats['searched_questions']} | "
                f"{stats['questions_with_evidence']} | "
                f"{rounded(stats['mean_evidence_recall'])} | "
                f"{rounded(stats['all_evidence_hit_rate'])} |"
            )
    lines += ["", "N/A means unavailable, not zero. Failure metrics cover completed work only.",
              "Search latency includes retries; elapsed time excludes data preparation.",
              "History rows cover recorded work only; zero searched may mean not yet processed.",
              "Dataset categories are not AML's seven leaderboard capability dimensions.",
              "", "Download the run's aml-benchmark artifact for the report/checkpoint, "
              "selection, summary and evidence ID/hash audit files.",
              "Run-scoped backend memories are retained; artifacts expire after 30 days. "
              "Artifact expiry does not clean backend memories or the journal.", ""]
    markdown = "\n".join(lines)
    (RESULTS / "summary.md").write_text(markdown)
    if path := os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a") as output:
            output.write(markdown)
    print("Saved benchmark summary and checkpoint artifacts")


def rounded(value):
    return "N/A" if value is None else f"{value:.4f}"


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "execute", "summary"])
    args = parser.parse_args()
    try:
        result = {"prepare": prepare, "execute": execute, "summary": summary}[args.command]()
    except Exception as exc:
        # Arbitrary upstream errors may contain bodies, URLs, credentials, or payloads.
        failure = {"phase": args.command, "type": type(exc).__name__}
        if isinstance(exc, BenchmarkError):
            failure["reason"] = str(exc)
        if isinstance(exc, HTTPError):
            failure["http_status"] = exc.code
        write_json(RESULTS / "failure.json", failure)
        print(f"Benchmark {args.command} failed ({type(exc).__name__})", file=sys.stderr)
        if isinstance(exc, BenchmarkError):
            print(str(exc), file=sys.stderr)
        return 1
    return result or 0


if __name__ == "__main__":
    raise SystemExit(main())
