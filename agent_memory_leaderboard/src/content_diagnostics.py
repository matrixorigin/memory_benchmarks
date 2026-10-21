"""Opt-in, bounded local content diagnostics for independently sourced evaluations."""

import argparse
import copy
import gzip
import hashlib
import html
import ipaddress
import json
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

FORMAT = 1
MIB = 1024 * 1024
REQUIRED_USES = {"local_evaluation", "local_content_diagnostics"}
MODEL_FIELDS = {
    "answer_model",
    "judge_model",
    "encoding",
    "input_budget",
    "output_tokens",
    "prompt_version",
    "scorer_version",
    "temperature",
    "token_accounting",
    "context_selection_version",
    "model_retry_version",
    "verdict_parser_version",
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def read_record(root, key, limit):
    if not re.fullmatch(r"[aq]-[0-9a-f]{64}", key):
        raise ValueError("Invalid diagnostic record identity")
    path = Path(root) / (key + ".json.gz")
    if path.is_symlink():
        raise ValueError("Diagnostic record cannot be a symlink")
    if not path.exists():
        return {}
    with gzip.open(path, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Diagnostic record exceeds the capture limit")
    value = json.loads(data)
    if value.get("format") != FORMAT:
        raise ValueError("Unsupported diagnostic record version")
    return value


def source_declaration(path):
    try:
        value = json.loads(Path(path).read_text())
        valid = (
            value["version"] == FORMAT
            and value["source_type"] in {"synthetic", "public"}
            and all(
                value[k] is True
                for k in ("independently_obtained", "no_official_material", "no_sensitive_content")
            )
            and type(value["retention_days"]) is int
            and 1 <= value["retention_days"] <= 30
            and isinstance(value["permitted_uses"], list)
            and REQUIRED_USES <= set(value["permitted_uses"])
            and all(
                isinstance(value[k], str) and 0 < len(value[k]) <= 500
                for k in ("source_uri", "source_version", "license")
            )
            and re.fullmatch(r"[0-9a-f]{64}", value["dataset_sha256"]) is not None
        )
        if not valid:
            raise ValueError
        if any(
            value[k].strip().lower() in {"unknown", "unspecified", "none", "n/a"}
            for k in ("license", "source_version")
        ):
            raise ValueError
        uri = urlsplit(value["source_uri"])
        if value["source_type"] == "public":
            if (
                uri.scheme != "https"
                or not uri.hostname
                or uri.username
                or uri.password
                or uri.query
                or uri.fragment
                or uri.hostname == "localhost"
                or uri.hostname.endswith((".local", ".internal", ".test", ".invalid"))
            ):
                raise ValueError
            try:
                address = ipaddress.ip_address(uri.hostname)
            except ValueError:
                address = None
            if address is not None and not address.is_global:
                raise ValueError
        else:
            if not re.fullmatch(r"synthetic:[a-zA-Z0-9._-]+", value["source_uri"]):
                raise ValueError
        redactor = Redactor()
        for field in ("source_version", "license"):
            if redactor.clean(value[field]) != value[field]:
                raise ValueError
        if any(secret in value["source_uri"] for secret in redactor.secrets):
            raise ValueError
        # Do not copy unknown fields, which could contain credentials or private configuration.
        result = {
            k: value[k]
            for k in (
                "version",
                "source_type",
                "source_uri",
                "source_version",
                "license",
                "dataset_sha256",
                "retention_days",
                "independently_obtained",
                "no_official_material",
                "no_sensitive_content",
            )
        }
        return result | {"permitted_uses": sorted(REQUIRED_USES)}
    except (KeyError, TypeError, ValueError, OSError):
        raise ValueError(
            "Content diagnostics require a valid independent-data declaration"
        ) from None


def options(args):
    enabled = getattr(args, "content_diagnostics", False)
    source = getattr(args, "diagnostic_source", None)
    if not enabled:
        if source is not None:
            raise ValueError("--diagnostic-source requires --content-diagnostics")
        return None
    if source is None:
        raise ValueError("--content-diagnostics requires --diagnostic-source")
    mib = getattr(args, "diagnostic_max_mib", 64)
    questions = getattr(args, "diagnostic_max_questions", 100)
    if not 2 <= mib <= 512 or not 1 <= questions <= 1000:
        raise ValueError("Diagnostic limits: 2–512 MiB and 1–1000 questions")
    return {
        "format": FORMAT,
        "source": source_declaration(source),
        "max_bytes": mib * MIB,
        "max_questions": questions,
    }


class Redactor:
    """Display redaction only: wire requests and evidence measurement remain untouched."""

    def __init__(self):
        self.secrets = sorted(
            {
                v
                for k, v in os.environ.items()
                if re.search(r"KEY|TOKEN|SECRET|PASSWORD", k, re.I) and len(v) >= 4
            },
            key=len,
            reverse=True,
        )

    def clean(self, value):
        if isinstance(value, dict):
            result = {}
            for k, v in value.items():
                hashes = v if isinstance(v, list) else [v]
                if (
                    (k.endswith("sha256") or k == "sample")
                    and hashes
                    and all(isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes)
                ):
                    result[k] = v
                else:
                    result[k] = self.clean(v)
            return result
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if not isinstance(value, str):
            return value
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"https?://[^\s\"<>]+", "[REDACTED_URL]", value)
        value = re.sub(r"(?i)\b(?:sk-|ghp_|github_pat_)[a-z0-9_-]{12,}", "[REDACTED]", value)
        return re.sub(r"(?i)\b(?:authorization|x-api-key)\s*[:=][^\n]+", "[REDACTED_AUTH]", value)


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".diagnostic-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def page(title, body):
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
        f"<title>{html.escape(title)}</title><style>body{{max-width:1000px;margin:2em auto;"
        "font:16px system-ui;padding:1em}}pre{white-space:pre-wrap;overflow-wrap:anywhere;"
        "background:#f4f4f4;padding:1em}td,th{padding:.5em;border:1px solid #ddd}"
        "table{border-collapse:collapse}aside{background:#fff2cc;padding:1em}</style>"
        f"</head><body><h1>{html.escape(title)}</h1>{body}</body></html>"
    ).encode()


def show(title, value):
    text = html.escape(json.dumps(value, ensure_ascii=False, indent=2))
    return f"<h2>{html.escape(title)}</h2><pre>{text}</pre>"


def signals(record):
    metrics = record.get("metrics", {})
    if metrics.get("evaluation_pending"):
        return [metrics.get("evaluation_status", "model_pending")]
    mapping = metrics.get("source_attribution", {})
    if (
        mapping.get("ambiguous_records")
        or mapping.get("unmapped_records")
        or metrics.get("unmatched_gold_evidence")
    ):
        return ["source_attribution_uncertain"]
    found = metrics.get("evidence_recall")
    budget = metrics.get("evaluation", {}).get("budget_evidence_recall")
    result = []
    if found is None:
        result.append("source_coverage_unavailable")
    elif found < 1:
        result.append("annotated_sources_not_retrieved")
    if budget is not None and found is not None and budget < found:
        result.append("annotated_sources_excluded_by_budget")
    if budget == 1 and metrics.get("evaluation", {}).get("local_judge_score") == 0:
        result.append("answer_or_scoring_requires_review")
    if "model" not in record:
        result.append("answer_eval_not_run")
    return result or ["no_specific_failure_signal"]


class ContentDiagnostics:
    def __init__(self, root, policy, contract, selected, *, resume=False):
        self.root = Path(root)
        if self.root.is_symlink():
            raise ValueError("Diagnostic directory cannot be a symlink")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.policy, self.redactor = policy, Redactor()
        self.reserve = min(MIB, policy["max_bytes"] // 4)
        self.sizes = {p.name: p.stat().st_size for p in self.root.iterdir() if p.is_file()}
        if any(p.is_symlink() or p.is_dir() for p in self.root.iterdir()):
            raise ValueError("Diagnostic directory has unsupported entries")
        self.manifest_path = self.root / "manifest.json"
        if resume and not self.manifest_path.exists():
            raise ValueError("Missing diagnostic manifest; start a fresh evaluation")
        self.current = None
        self.state = None
        if self.manifest_path.exists():
            self.state = json.loads(self.manifest_path.read_text())
            if self.state.get("format") != FORMAT or self.state.get("policy") != policy:
                raise ValueError("Diagnostic format or source/limits differ from checkpoint")
            if datetime.now(UTC) >= datetime.fromisoformat(self.state["expires_at"]):
                raise ValueError("Expired diagnostic files require retention cleanup")
        else:
            picked, samples = [], set()
            seen = set()
            total = 0
            for conversation in selected:
                for i, question in enumerate(conversation["qa"]):
                    qid = question.get("id", f"{conversation['sample_id']}#q{i:04d}")
                    key = "q-" + digest(qid)
                    if key in seen:
                        raise ValueError("Diagnostic question identities must be unique")
                    seen.add(key)
                    total += 1
                    if len(picked) < policy["max_questions"]:
                        picked.append(key)
                        samples.add(digest(conversation["sample_id"]))
            evaluation = contract.get("evaluation") or {}
            comparable = {
                k: contract[k]
                for k in (
                    "dataset_sha256",
                    "kind",
                    "top_k",
                    "mode",
                    "ingestion_version",
                )
            }
            comparable["histories_sha256"] = digest(contract["conversations"])
            comparable["evaluation"] = {k: evaluation[k] for k in MODEL_FIELDS if k in evaluation}
            comparable["model_endpoints_sha256"] = digest(
                [evaluation.get("answer_base"), evaluation.get("judge_base")]
            )
            source_files = sorted(Path(__file__).parent.glob("*.py"))
            self.state = {
                "format": FORMAT,
                "policy": policy,
                "comparison_contract": self.redactor.clean(comparable),
                "runner_source_sha256": digest(
                    [(p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in source_files]
                ),
                "created_at": datetime.now(UTC).isoformat(),
                "expires_at": (
                    datetime.now(UTC) + timedelta(days=policy["source"]["retention_days"])
                ).isoformat(),
                "selected_questions": picked,
                "selected_samples": sorted(samples),
                "total_questions": total,
                "records": {},
                "incomplete": [],
                "omitted_questions": max(0, total - len(picked)),
                "redactions_present": False,
                "official_aml_score": False,
            }
            self.flush()

    def flush(self):
        atomic_bytes(self.manifest_path, encoded(self.state))
        self.sizes["manifest.json"] = self.manifest_path.stat().st_size

    def incomplete(self, reason):
        if reason not in self.state["incomplete"]:
            self.state["incomplete"].append(reason)
        self.flush()

    def read(self, key):
        return read_record(self.root, key, self.policy["max_bytes"])

    def write_file(self, name, data):
        used = sum(v for k, v in self.sizes.items() if k not in {"manifest.json", "index.html"})
        if used - self.sizes.get(name, 0) + len(data) > self.policy["max_bytes"] - self.reserve:
            self.incomplete("size_limit_reached")
            return False
        atomic_bytes(self.root / name, data)
        self.sizes[name] = len(data)
        return True

    def put(self, key, record, kind, sample=None):
        clean = self.redactor.clean(record)
        if clean != record:
            self.state["redactions_present"] = True
        candidate = copy.deepcopy(self.state)
        candidate["records"][key] = {"kind": kind, "sample": sample}
        if len(encoded(candidate)) > self.reserve // 2:
            self.incomplete("metadata_limit_reached")
            return
        data = encoded(clean | {"format": FORMAT})
        if len(data) > self.policy["max_bytes"] - self.reserve:
            self.incomplete("size_limit_reached")
            return
        if self.write_file(key + ".json.gz", gzip.compress(data, mtime=0)):
            self.state["records"][key] = candidate["records"][key]
            self.flush()

    def add(self, sample, body, response=None):
        sample = digest(sample)
        if sample in self.state["selected_samples"]:
            self.put(
                "a-" + digest(body["request_id"]),
                {
                    "request": body,
                    "response": response,
                    "request_sha256": digest(body),
                    "status": "acknowledged" if response else "prepared_not_confirmed",
                },
                "add",
                sample,
            )

    def question(self, qid, sample, question, body=None, result=None, metrics=None):
        key = "q-" + digest(qid)
        self.current = key if key in self.state["selected_questions"] else None
        if self.current is None:
            return
        record = self.read(key)
        record.update(
            question={
                k: question[k]
                for k in (
                    "question",
                    "answer",
                    "options",
                    "question_date",
                    "category",
                    "evidence",
                )
                if k in question
            },
            sample=digest(sample),
        )
        if body is not None:
            record["search_request"] = body
            record["search_request_sha256"] = digest(body)
        if result is not None:
            record["retrieved"] = result
            record["retrieved_content_sha256"] = [digest(r["content"]) for r in result]
        if metrics is not None:
            record["metrics"] = metrics
        self.put(key, record, "question", digest(sample))

    def model(self, phase, value):
        if self.current is None:
            return
        record = self.read(self.current)
        if not record:
            self.incomplete("question_snapshot_unavailable")
            return
        record.setdefault("model", {})[phase] = value | {"actual_payload_sha256": digest(value)}
        # A retried phase must not display the preceding attempt's output as its new result.
        if phase.endswith("_input"):
            record["model"].pop(phase.replace("_input", "_output"), None)
        if phase == "answer_input":
            for stale in ("judge_input", "judge_output", "judge_candidates"):
                record["model"].pop(stale, None)
        if phase == "judge_input":
            record["model"].setdefault("judge_candidates", []).append({"input": value})
        elif phase == "judge_output" and record["model"].get("judge_candidates"):
            record["model"]["judge_candidates"][-1]["output"] = value
        self.put(self.current, record, "question", record["sample"])

    def resume_search(self, contents):
        if self.current is None:
            return
        expected = self.read(self.current).get("retrieved_content_sha256")
        if expected is None:
            self.incomplete("resumed_search_snapshot_unavailable")
        elif expected != [digest(content) for content in contents]:
            raise ValueError("Diagnostic Search snapshot differs from the model cache")

    def render(self, status, summary, error=None):
        self.state["run_status"] = status
        self.state["summary"] = summary
        self.state["run_error"] = error
        captured = sum(
            m["kind"] == "question" and (self.root / (key + ".json.gz")).is_file()
            for key, m in self.state["records"].items()
        )
        self.state["snapshot_questions"] = captured
        self.state["unrecorded_selected_questions"] = (
            len(self.state["selected_questions"]) - captured
        )
        links = []
        for key, metadata in list(self.state["records"].items()):
            record = self.read(key)
            if not record:
                self.incomplete("record_snapshot_unavailable")
                continue
            if metadata["kind"] == "add":
                body = show("Actual prepared Add request and acknowledged response", record)
            else:
                body = (
                    "<aside>Local diagnostic signals are not automatic root-cause findings.</aside>"
                )
                if self.state["incomplete"]:
                    body += (
                        "<aside>Capture is incomplete; this is the latest saved snapshot.</aside>"
                    )
                body += show("Question (gold is diagnostic-only)", record.get("question"))
                body += show(
                    "Prepared Search request",
                    record.get("search_request", "not captured; consult run stage/status"),
                )
                included = record.get("model", {}).get("answer_input", {}).get("included_records")
                for rank, item in enumerate(record.get("retrieved", []), 1):
                    selection = (
                        "not evaluated"
                        if included is None
                        else "included"
                        if rank <= included
                        else "excluded by input budget"
                    )
                    body += show(f"Search rank {rank}: {selection}", item)
                body += show(
                    "Actual model inputs and returned outputs",
                    record.get("model", "Answer/Eval not run; retrieval-only or skipped"),
                )
                body += show("Metrics, model failures and retries", record.get("metrics", {}))
                body += show("Diagnostic signals", signals(record))
                adds = [
                    k
                    for k, m in self.state["records"].items()
                    if m["kind"] == "add" and m["sample"] == record["sample"]
                ]
                body += (
                    "<h2>Shared Add history</h2>"
                    "<p>Prepared-only entries are not proof of a sent request.</p>"
                )
                body += (
                    "<ul>"
                    + "".join(
                        (
                            f"<li><a href='{k}.html'>Add batch {i}</a></li>"
                            if (self.root / (k + ".html")).exists()
                            else f"<li>Add batch {i}: readable page omitted by capture limit</li>"
                        )
                        for i, k in enumerate(adds, 1)
                    )
                    + "</ul>"
                )
            if self.write_file(key + ".html", page("Local content diagnostic", body)):
                if metadata["kind"] == "question":
                    links.append(f"<li><a href='{key}.html'>Question {len(links) + 1}</a></li>")
        self.state["readable_question_pages"] = len(links)
        body = (
            "<aside>Independent self-run evaluation only. Not an official AML result. "
            "Display redactions do not change requests, source hashes or scores.</aside>"
        )
        if self.state.get("simulation"):
            body += (
                "<aside>OFFLINE TOOL DEMONSTRATION: simulated adapter/model responses "
                "and a synthetic byte tokenizer. Not a Memoria quality result.</aside>"
            )
        body += show(
            "Source declaration (operator-reviewed, not a license verification)",
            self.state["policy"]["source"],
        )
        body += show(
            "Evaluation contract (endpoint addresses omitted)", self.state["comparison_contract"]
        )
        body += show(
            "Coverage, limits and retention",
            {
                k: self.state[k]
                for k in (
                    "run_status",
                    "run_error",
                    "total_questions",
                    "snapshot_questions",
                    "unrecorded_selected_questions",
                    "readable_question_pages",
                    "omitted_questions",
                    "incomplete",
                    "redactions_present",
                    "expires_at",
                    "summary",
                )
            },
        )
        body += "<p>Record bodies are gzip JSON. Shared Add batches are stored once per run; "
        body += (
            "question pages link to their history. "
            "No model credentials or endpoint URLs are exported.</p>"
        )
        body += "<ol>" + "".join(links) + "</ol>"
        data = page("Local evaluation diagnostics", body)
        if len(data) > self.reserve // 2:
            self.incomplete("index_limit_reached")
            data = page(
                "Incomplete diagnostics", "<p>Index size limit reached; see manifest.json.</p>"
            )
        atomic_bytes(self.root / "index.html", data)
        self.sizes["index.html"] = len(data)
        self.flush()


def compare(left, right, output):
    states = [json.loads((Path(root) / "manifest.json").read_text()) for root in (left, right)]
    for state in states:
        if (
            state.get("format") != FORMAT
            or datetime.now(UTC) >= datetime.fromisoformat(state["expires_at"])
            or not 0 < state["policy"]["max_bytes"] <= 512 * MIB
        ):
            raise ValueError("Unsupported or expired diagnostic comparison input")
    if states[0]["comparison_contract"] != states[1]["comparison_contract"]:
        raise ValueError("Diagnostic datasets or evaluation contracts are not comparable")
    rows = []
    for key in sorted(set(states[0]["selected_questions"]) | set(states[1]["selected_questions"])):
        pair = []
        for root, state in zip((left, right), states):
            record = {}
            if key in state["records"]:
                record = read_record(root, key, state["policy"]["max_bytes"])
            pair.append(
                {
                    "metrics": record.get("metrics"),
                    "ranked_content_hashes": record.get("retrieved_content_sha256"),
                    "model_usage": {
                        phase: record.get("model", {}).get(phase, {}).get("usage")
                        for phase in ("answer_output", "judge_output")
                    },
                    "signals": signals(record) if record else ["not_recorded"],
                }
            )
        rows.append({"question_key": key, "before": pair[0], "after": pair[1]})
    result = {
        "format": FORMAT,
        "questions": rows,
        "coverage": [
            {
                k: state[k]
                for k in (
                    "summary",
                    "total_questions",
                    "omitted_questions",
                    "incomplete",
                )
            }
            for state in states
        ],
        "official_aml_score": False,
    }
    output = Path(output)
    if output.suffix != ".html":
        raise ValueError("Comparison output must use the .html extension")
    if output.exists() or output.with_suffix(".json").exists():
        raise ValueError("Comparison output already exists")
    result = Redactor().clean(result)
    atomic_bytes(output.with_suffix(".json"), encoded(result))
    atomic_bytes(output, page("Paired local evaluation comparison", show("Results", result)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path, help="First content-diagnostics directory")
    parser.add_argument("right", type=Path, help="Second content-diagnostics directory")
    parser.add_argument("--output", required=True, type=Path, help="New local HTML comparison file")
    args = parser.parse_args()
    compare(args.left, args.right, args.output)


if __name__ == "__main__":
    main()
