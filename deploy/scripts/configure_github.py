"""Upload dev credentials through stdin, without printing secret values."""

import argparse
import json
import subprocess
from pathlib import Path

import yaml


def gh(*arguments: str, value: str) -> None:
    result = subprocess.run(
        ["gh", *arguments], input=value, text=True, capture_output=True
    )
    if result.returncode:
        raise RuntimeError("GitHub configuration update failed; check repository permissions")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--runtime-file", type=Path, default=Path("deploy/.runtime.env"))
    parser.add_argument("--adapter-repository", required=True)
    parser.add_argument("--memoria-image")
    parser.add_argument("--memoria-build-mode", choices=["source", "image"], default="source")
    parser.add_argument("--memoria-ref")
    parser.add_argument("--memoria-repository")
    args = parser.parse_args()
    if args.memoria_build_mode == "image" and not args.memoria_image:
        parser.error("Image mode requires --memoria-image pinned to a digest")
    repo = "matrixorigin/memory_benchmarks"
    config_path = Path(__file__).resolve().parents[1] / "pulumi/Pulumi.new-dev.yaml"
    config = yaml.safe_load(config_path.read_text())["config"]
    kubeconfig = args.kubeconfig.expanduser()
    command = ["kubectl", "--kubeconfig", str(kubeconfig), "--request-timeout=20s"]
    result = subprocess.run(
        command + ["config", "view", "--minify", "-o", "json"],
        capture_output=True, text=True, check=True,
    )
    server = json.loads(result.stdout)["clusters"][0]["cluster"]["server"]
    if server != config["memory-benchmarks:expectedApiServer"]:
        raise ValueError("Only the configured Aliyun dev kubeconfig may be uploaded")
    result = subprocess.run(
        command + ["get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}"],
        capture_output=True, text=True, check=True,
    )
    if result.stdout != config["memory-benchmarks:expectedClusterUID"]:
        raise ValueError("Dev cluster UID does not match")
    runtime = {}
    for line in args.runtime_file.read_text().splitlines():
        key, _, value = line.partition("=")
        runtime[key] = json.loads(value)
    keys = ["AML_DATABASE_PASSWORD", "AML_MEMORIA_MASTER_KEY", "AML_ADAPTER_API_KEY"]
    for name in keys:
        if not runtime.get(name):
            raise ValueError("Runtime credential file is incomplete")
    gh("secret", "set", "KUBECONFIG", "--repo", repo, value=kubeconfig.read_text())
    print("Updated KUBECONFIG with the Aliyun dev unit configuration")
    for name in keys:
        gh("secret", "set", name, "--repo", repo, value=runtime[name])
        print(f"Configured Secret {name}")
    variables = {
        "AML_EXPECTED_API_SERVER": str(config["memory-benchmarks:expectedApiServer"]),
        "AML_EXPECTED_CLUSTER_UID": str(config["memory-benchmarks:expectedClusterUID"]),
        "AML_DATABASE_ACCOUNT": str(config["memory-benchmarks:databaseAccount"]),
        "AML_ADAPTER_REPOSITORY": args.adapter_repository,
        "AML_MEMORIA_REPOSITORY": args.memoria_repository or args.adapter_repository,
        "AML_MEMORIA_BUILD_MODE": args.memoria_build_mode,
    }
    for name, value in {
        "AML_MEMORIA_IMAGE": args.memoria_image,
        "AML_MEMORIA_REF": args.memoria_ref,
    }.items():
        if value is not None:
            variables[name] = value
    for name, value in variables.items():
        gh("variable", "set", name, "--repo", repo, value=value)
        print(f"Configured Variable {name}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"GitHub configuration failed ({type(error).__name__})") from None
