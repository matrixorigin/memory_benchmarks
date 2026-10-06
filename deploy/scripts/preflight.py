"""Verify the Aliyun unit cluster without printing kubeconfig credentials."""

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml


def kubectl(path: str, *args: str) -> str:
    result = subprocess.run(
        ["kubectl", "--kubeconfig", path, "--request-timeout=20s", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError("Kubernetes request failed; check connectivity and RBAC")
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kubeconfig-file", type=Path)
    args = parser.parse_args()
    config_path = Path(__file__).resolve().parents[1] / "pulumi/Pulumi.new-dev.yaml"
    config = yaml.safe_load(config_path.read_text())["config"]

    def setting(name: str) -> str:
        return str(config[f"memory-benchmarks:{name}"])

    content = (
        args.kubeconfig_file.expanduser().read_text()
        if args.kubeconfig_file
        else os.environ.get("AML_KUBECONFIG_CONTENT", "")
    )
    if not content:
        raise ValueError("KUBECONFIG must contain the raw Aliyun dev YAML, not a path or base64")
    descriptor, path = tempfile.mkstemp(prefix="aml-dev-", dir=os.getenv("RUNNER_TEMP"))
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(content)
        data = json.loads(kubectl(path, "config", "view", "--minify", "-o", "json"))
        if data["clusters"][0]["cluster"]["server"] != setting("expectedApiServer"):
            raise ValueError("kubeconfig API server does not match the Aliyun dev unit cluster")
        identity = json.loads(kubectl(path, "get", "namespace", "kube-system", "-o", "json"))
        if identity["metadata"]["uid"] != setting("expectedClusterUID"):
            raise ValueError("kube-system UID does not match the expected dev cluster")
        kubectl(path, "get", "storageclass", setting("storageClass"), "-o", "name")
        namespace = setting("namespace")
        permissions = [
            ("create", "namespaces", None),
            ("get", "namespaces", None),
            ("update", "namespaces", None),
            ("patch", "namespaces", None),
        ]
        for resource in [
            "deployments.apps", "services", "secrets", "configmaps",
            "persistentvolumeclaims", "ingresses.networking.k8s.io",
        ]:
            permissions.extend(
                (verb, resource, namespace)
                for verb in ["get", "list", "watch", "create", "update", "patch", "delete"]
            )
        for verb, resource, scope in permissions:
            command = ["auth", "can-i", verb, resource]
            if scope:
                command.extend(["-n", scope])
            if kubectl(path, *command).strip() != "yes":
                raise ValueError(f"Missing permission: {verb} {resource} in {scope or 'cluster'}")
        if os.getenv("AML_PUBLIC_HOST"):
            host = os.environ["AML_PUBLIC_HOST"]
            if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}", host):
                raise ValueError("AML_PUBLIC_HOST must be a lowercase fully qualified domain name")
            kubectl(path, "get", "ingressclass", setting("ingressClass"), "-o", "name")
            kubectl(path, "get", "crd", "certificates.cert-manager.io", "-o", "name")
            issuer_name = os.getenv("AML_CERT_ISSUER") or setting("certificateIssuer")
            issuer = json.loads(
                kubectl(path, "get", "clusterissuer", issuer_name, "-o", "json")
            )
            acme = issuer.get("spec", {}).get("acme", {})
            zones = [
                solver.get("dns01", {}).get("webhook", {}).get("config", {}).get("aliyunDomain")
                for solver in acme.get("solvers", [])
            ]
            if not any(zone and (host == zone or host.endswith("." + zone)) for zone in zones):
                raise ValueError("Public hostname is outside the configured Aliyun DNS solver zone")
            if not any(
                item.get("type") == "Ready" and item.get("status") == "True"
                for item in issuer.get("status", {}).get("conditions", [])
            ):
                raise ValueError("The certificate ClusterIssuer is not Ready")
            for verb in ["get", "list", "watch", "create", "update", "patch", "delete"]:
                allowed = kubectl(
                    path, "auth", "can-i", verb, "certificates.cert-manager.io", "-n", namespace
                )
                if allowed.strip() != "yes":
                    raise ValueError(f"Missing permission: {verb} certificates in {namespace}")
        github_env = os.getenv("GITHUB_ENV")
        if github_env:
            with open(github_env, "a") as output:
                output.write(f"KUBECONFIG={path}\n")
                output.write(f"AML_VERIFIED_CLUSTER_UID={identity['metadata']['uid']}\n")
        else:
            print("Dev cluster identity, storage class and deployment permissions confirmed")
            os.unlink(path)
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(str(error)) from None
