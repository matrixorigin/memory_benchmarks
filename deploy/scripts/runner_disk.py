"""Fail before expensive CI work when the runner has insufficient free disk."""

import argparse
import os
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum-free-gib", type=int, required=True)
    parser.add_argument("--phase", required=True)
    args = parser.parse_args()
    if args.minimum_free_gib <= 0:
        raise SystemExit("Minimum free disk must be a positive number of GiB")
    paths = {Path.cwd().resolve(), Path(os.getenv("RUNNER_TEMP", "/tmp")).resolve()}
    for path in sorted(paths):
        free = shutil.disk_usage(path).free / (1024 ** 3)
        print(f"{args.phase}: {free:.1f} GiB free at {path}", flush=True)
        if free < args.minimum_free_gib:
            raise SystemExit(
                f"Insufficient runner disk for {args.phase}: require at least "
                f"{args.minimum_free_gib} GiB free. Use a larger runner or free build "
                "storage; do not retry the same exhausted runner."
            )


if __name__ == "__main__":
    main()
