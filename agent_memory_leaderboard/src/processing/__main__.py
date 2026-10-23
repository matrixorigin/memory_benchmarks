"""Explicit offline commands, private artifacts, count-only console output."""

import argparse
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import httpx

from ..config import Settings
from ..memoria import Memoria
from ..models import SearchResponse
from .experiment import Publication, publish, search
from .extract import Extractor, ProcessingError, ProcessorConfig
from .models import History, Snapshot, read_json
from .prompt import messages


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=".processing-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def load(path, model):
    with path.open("rb") as stream:
        data = stream.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024:
        raise ProcessingError("artifact_too_large")
    return model.model_validate(read_json(data))


@contextmanager
def output_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Leave the lock inode in place; unlinking it introduces a split-lock race.
    descriptor = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProcessingError("output_in_use") from None
        yield
    finally:
        os.close(descriptor)


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("prompt", help="Print the project-authored extraction instructions")
    extract = commands.add_parser("extract", help="Process public/synthetic history only")
    extract.add_argument("--input", type=Path, required=True)
    extract.add_argument("--output", type=Path, required=True)
    extract.add_argument("--enable-processing", action="store_true")
    extract.add_argument("--resume", action="store_true")
    publication = commands.add_parser(
        "publish", help="Copy snapshot into isolated experimental owners"
    )
    publication.add_argument("--input", type=Path, required=True, help="Extraction snapshot")
    publication.add_argument("--output", type=Path, required=True)
    publication.add_argument("--experiment-id", required=True)
    publication.add_argument("--enable-processing", action="store_true")
    publication.add_argument("--derived-only", action="store_true")
    retrieval = commands.add_parser("search", help="Raw or raw+derived retrieval; no answering LLM")
    retrieval.add_argument("--input", type=Path, required=True, help="Publication manifest")
    retrieval.add_argument("--output", type=Path, required=True)
    retrieval.add_argument("--query-file", type=Path, required=True)
    retrieval.add_argument("--enable-derived-search", action="store_true")
    retrieval.add_argument("--baseline-file", type=Path)
    retrieval.add_argument("--allow-partial-extraction", action="store_true")
    retrieval.add_argument("--top-k", type=int, default=100)
    retrieval.add_argument("--token-budget", type=int, default=8192)
    retrieval.add_argument("--encoding", default="o200k_base")
    return root


def execute(args):
    if args.command == "prompt":
        print(messages([])[0]["content"])
        return 0
    if args.command in {"extract", "publish"} and not args.enable_processing:
        print('{"status":"disabled"}')
        return 0
    if args.input.resolve() == args.output.resolve():
        raise ProcessingError("input_output_must_differ")
    if args.command == "search" and args.query_file.resolve() == args.output.resolve():
        raise ProcessingError("input_output_must_differ")
    if (
        args.command == "search"
        and args.baseline_file is not None
        and args.baseline_file.resolve() == args.output.resolve()
    ):
        raise ProcessingError("input_output_must_differ")
    with output_lock(args.output):
        if args.command == "extract":
            history = load(args.input, History)
            prior = load(args.output, Snapshot) if args.output.exists() and args.resume else None
            if args.output.exists() and not args.resume:
                raise ProcessingError("output_exists_use_resume")
            config = ProcessorConfig.from_env()
            with httpx.Client(trust_env=False) as client:
                snapshot = Extractor(config, client).run(
                    history,
                    enabled=True,
                    previous=prior,
                    checkpoint=lambda value: save(args.output, value.model_dump()),
                )
            print(
                json.dumps(
                    {
                        "windows": len(snapshot.results),
                        "complete_windows": sum(r.status == "complete" for r in snapshot.results),
                        "partial_windows": sum(r.status == "partial" for r in snapshot.results),
                        "failed_windows": sum(r.status == "failed" for r in snapshot.results),
                        "derived_memories": len(snapshot.memories()),
                    }
                )
            )
            return 0 if snapshot.complete else 2
        with httpx.Client(trust_env=False) as client:
            memoria = Memoria(Settings.from_env(), client)
            if args.command == "publish":
                snapshot = load(args.input, Snapshot)
                previous = load(args.output, Publication) if args.output.exists() else None

                def checkpoint(value):
                    if previous and (
                        previous.snapshot_hash != value.snapshot_hash
                        or previous.raw_owner != value.raw_owner
                        or previous.derived_owner != value.derived_owner
                    ):
                        raise ProcessingError("publication_output_mismatch")
                    save(args.output, value.model_dump())

                result = publish(
                    snapshot,
                    memoria,
                    args.experiment_id,
                    enabled=True,
                    checkpoint=checkpoint,
                    derived_only=args.derived_only,
                    previous=previous,
                )
                print(
                    json.dumps(
                        {
                            "raw_ready": bool(result.raw_ready),
                            "derived_ready": bool(result.derived_ready),
                            "failed_records": sum(
                                r.status == "failed" for r in [*result.raw, *result.derived]
                            ),
                        }
                    )
                )
                return (
                    0 if (result.raw_ready or result.derived_only) and result.derived_ready else 2
                )
            if args.output.exists():
                raise ProcessingError("search_output_exists")
            with args.query_file.open(encoding="utf-8") as stream:
                query = stream.read(100001)
            result = search(
                load(args.input, Publication),
                memoria,
                query,
                enable_derived=args.enable_derived_search,
                top_k=args.top_k,
                token_budget=args.token_budget,
                encoding=args.encoding,
                baseline=load(args.baseline_file, SearchResponse).model_dump()
                if args.baseline_file
                else None,
                allow_partial_extraction=args.allow_partial_extraction,
            )
            save(args.output, result)
            print(
                json.dumps(
                    {
                        k: result[k]
                        for k in ("derived_status", "selected_records", "elapsed_seconds")
                    }
                )
            )
            return 0


def main(argv=None):
    os.umask(0o077)
    args = parser().parse_args(argv)
    try:
        return execute(args)
    except ProcessingError as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}))
        return 2
    except Exception:
        # ValidationError, HTTP exceptions, paths, URLs and upstream bodies can
        # contain payloads or credentials. Never print them or their traceback.
        print('{"status":"failed","reason":"configuration_or_operation_failed"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
