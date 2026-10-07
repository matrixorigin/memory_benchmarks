"""Select app updates and retain the deployed values for an unselected app."""

import argparse
import base64
import hashlib
import hmac
import json
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from memoria_image import IMAGE_PATTERN, append_values, checked

TARGETS = {"both", "memoria", "memory_benchmarks"}
NAMESPACE = "aml-memory-benchmarks"


def target() -> str:
    value = os.getenv("AML_DEPLOYMENT_TARGET", "both") or "both"
    if value not in TARGETS:
        raise ValueError("deployment_target must be both, memoria, or memory_benchmarks")
    return value


def command(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if result.returncode:
        # Never relay kubectl output: it can contain runtime credentials.
        raise ValueError(
            "Cannot read the existing AML deployment; first deploy both services "
            "and check cluster access before selecting a single service"
        )
    return result.stdout


def plan() -> None:
    value = target()
    append_values(
        "GITHUB_OUTPUT",
        {
            "update_adapter": str(value in {"both", "memory_benchmarks"}).lower(),
            "update_memoria": str(value in {"both", "memoria"}).lower(),
        },
    )
    print(f"Deployment target: {value}")


def verify_credentials(data: dict, config: dict) -> None:
    try:
        decoded = {
            key: base64.b64decode(value, validate=True).decode() for key, value in data.items()
        }
        for key, env in {
            "MASTER_KEY": "AML_MEMORIA_MASTER_KEY",
            "ADAPTER_API_KEY": "AML_ADAPTER_API_KEY",
            "EMBEDDING_API_KEY": "AML_EMBEDDING_API_KEY",
        }.items():
            if not hmac.compare_digest(decoded[key].encode(), os.environ[env].encode()):
                raise ValueError
        url = urlsplit(decoded["DATABASE_URL"])

        def setting(name):
            return str(config[f"memory-benchmarks:{name}"])

        username = ":".join(
            setting(name) for name in ("databaseAccount", "databaseUser", "databaseRole")
        )
        if (
            url.scheme != "mysql"
            or unquote(url.username or "") != username
            or url.hostname != setting("databaseHost")
            or url.port != int(setting("databasePort"))
            or url.path != "/" + setting("databaseName")
            or not hmac.compare_digest(
                unquote(url.password or "").encode(), os.environ["AML_DATABASE_PASSWORD"].encode()
            )
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise ValueError(
            "Shared runtime credentials/configuration differ from the deployed "
            "version; use deployment_target=both to update them together"
        ) from None


def image(values: dict, app: str) -> str:
    mapping = values["image"] if app == "adapter" else values["memoria"]["image"]
    return checked(
        IMAGE_PATTERN,
        mapping["repository"] + "@" + mapping["digest"],
        "Existing application image must be pinned by digest; deploy both first",
    )


def deployment(manifest: str, name: str) -> dict:
    import yaml

    for resource in yaml.safe_load_all(manifest):
        if (
            resource
            and resource.get("kind") == "Deployment"
            and resource.get("metadata", {}).get("name") == name
        ):
            return resource
    raise ValueError("Existing AML deployment is incomplete; deploy both first")


def capture() -> dict:
    if target() == "both":
        return {}
    import yaml

    config = yaml.safe_load(Path("deploy/pulumi/Pulumi.new-dev.yaml").read_text())["config"]
    if os.getenv("AML_VERIFIED_CLUSTER_UID") != config["memory-benchmarks:expectedClusterUID"]:
        raise ValueError("Run the cluster preflight before selecting existing application values")
    # --all retains computed defaults, including resource/probe/timeout settings.
    values = json.loads(
        command("helm", "get", "values", "aml", "-n", NAMESPACE, "--all", "-o", "json")
    )
    if (
        values.get("authSecret") != "aml-runtime"
        or values.get("imagePullSecret") != "aml-registry"
        or values.get("stateClaim") != "aml-adapter-state"
    ):
        raise ValueError("Existing release does not match the dedicated AML configuration")
    data = json.loads(
        command("kubectl", "-n", NAMESPACE, "get", "secret", "aml-runtime", "-o", "json")
    )["data"]
    verify_credentials(data, config)
    for app, name in [("adapter", "aml"), ("memoria", "aml-memoria")]:
        resource = json.loads(
            command("kubectl", "-n", NAMESPACE, "get", "deployment", name, "-o", "json")
        )
        containers = resource["spec"]["template"]["spec"]["containers"]
        if next((c["image"] for c in containers if c["name"] == app), None) != image(values, app):
            raise ValueError("Live image differs from Helm values; reconcile with both first")
    # A chart edit can roll the other application even with the same image. Reject
    # such partial releases instead of silently applying the new pod template.
    manifest = command("helm", "get", "manifest", "aml", "-n", NAMESPACE)
    with tempfile.TemporaryDirectory(prefix="aml-values-") as directory:
        path = Path(directory) / "values.json"
        path.write_text(json.dumps(values))
        rendered = command(
            "helm",
            "template",
            "aml",
            "deploy/chart",
            "-n",
            NAMESPACE,
            "--kube-version",
            "1.28.9",
            "-f",
            str(path),
        )
    retained = "aml-memoria" if target() == "memory_benchmarks" else "aml"
    if deployment(manifest, retained)["spec"] != deployment(rendered, retained)["spec"]:
        raise ValueError("Chart changes would update the unselected application; deploy both")
    return {"deployment_target": target(), "values": values}


def reuse(path: Path) -> None:
    snapshot = capture()
    if not snapshot:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as output:
        os.chmod(path, 0o600)
        json.dump(snapshot, output)
    values = snapshot["values"]
    env = {"AML_RETAINED_VALUES_FILE": str(path.resolve())}
    if target() == "memoria":
        env["AML_ADAPTER_IMAGE"] = image(values, "adapter")
    else:
        memoria = values["memoria"]
        env.update(
            {
                "AML_MEMORIA_IMAGE": image(values, "memoria"),
                "AML_MEMORIA_BUILD_MODE": "reuse",
                "AML_MEMORIA_SOURCE_REPOSITORY": memoria.get("sourceRepository", ""),
                "AML_MEMORIA_SOURCE_REF": memoria.get("sourceRef", ""),
                "AML_MEMORIA_SOURCE_COMMIT": memoria.get("sourceCommit", ""),
            }
        )
    ingress = values.get("ingress", {})
    env["AML_PUBLIC_HOST"] = ingress.get("host", "") if ingress.get("enabled") else ""
    append_values("GITHUB_ENV", env)
    print("Retained the unselected application's deployed image and chart configuration")


def snapshot_digest(snapshot: dict) -> str:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def fingerprint(path: Path) -> None:
    snapshot = json.loads(path.read_text()) if target() != "both" else {}
    append_values("GITHUB_OUTPUT", {"retained_snapshot_sha256": snapshot_digest(snapshot)})
    print("Recorded a digest of the retained configuration; values remain on this runner")


def recheck(path: Path) -> None:
    snapshot = json.loads(path.read_text()) if target() != "both" else {}
    expected = os.getenv("AML_RETAINED_SNAPSHOT_SHA256")
    if expected is not None and not hmac.compare_digest(snapshot_digest(snapshot), expected):
        raise ValueError("The retained configuration changed between jobs; start a new run")
    if target() != "both" and snapshot != capture():
        raise ValueError("The deployed configuration changed during the build; start a new run")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["plan", "reuse", "fingerprint", "recheck"])
    parser.add_argument("--snapshot", type=Path, default=Path("deploy/.retained-values.json"))
    args = parser.parse_args()
    if args.operation == "plan":
        plan()
    elif args.operation == "reuse":
        reuse(args.snapshot)
    elif args.operation == "fingerprint":
        fingerprint(args.snapshot)
    else:
        recheck(args.snapshot)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Explicit validation messages contain no credentials or command output.
        message = "Invalid existing deployment configuration; deploy both services first"
        if type(error) is ValueError:
            message = str(error)
        raise SystemExit(message) from None
