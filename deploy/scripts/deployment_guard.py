"""Reject deployments during an evaluation freeze; preview remains available."""

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation", choices=["preview", "deploy"], required=True)
    args = parser.parse_args()
    frozen = os.getenv("AML_FREEZE", "").strip().lower()
    if frozen not in {"", "false", "true"}:
        raise SystemExit("AML_FREEZE must be true or false; refusing to continue")
    if args.operation == "deploy" and frozen == "true":
        raise SystemExit("Deployment blocked by AML_FREEZE=true; evaluation release is frozen")
    print(f"Release guard passed for {args.operation}")


if __name__ == "__main__":
    main()
