"""Select a source ref and record the actual commit and Memoria deployment image."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

COMMIT_PATTERN = re.compile(r"[a-f0-9]{40}")
DIGEST_PATTERN = re.compile(r"sha256:[a-f0-9]{64}")
SOURCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*")
ACR_PATTERN = re.compile(
    r"registry\.cn-hangzhou\.aliyuncs\.com/[a-z0-9_-]+/[a-z0-9_.-]+"
)
IMAGE_PATTERN = re.compile(r"[^\s@]+@sha256:[a-f0-9]{64}")
REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")


def append_values(target: str, values: dict[str, str]) -> None:
    path = os.getenv(target)
    if path:
        with Path(path).open("a") as output:
            for key, value in values.items():
                if "\n" in value or "\r" in value:
                    raise ValueError(f"Invalid multiline value for {key}")
                output.write(f"{key}={value}\n")


def checked(pattern: re.Pattern[str], value: str, description: str) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(description)
    return value


def resolve(args: argparse.Namespace) -> None:
    config = json.loads(args.config.read_text())
    mode = args.mode or os.getenv("AML_MEMORIA_BUILD_MODE") or config["default_mode"]
    if mode not in {"source", "image"}:
        raise ValueError("Memoria build mode must be source or image")
    repository, source_ref, image_repository, image = "", "", "", ""
    if mode == "source":
        repository = checked(
            SOURCE_PATTERN, config["repository"], "Source repository must be owner/repository"
        )
        source_ref = checked(
            REF_PATTERN, os.getenv("AML_MEMORIA_REF") or config["ref"],
            "Memoria source ref must be a branch, tag or commit SHA",
        )
        result = subprocess.run(
            ["git", "check-ref-format", "--branch", source_ref],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode:
            raise ValueError("Invalid Memoria source ref")
        if args.publish:
            adapter_repository = checked(
                ACR_PATTERN, os.getenv("AML_ADAPTER_REPOSITORY", ""),
                "AML_ADAPTER_REPOSITORY must be a Hangzhou ACR repository",
            )
            image_repository = checked(
                ACR_PATTERN,
                os.getenv("AML_MEMORIA_REPOSITORY") or adapter_repository,
                "AML_MEMORIA_REPOSITORY must be a Hangzhou ACR repository",
            )
    else:
        image = checked(
            IMAGE_PATTERN, os.getenv("AML_MEMORIA_IMAGE", ""),
            "Image mode requires AML_MEMORIA_IMAGE pinned to repository@sha256:digest",
        )

    append_values("GITHUB_OUTPUT", {
        "mode": mode, "source_repository": repository, "source_ref": source_ref,
        "image_repository": image_repository,
        "cache_scope": repository.replace("/", "-"),
    })
    append_values("GITHUB_ENV", {
        "AML_MEMORIA_BUILD_MODE": mode,
        "AML_MEMORIA_SOURCE_REPOSITORY": repository,
        "AML_MEMORIA_SOURCE_REF": source_ref,
        "AML_MEMORIA_SOURCE_COMMIT": "",
        "AML_MEMORIA_REPOSITORY": image_repository,
    })
    print(json.dumps({
        "mode": mode, "repository": repository, "ref": source_ref,
        "image_repository": image_repository, "image": image,
    }))


def record_checkout(args: argparse.Namespace) -> None:
    commit = subprocess.check_output(
        ["git", "-C", str(args.source_dir), "rev-parse", "HEAD"], text=True
    ).strip()
    checked(COMMIT_PATTERN, commit, "The Memoria checkout did not resolve to a full commit SHA")
    for relative in ("memoria/Cargo.toml", "memoria/Cargo.lock"):
        if not (args.source_dir / relative).is_file():
            raise ValueError(f"The selected Memoria ref has no {relative}")
    append_values("GITHUB_OUTPUT", {"source_commit": commit})
    append_values("GITHUB_ENV", {"AML_MEMORIA_SOURCE_COMMIT": commit})
    print(f"Selected Memoria ref {os.environ['AML_MEMORIA_SOURCE_REF']} at commit {commit}")


def prepare_tags(_args: argparse.Namespace) -> None:
    build_date = datetime.now(UTC).strftime("%Y%m%d")
    adapter_repository = checked(
        ACR_PATTERN, os.environ["AML_ADAPTER_REPOSITORY"],
        "Adapter image repository must be a Hangzhou ACR repository",
    )
    adapter_commit = checked(
        COMMIT_PATTERN, os.environ["GITHUB_SHA"], "Benchmark commit must be a full SHA"
    )
    adapter_tag = (
        f"{adapter_repository}:aml-memory-adapter-{build_date}-{adapter_commit[:7]}"
    )
    memoria_tag = ""
    if os.environ["AML_MEMORIA_BUILD_MODE"] == "source":
        memoria_repository = checked(
            ACR_PATTERN, os.environ["AML_MEMORIA_REPOSITORY"],
            "Memoria image repository must be a Hangzhou ACR repository",
        )
        memoria_commit = checked(
            COMMIT_PATTERN, os.environ["AML_MEMORIA_SOURCE_COMMIT"],
            "Memoria source commit must be a full SHA",
        )
        memoria_tag = f"{memoria_repository}:aml-memoria-{build_date}-{memoria_commit[:7]}"
    elif os.environ["AML_MEMORIA_BUILD_MODE"] != "image":
        raise ValueError("Memoria build mode must be source or image")

    append_values("GITHUB_OUTPUT", {
        "adapter_tag": adapter_tag, "memoria_tag": memoria_tag, "build_date": build_date,
    })
    append_values("GITHUB_ENV", {
        "AML_ADAPTER_TAG": adapter_tag, "AML_MEMORIA_TAG": memoria_tag,
        "AML_BUILD_DATE_UTC": build_date,
    })
    print(json.dumps({"adapter_tag": adapter_tag, "memoria_tag": memoria_tag}))


def pin(args: argparse.Namespace) -> None:
    mode = os.environ["AML_MEMORIA_BUILD_MODE"]
    repository, source_ref, commit = "", "", ""
    if mode == "source":
        digest = checked(
            DIGEST_PATTERN, os.getenv("BUILT_MEMORIA_DIGEST", ""),
            "Memoria build did not return a valid sha256 digest",
        )
        image_repository = checked(
            ACR_PATTERN, os.environ["AML_MEMORIA_REPOSITORY"],
            "Memoria image repository must be a Hangzhou ACR repository",
        )
        repository = checked(
            SOURCE_PATTERN, os.environ["AML_MEMORIA_SOURCE_REPOSITORY"],
            "Memoria source repository must be owner/repository",
        )
        commit = checked(
            COMMIT_PATTERN, os.environ["AML_MEMORIA_SOURCE_COMMIT"],
            "Memoria source commit must be a full SHA",
        )
        source_ref = checked(
            REF_PATTERN, os.environ["AML_MEMORIA_SOURCE_REF"],
            "Memoria source ref must be a branch, tag or commit SHA",
        )
        image = image_repository + "@" + digest
    elif mode == "image":
        image = checked(
            IMAGE_PATTERN, os.getenv("AML_MEMORIA_IMAGE", ""),
            "Memoria image must be pinned to repository@sha256:digest",
        )
    else:
        raise ValueError("Memoria build mode must be source or image")

    metadata = {
        "mode": mode, "source_repository": repository,
        "source_ref": source_ref, "source_commit": commit,
        "image": image, "platform": "linux/amd64",
        "build_tag": os.getenv("AML_MEMORIA_TAG", ""),
        "adapter_build_tag": os.getenv("AML_ADAPTER_TAG", ""),
        "build_date_utc": os.getenv("AML_BUILD_DATE_UTC", ""),
        "benchmark_commit": os.getenv("GITHUB_SHA", ""),
        "actions_run_id": os.getenv("GITHUB_RUN_ID", ""),
        "actions_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", ""),
    }
    if mode == "source":
        dockerfile = Path(__file__).resolve().parents[1] / "docker/memoria.Dockerfile"
        metadata["dockerfile"] = "deploy/docker/memoria.Dockerfile"
        metadata["dockerfile_sha256"] = hashlib.sha256(dockerfile.read_bytes()).hexdigest()
    args.metadata_file.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_file.write_text(json.dumps(metadata, indent=2) + "\n")
    append_values("GITHUB_ENV", {"AML_MEMORIA_IMAGE": image})
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a") as output:
            output.write("## Memoria image selected for this run\n\n")
            output.write(f"- Mode: `{mode}`\n")
            if commit:
                output.write(f"- Source: `{repository}`, ref `{source_ref}`, commit `{commit}`\n")
                output.write(f"- Build tag: `{metadata['build_tag']}`\n")
            output.write(f"- Image: `{image}`\n\n")
            output.write(
                "This records the image selection; deployment may still fail or be previewed.\n"
            )
    print("Recorded the pinned Memoria image and source metadata")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("resolve")
    prepare.add_argument(
        "--config", type=Path,
        default=Path(__file__).resolve().parents[1] / "memoria-source.json",
    )
    prepare.add_argument("--mode", choices=["source", "image"])
    prepare.add_argument("--publish", action="store_true")
    prepare.set_defaults(run=resolve)
    checkout = commands.add_parser("checkout")
    checkout.add_argument("--source-dir", type=Path, default=Path(".memoria-source"))
    checkout.set_defaults(run=record_checkout)
    tags = commands.add_parser("tags")
    tags.set_defaults(run=prepare_tags)
    record = commands.add_parser("pin")
    record.add_argument("--metadata-file", type=Path, default=Path("deploy/memoria-build.json"))
    record.set_defaults(run=pin)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, TypeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Memoria image configuration failed: {error}") from None
