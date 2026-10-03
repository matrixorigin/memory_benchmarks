"""Run or inspect the nine datasets listed in AML's benchmark catalog."""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = [
    ("LoCoMo-Refined", "textual/locomo_refined", "locomo", "run.json"),
    ("ScriptMem", "textual/scriptmem", None, None),
    ("LongMemEval-Refined", "textual/longmemeval_refined", None, None),
    ("LongMemEval-S", "textual/longmemeval_s", "longmemeval", "full-run.json"),
    ("CLBench", "textual/clbench", "clbench", "full-run.json"),
    ("PersonaMem-v2", "textual/personamem_v2", "personamem_v2", "full-run.json"),
    ("BEAM", "textual/beam", "beam", "full-run.json"),
    ("CAMBench Coding", "coding/cambench", None, None),
    ("SWEContextBench", "coding/swecontextbench", "swecontextbench", "full-run.json"),
]


def snapshot():
    rows = []
    for name, directory, kind, filename in CATALOG:
        base = ROOT / "benchmarks" / directory
        row = {"dataset": name, "directory": str(base.relative_to(ROOT)),
               "kind": kind, "native_benchmark_score": None}
        report = base / "results" / filename if filename else None
        if report and report.exists():
            value = json.loads(report.read_text())
            row.update(status=value["status"], report=str(report.relative_to(ROOT)),
                       summary=value.get("summary"), error=value.get("error"),
                       current=value.get("current"), stage=value.get("stage"))
            row["privacy"] = value.get("privacy")
            if value["status"] == "running":
                # Read counters directly; final summary is only written on exit.
                row["progress"] = {"completed_adds": len(value["completed_adds"]),
                                   "processed_questions": len(value["questions"])}
        elif (base / "status.json").exists():
            value = json.loads((base / "status.json").read_text())
            row.update(status=value["status"], reason=value.get("reason"),
                       sources=value.get("sources"))
        else:
            row["status"] = "not_run"
        rows.append(row)
    return {"snapshot_at": datetime.now(timezone.utc).isoformat(),
            "catalog_source": "https://agentmemories.ai/docs#doc-en-benchmarks",
            "mode": "Add/Search endpoint diagnostics; no native answer or coding scores",
            "datasets": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Run/resume every available dataset")
    parser.add_argument("--runtime-label")
    parser.add_argument("--output", type=Path, default=ROOT / "state" / "suite-results.json")
    args = parser.parse_args()
    if args.run:
        current = snapshot()["datasets"]
        for (_, directory, kind, filename), row in zip(CATALOG, current, strict=True):
            if kind is None or row["status"] in {"completed", "running"}:
                continue
            output = ROOT / "benchmarks" / directory / "results" / filename
            command = [sys.executable, "-m", "src.benchmark", "--kind", kind,
                       "--all", "--output", str(output)]
            if output.exists():
                command.append("--resume")
            if args.runtime_label:
                command.extend(["--runtime-label", args.runtime_label])
            subprocess.run(command, cwd=ROOT, check=False)
    report = snapshot()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if args.run and any(r["status"] != "completed" for r in report["datasets"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
