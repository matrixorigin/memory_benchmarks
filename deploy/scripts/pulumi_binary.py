"""Configure this CI checkout to run the precompiled Go Pulumi program."""

import argparse
import os
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--project", type=Path, default=Path("deploy/pulumi/Pulumi.yaml"))
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise SystemExit("The precompiled Pulumi program must be an executable file")
    project = yaml.safe_load(args.project.read_text())
    runtime = project.get("runtime")
    if runtime == "go":
        runtime = {"name": "go", "options": {}}
    if not isinstance(runtime, dict) or runtime.get("name") != "go":
        raise SystemExit("Only the Go Pulumi runtime supports this deployment binary")
    runtime.setdefault("options", {})["binary"] = str(binary)
    project["runtime"] = runtime
    args.project.write_text(yaml.safe_dump(project, sort_keys=False))
    print("Configured Pulumi to use the precompiled deployment program")


if __name__ == "__main__":
    main()
