"""Materialize private target identifiers from CI variables, without printing them."""

import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "pulumi"
    replacements = {
        "__AML_EXPECTED_API_SERVER__": os.getenv("AML_EXPECTED_API_SERVER", ""),
        "__AML_EXPECTED_CLUSTER_UID__": os.getenv("AML_EXPECTED_CLUSTER_UID", ""),
        "__AML_DATABASE_ACCOUNT__": os.getenv("AML_DATABASE_ACCOUNT", ""),
    }
    if not all(replacements.values()):
        raise SystemExit(
            "Configure AML_EXPECTED_API_SERVER, AML_EXPECTED_CLUSTER_UID and "
            "AML_DATABASE_ACCOUNT repository Variables"
        )
    try:
        server = urlsplit(replacements["__AML_EXPECTED_API_SERVER__"])
        server.port  # Reject malformed ports before writing YAML.
    except ValueError:
        raise SystemExit("AML_EXPECTED_API_SERVER must be a valid HTTPS API URL") from None
    if (server.scheme != "https" or not server.hostname or server.username or server.password
            or server.query or server.fragment or server.path not in {"", "/"}):
        raise SystemExit("AML_EXPECTED_API_SERVER must be an HTTPS Kubernetes API URL")
    for marker in ["__AML_EXPECTED_CLUSTER_UID__", "__AML_DATABASE_ACCOUNT__"]:
        value = replacements[marker]
        try:
            canonical = str(UUID(value))
        except ValueError:
            raise SystemExit("Cluster UID and database account must be canonical UUIDs") from None
        if canonical != value:
            raise SystemExit("Cluster UID and database account must be canonical UUIDs")
    text = (root / "Pulumi.new-dev.example.yaml").read_text()
    # Strictly validated URL/UUID values are quoted YAML scalars in the template.
    for marker, value in replacements.items():
        if any(char in value for char in "'\"\r\n\\ "):
            raise SystemExit("Dev identifiers contain unsupported characters")
        text = text.replace(marker, value)
    target = root / "Pulumi.new-dev.yaml"
    target.write_text(text)
    target.chmod(0o600)
    print("Private dev configuration generated; target identifiers were not logged")


if __name__ == "__main__":
    main()
