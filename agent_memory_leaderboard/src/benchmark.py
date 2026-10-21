"""Run public memory datasets through AML Add/Search with optional local Answer/Eval."""

import argparse
import hashlib
import json
import os
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .checkpoint import checkpoint, load, question_dir, save, save_question
from .content_diagnostics import ContentDiagnostics
from .content_diagnostics import options as diagnostic_options
from .datasets import ADAPTERS, DATASETS, normalize
from .diagnostics import RetryAudit, adapter_reason_code
from .evaluation import AnswerEvaluator, EvaluationConfig, ModelError
from .evidence import SourceManifest, prefix_recalls, recall, source_messages
from .models import AddResponse, SearchResponse
from .privacy import apply_policy, evidence_audit
from .smoke import call_adapter


def batches(conversation):
    """Original dialogue, roles and source dates only; no source/evaluation labels."""
    batch, words, index, current = [], 0, 0, None
    for session, _, _, message in source_messages(conversation):
        count = len(message["content"].split())
        if current != session:
            if batch:
                yield current, index, batch
            batch, words, index, current = [], 0, 0, session
        if batch and (len(batch) == 20 or words + count > 2000):
            yield session, index, batch
            batch, words, index = [], 0, index + 1
        batch.append(message)
        words += count
    if batch:
        yield current, index, batch


def evidence_ids(contents):
    """Legacy labeled-report helper; new runs use SourceManifest."""
    return set(re.findall(r"\[dialogue:([^\]]+)\]", "\n".join(contents)))


def evidence_score(expected, contents):
    expected = set(expected)
    found = evidence_ids(contents)
    if not expected:
        return None
    return len(expected & found) / len(expected)


def evidence_prefix_scores(expected, contents, top_k):
    """Recall of prefixes from this one Search response, without extra requests."""
    cutoffs = sorted({1, 5, 10, 20, 50, 100, top_k})
    return {str(k): evidence_score(expected, contents[:k]) for k in cutoffs if k <= top_k}


def summarize(report):
    questions = list(report["questions"].values())
    recalls = [q["evidence_recall"] for q in questions if q.get("evidence_recall") is not None]
    evaluations = [q["evaluation"] for q in questions if "evaluation" in q]

    def mean(key):
        values = [e[key] for e in evaluations if e.get(key) is not None]
        return sum(values) / len(values) if values else None

    eligible = sum(
        bool(q.get("evaluation_pending"))
        or ("evaluation" in q and "skipped" not in q["evaluation"])
        for q in questions
    )
    scored = sum("local_judge_score" in e for e in evaluations)
    return {
        "evaluation_eligible_questions": eligible,
        "evaluation_coverage": scored / eligible if eligible else None,
        "answer_failed_questions": sum(
            q.get("evaluation_status") == "answer_failed" for q in questions
        ),
        "judge_failed_questions": sum(
            q.get("evaluation_status") == "judge_failed" for q in questions
        ),
        "scored_questions": sum("local_judge_score" in e for e in evaluations),
        "evaluation_pending_questions": sum(bool(q.get("evaluation_pending")) for q in questions),
        "mean_local_judge_score": mean("local_judge_score"),
        "mean_token_f1": mean("token_f1"),
        "mean_bleu1": mean("bleu1"),
        "mean_budget_evidence_recall": mean("budget_evidence_recall"),
        "completed_add_chunks": len(report["completed_adds"]),
        "processed_questions": len(questions),
        "searched_questions": sum("returned" in q for q in questions),
        "skipped_questions": sum("skipped" in q for q in questions),
        "empty_results": sum(q.get("returned") == 0 for q in questions),
        "questions_with_evidence": len(recalls),
        "mean_evidence_recall": sum(recalls) / len(recalls) if recalls else None,
        "all_evidence_hit_rate": sum(r == 1 for r in recalls) / len(recalls) if recalls else None,
    }


def run(args):
    os.umask(0o077)
    diagnostic_policy = diagnostic_options(args)
    raw = args.dataset.read_bytes()
    if (
        diagnostic_policy is not None
        and hashlib.sha256(raw).hexdigest() != diagnostic_policy["source"]["dataset_sha256"]
    ):
        raise ValueError("Diagnostic source declaration does not match the dataset bytes")
    adapter = ADAPTERS[args.kind]
    dataset = adapter.load(args.dataset) if hasattr(adapter, "load") else json.loads(raw)
    selected = normalize(
        dataset[: None if getattr(args, "all", False) else args.conversations], args.kind
    )
    mode = getattr(args, "mode", "retrieval")
    evaluation_config = EvaluationConfig.from_env() if mode == "answer-eval" else None
    evaluator = AnswerEvaluator(evaluation_config, args.kind) if evaluation_config else None
    contract = {
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "conversations": [c["sample_id"] for c in selected],
        "adapter_url": args.adapter_url,
        "top_k": args.top_k,
        "runner_version": 2,
        "ingestion_version": "label-free-exact-hash-v1",
        "mode": mode,
        "evaluation": evaluator.config.contract() if evaluator else None,
        "kind": args.kind,
    }
    if diagnostic_policy is not None:
        contract["content_diagnostics"] = diagnostic_policy
    if args.resume:
        report = load(args.output)
        report["contract"].setdefault("kind", "locomo")
        if report["contract"] != contract:
            raise ValueError("Resume configuration differs from the recorded run")
    else:
        if (
            args.output.exists()
            or question_dir(args.output).exists()
            or args.output.with_suffix(".requests.jsonl").exists()
            or (args.output.parent / (args.output.stem + ".sources")).exists()
        ):
            raise ValueError("Output already exists; use --resume or choose a new file")
        report = {
            "run_id": uuid.uuid4().hex,
            "contract": contract,
            "mode": mode,
            "checkpoint_format": 2,
            "official_aml_score": False,
            "evidence_unit": DATASETS[args.kind]["evidence_unit"],
            "source": DATASETS[args.kind]["source"],
            "completed_adds": [],
            "questions": {},
            "question_audit_version": 2,
            "users": [],
            "notes": [
                "Exact-content source attribution; ambiguous/unmapped records are not credited.",
                "Local diagnostics, not an official AML score or native judge reproduction.",
                "Text-only ingestion; questions marked multimodal are skipped.",
                "Local whitespace chunking, not AML's frozen adapter.",
                "Run-scoped memories retained for diagnosis and resume.",
            ],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.parent.chmod(0o700)
    apply_policy(report)
    if evaluator:
        report["privacy"]["private_cache"] = (
            "Retrieved evidence and generated answers; excluded from CI artifacts; same retention"
        )
    if deadline := report["privacy"].get("delete_by"):
        if datetime.now(UTC) >= datetime.fromisoformat(deadline):
            raise ValueError(
                "Expired checkpoint: finish retention cleanup before starting a new job"
            )
    report.setdefault("attempts", []).append(
        {
            "started_at": datetime.now(UTC).isoformat(),
            "runtime_label": getattr(args, "runtime_label", None),
            "resumed": args.resume,
            "previous_status": report.get("status"),
            "previous_error": report.get("error"),
            "completed_adds_at_start": len(report["completed_adds"]),
            "processed_questions_at_start": len(report["questions"]),
        }
    )
    report.update(status="running", error=None, summary=None)
    completed = set(report["completed_adds"])
    headers = {}
    if key := os.getenv("ADAPTER_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    retry_audit = RetryAudit(args.output.with_suffix(".requests.jsonl"))
    private_root = getattr(args, "private_dir", None) or args.output.parent / ".private"
    private_root = private_root / report["run_id"]
    content = (
        ContentDiagnostics(
            private_root / "content-diagnostics",
            diagnostic_policy,
            contract,
            selected,
            resume=args.resume,
        )
        if diagnostic_policy is not None
        else None
    )
    if content is not None and evaluator is not None:
        evaluator.content_observer = content.model
    report["request_diagnostics"] = retry_audit.summary()

    def persist(*, final=False):
        report["request_diagnostics"] = retry_audit.summary()
        checkpoint(args.output, report, final=final)

    def call(http, path, body, identity):
        request_hash = hashlib.sha256((path + identity).encode()).hexdigest()
        return call_adapter(
            http, args.adapter_url, headers, path, body, observe=retry_audit.observer(request_hash)
        )

    persist()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(1800, connect=30, pool=30), trust_env=False
        ) as http:
            http.get(args.adapter_url.rstrip("/") + "/health").raise_for_status()
            for conversation in selected:
                sample = conversation["sample_id"]
                user = f"{args.kind}:{report['run_id']}:{sample}"
                if user not in report["users"]:
                    report["users"].append(user)
                manifest = SourceManifest(conversation["conversation"])
                manifest_path = (
                    args.output.parent
                    / (args.output.stem + ".sources")
                    / (hashlib.sha256(sample.encode()).hexdigest() + ".json")
                )
                save(manifest_path, manifest.dump())
                for session, index, messages in batches(conversation["conversation"]):
                    rid = f"{user}:{session}:{index}"
                    if rid in completed:
                        continue
                    report.update(stage="add", current=rid)
                    persist()
                    body = dict(request_id=rid, user_id=user, session_id=session, messages=messages)
                    if content is not None:
                        content.add(sample, body)
                    response = call(http, "/add", body, rid)
                    if not isinstance(response, dict) or response.get("success") is not True:
                        raise ValueError("Add response requires explicit success=true")
                    result = AddResponse.model_validate(response)
                    if any(
                        getattr(result, k) != body[k]
                        for k in ("request_id", "user_id", "session_id")
                    ):
                        raise ValueError("Add ID mismatch")
                    if content is not None:
                        content.add(sample, body, result.model_dump())
                    report["completed_adds"].append(rid)
                    completed.add(rid)
                    persist()
                    print(f"Added {sample}/{session}/{index}", flush=True)
                source_ids = {
                    turn["dia_id"]
                    for session, turns in conversation["conversation"].items()
                    if re.fullmatch(r"session_\d+", session)
                    for turn in turns
                }
                for i, question in enumerate(conversation["qa"]):
                    qid = question.get("id", f"{sample}#q{i:04d}")
                    previous = report["questions"].get(qid)
                    if previous is not None and not previous.get("evaluation_pending"):
                        continue
                    search_body = dict(user_id=user, query=question["question"], top_k=args.top_k)
                    if content is not None:
                        content.question(
                            qid, sample, question, body=search_body if previous is None else None
                        )
                    report.update(stage="search", current=qid)
                    persist()
                    cache_path = private_root / (hashlib.sha256(qid.encode()).hexdigest() + ".json")
                    if previous and previous.get("evaluation_pending"):
                        record = previous
                        contents = json.loads(cache_path.read_text())["contents"]
                        if content is not None:
                            content.resume_search(contents)
                    elif question.get("is_multi_modality"):
                        record = {"skipped": "multimodal"}
                    else:
                        start = time.monotonic()
                        result = SearchResponse.model_validate(
                            call(
                                http,
                                "/search",
                                search_body,
                                qid,
                            )
                        )
                        if len(result.data) > args.top_k:
                            raise ValueError("Search exceeded top_k")
                        contents = [r.content for r in result.data]
                        ranked_ids, mapping = manifest.audit(contents)
                        record = {
                            "source_attribution": mapping,
                            "category": question["category"],
                            "returned": len(result.data),
                            "latency_seconds": time.monotonic() - start,
                            "evidence_recall": recall(question.get("evidence", []), ranked_ids),
                            "retrieved_evidence_ids": sorted(
                                {s for ids in ranked_ids for s in ids}
                            ),
                            "evidence_recall_at_k": prefix_recalls(
                                question.get("evidence", []), ranked_ids, args.top_k
                            ),
                            "unmatched_gold_evidence": sorted(
                                set(question.get("evidence", [])) - source_ids
                            ),
                        }
                        if content is not None:
                            content.question(
                                qid,
                                sample,
                                question,
                                result=[r.model_dump(exclude_none=True) for r in result.data],
                            )
                        evidence_dir = args.output.parent / (args.output.stem + ".evidence")
                        evidence_dir.mkdir(exist_ok=True)
                        evidence_path = evidence_dir / (
                            hashlib.sha256(qid.encode()).hexdigest() + ".json"
                        )
                        save(
                            evidence_path,
                            {
                                "question_id": qid,
                                "retrieved": [
                                    evidence_audit(r.model_dump(exclude_none=True))
                                    for r in result.data
                                ],
                            },
                        )
                        record["evidence_file"] = str(evidence_path.relative_to(args.output.parent))
                    record["sample_id"] = sample
                    if evaluator and "skipped" not in record:
                        if not previous:
                            save(cache_path, {"contents": contents})
                            record["evaluation_pending"] = True
                            record["evaluation_status"] = "pending"
                            report["questions"][qid] = record
                            save_question(args.output, qid, record)
                            persist()
                        report.update(stage="answer-eval", current=qid)
                        persist()
                        if content is not None:
                            content.question(qid, sample, question, metrics=record)
                        try:
                            evaluation = evaluator.evaluate(
                                question,
                                contents,
                                cache_path,
                                reader_records=manifest.reader_records(contents)
                                if args.kind == "longmemeval"
                                else None,
                            )
                        except ModelError as exc:
                            evaluation = exc.partial_result
                            record.update(
                                evaluation_status=f"{exc.phase}_failed",
                                evaluation_pending=True,
                                evaluation_error=exc.audit(),
                            )
                        else:
                            record["evaluation_status"] = (
                                "skipped" if "skipped" in evaluation else "completed"
                            )
                            record.pop("evaluation_pending", None)
                            record.pop("evaluation_error", None)
                        ranked_ids, _ = manifest.audit(contents)
                        if "context_records" in evaluation:
                            evaluation["budget_evidence_recall"] = recall(
                                question.get("evidence", []),
                                [
                                    ranked_ids[index]
                                    for index in evaluation["context_record_indices"]
                                ]
                                if "context_record_indices" in evaluation
                                else ranked_ids[: evaluation["context_records"]],
                            )
                        record["evaluation"] = evaluation
                        record["model_diagnostics"] = list(evaluator.model_attempts)
                        if record.get("evaluation_error", {}).get("fatal"):
                            if content is not None:
                                content.question(qid, sample, question, metrics=record)
                            report["questions"][qid] = record
                            save_question(args.output, qid, record)
                            persist()
                            error = record["evaluation_error"]
                            raise ModelError(
                                error["phase"],
                                error["reason_code"],
                                error["http_status"],
                                fatal=True,
                            )
                    report["questions"][qid] = record
                    if content is not None:
                        content.question(qid, sample, question, metrics=record)
                    save_question(args.output, qid, record)
                    persist()
                print(f"Finished {sample}", flush=True)
        report["status"] = (
            "partial"
            if any(q.get("evaluation_pending") for q in report["questions"].values())
            else "completed"
        )
    except (Exception, KeyboardInterrupt) as exc:
        report.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            error={"type": type(exc).__name__},
        )
        if isinstance(exc, ModelError):
            report["error"].update(phase=exc.phase, reason_code=exc.reason, http_status=exc.status)
        if isinstance(exc, httpx.HTTPStatusError):
            report["error"]["http_status"] = exc.response.status_code
            if code := adapter_reason_code(exc.response):
                report["error"]["adapter_reason_code"] = code
        # Never persist arbitrary upstream bodies or exception text containing secrets.
    finally:
        if report["status"] in {"completed", "partial", "failed", "interrupted"}:
            apply_policy(report, datetime.now(UTC))
        report["summary"] = summarize(report)
        try:
            if content is not None:
                content.render(report["status"], report["summary"], report["error"])
        finally:
            # A separate private output disk failing must not discard benchmark results.
            persist(final=True)
    print(json.dumps({k: report[k] for k in ("status", "error", "summary")}, indent=2))
    return 0 if report["status"] == "completed" else 1


def main(default_kind="locomo", default_output=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=DATASETS, default=default_kind)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        required=default_output is None,
        default=Path(default_output) if default_output else None,
    )
    parser.add_argument("--adapter-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--conversations",
        "--limit",
        type=int,
        default=10,
        help="First N complete histories (default: 10)",
    )
    parser.add_argument("--all", action="store_true", help="Run every history in this dataset")
    parser.add_argument("--top-k", type=int, default=100)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--mode", choices=["retrieval", "answer-eval"], default="retrieval")
    parser.add_argument(
        "--private-dir", type=Path, help="Private resumable answer cache; do not upload"
    )
    parser.add_argument("--runtime-label", help="Record the backend revision for this attempt")
    parser.add_argument(
        "--content-diagnostics",
        action="store_true",
        help="Opt in to private local content reports for independent data only",
    )
    parser.add_argument(
        "--diagnostic-source", type=Path, help="Reviewed source/license/retention declaration JSON"
    )
    parser.add_argument("--diagnostic-max-mib", type=int, default=64)
    parser.add_argument("--diagnostic-max-questions", type=int, default=100)
    args = parser.parse_args()
    args.dataset = args.dataset or Path(DATASETS[args.kind]["path"])
    if args.conversations < 1 or not 1 <= args.top_k <= 100:
        parser.error("conversations must be positive; top-k must be 1–100")
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
