"""Release selection regressions; all cluster responses and credentials are synthetic."""

import argparse
import base64
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy/scripts"))
import deployment_target as dt  # noqa: E402
import memoria_image as mi  # noqa: E402


class DeploymentTargetTests(unittest.TestCase):
    def setUp(self):
        self.values = yaml.safe_load((ROOT / "deploy/chart/values.yaml").read_text())
        self.values["image"] = {
            "repository": "registry.example.invalid/adapter",
            "digest": "sha256:" + "1" * 64,
        }
        self.values["memoria"]["image"] = {
            "repository": "registry.example.invalid/memoria",
            "digest": "sha256:" + "2" * 64,
        }
        self.values["memoria"].update(
            sourceRepository="matrixorigin/memoria",
            sourceRef="release",
            sourceCommit="a" * 40,
            embeddingBaseURL="https://embedding.example.invalid/v1",
        )
        self.values["secretChecksum"] = "synthetic-checksum"
        self.config = {
            "memory-benchmarks:" + k: v
            for k, v in {
                "expectedClusterUID": "synthetic-cluster",
                "databaseAccount": "synthetic-account",
                "databaseUser": "aml_memoria",
                "databaseRole": "aml_memoria_role",
                "databaseName": "aml_memoria",
                "databaseHost": "db.example.invalid",
                "databasePort": "6001",
            }.items()
        }
        self.env = {
            "PATH": os.environ["PATH"],
            "AML_DEPLOYMENT_TARGET": "memory_benchmarks",
            "AML_VERIFIED_CLUSTER_UID": "synthetic-cluster",
            "AML_MEMORIA_MASTER_KEY": "synthetic-master",
            "AML_ADAPTER_API_KEY": "synthetic-adapter",
            "AML_EMBEDDING_API_KEY": "synthetic-model",
            "AML_DATABASE_PASSWORD": "synthetic-password",
            "AML_ADAPTER_REPOSITORY": "registry.cn-hangzhou.aliyuncs.com/test/plugin",
            "GITHUB_SHA": "b" * 40,
        }
        self.data = {
            k: base64.b64encode(v.encode()).decode()
            for k, v in {
                "MASTER_KEY": "synthetic-master",
                "ADAPTER_API_KEY": "synthetic-adapter",
                "EMBEDDING_API_KEY": "synthetic-model",
                "DATABASE_URL": "mysql://synthetic-account%3Aaml_memoria%3Aaml_memoria_role:"
                "synthetic-password@db.example.invalid:6001/aml_memoria",
            }.items()
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.json"
            path.write_text(json.dumps(self.values))
            self.manifest = subprocess.check_output(
                [
                    "helm",
                    "template",
                    "aml",
                    str(ROOT / "deploy/chart"),
                    "-n",
                    dt.NAMESPACE,
                    "--kube-version",
                    "1.28.9",
                    "-f",
                    str(path),
                ],
                text=True,
            )

    def cluster_command(self, *args):
        if args[:3] == ("helm", "get", "values"):
            return json.dumps(self.values)
        if args[:3] == ("helm", "get", "manifest"):
            return self.manifest
        if args[0] == "helm":
            return subprocess.check_output(args, text=True)
        if "secret" in args:
            return json.dumps({"data": self.data})
        app = "adapter" if args[5] == "aml" else "memoria"
        return json.dumps(
            {
                "spec": {
                    "template": {
                        "spec": {"containers": [{"name": app, "image": dt.image(self.values, app)}]}
                    }
                }
            }
        )

    def test_partial_selection_retains_deployed_memoria_source_not_configured_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            envfile = Path(directory) / "env"
            with (
                patch.dict(os.environ, self.env | {"GITHUB_ENV": str(envfile)}, clear=True),
                patch.object(
                    Path, "read_text", return_value=yaml.safe_dump({"config": self.config})
                ),
                patch.object(dt, "command", side_effect=self.cluster_command),
            ):
                snapshot = Path(directory) / "snapshot.json"
                dt.reuse(snapshot)
            selected = dict(line.split("=", 1) for line in envfile.read_text().splitlines())
            self.assertEqual(selected["AML_MEMORIA_IMAGE"], dt.image(self.values, "memoria"))
            self.assertEqual(selected["AML_MEMORIA_BUILD_MODE"], "reuse")
            self.assertEqual(selected["AML_MEMORIA_SOURCE_COMMIT"], "a" * 40)
            self.assertEqual(json.loads(snapshot.read_text())["values"], self.values)
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)

    def test_memoria_only_reuses_adapter_image(self):
        with tempfile.TemporaryDirectory() as directory:
            envfile = Path(directory) / "env"
            with (
                patch.dict(
                    os.environ,
                    self.env | {"GITHUB_ENV": str(envfile), "AML_DEPLOYMENT_TARGET": "memoria"},
                    clear=True,
                ),
                patch.object(
                    Path, "read_text", return_value=yaml.safe_dump({"config": self.config})
                ),
                patch.object(dt, "command", side_effect=self.cluster_command),
            ):
                dt.reuse(Path(directory) / "snapshot.json")
            selected = dict(line.split("=", 1) for line in envfile.read_text().splitlines())
            self.assertEqual(selected["AML_ADAPTER_IMAGE"], dt.image(self.values, "adapter"))

    def test_first_deployment_both_has_no_cluster_dependency(self):
        with (
            patch.dict(os.environ, {"AML_DEPLOYMENT_TARGET": "both"}, clear=True),
            patch.object(dt, "command", side_effect=AssertionError("Unexpected cluster access")),
        ):
            self.assertEqual(dt.capture(), {})

    def test_shared_credentials_change_requires_both_without_logging_credentials(self):
        with patch.dict(
            os.environ, self.env | {"AML_MEMORIA_MASTER_KEY": "rotated-secret"}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "deployment_target=both") as error:
                dt.verify_credentials(self.data, self.config)
            self.assertNotIn("rotated-secret", str(error.exception))

    def test_shared_database_change_requires_both(self):
        changed = self.config | {"memory-benchmarks:databaseName": "another_database"}
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaisesRegex(ValueError, "deployment_target=both"):
                dt.verify_credentials(self.data, changed)

    def test_chart_change_to_unselected_pod_is_rejected(self):
        self.manifest = self.manifest.replace(
            "terminationGracePeriodSeconds: 120", "terminationGracePeriodSeconds: 121"
        )
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch.object(Path, "read_text", return_value=yaml.safe_dump({"config": self.config})),
            patch.object(dt, "command", side_effect=self.cluster_command),
        ):
            with self.assertRaisesRegex(ValueError, "Chart changes"):
                dt.capture()

    def test_cluster_access_failure_is_safe_and_actionable(self):
        with patch.object(
            subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 1, stderr="synthetic-private-config"),
        ):
            with self.assertRaisesRegex(ValueError, "first deploy both") as error:
                dt.command("helm", "get", "values")
            self.assertNotIn("synthetic-private-config", str(error.exception))

    def test_deployment_changed_during_build_is_rejected(self):
        before = {"deployment_target": "memoria", "values": self.values}
        after = copy.deepcopy(before)
        after["values"]["workers"] = 3
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps(before))
            with (
                patch.dict(os.environ, self.env | {"AML_DEPLOYMENT_TARGET": "memoria"}, clear=True),
                patch.object(dt, "capture", return_value=after),
            ):
                with self.assertRaisesRegex(ValueError, "changed during the build"):
                    dt.recheck(path)

    def test_adapter_only_does_not_need_memoria_build_metadata_and_records_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.env | {
                "GITHUB_OUTPUT": str(Path(directory) / "output"),
                "AML_MEMORIA_BUILD_MODE": "reuse",
                "AML_MEMORIA_IMAGE": dt.image(self.values, "memoria"),
                "AML_MEMORIA_SOURCE_REPOSITORY": "matrixorigin/memoria",
                "AML_MEMORIA_SOURCE_REF": "release",
                "AML_MEMORIA_SOURCE_COMMIT": "a" * 40,
            }
            with patch.dict(os.environ, env, clear=True):
                mi.prepare_tags(argparse.Namespace())
                metadata = Path(directory) / "metadata.json"
                mi.pin(argparse.Namespace(metadata_file=metadata))
            output = (Path(directory) / "output").read_text()
            self.assertIn("memoria_tag=\n", output)
            record = json.loads(metadata.read_text())
            self.assertEqual(record["mode"], "reuse")
            self.assertEqual(record["source_commit"], "a" * 40)

    def test_plan_runs_before_deployment_dependencies_are_installed(self):
        for selected, adapter, memoria in [
            ("both", "true", "true"),
            ("memoria", "false", "true"),
            ("memory_benchmarks", "true", "false"),
        ]:
            with self.subTest(target=selected), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "output"
                subprocess.check_output(
                    [
                        sys.executable,
                        "-S",
                        str(ROOT / "deploy/scripts/deployment_target.py"),
                        "plan",
                    ],
                    env={
                        "PATH": os.environ["PATH"],
                        "AML_DEPLOYMENT_TARGET": selected,
                        "GITHUB_OUTPUT": str(output),
                    },
                    text=True,
                )
                self.assertEqual(
                    output.read_text(), f"update_adapter={adapter}\nupdate_memoria={memoria}\n"
                )

    def test_unknown_target_fails_closed(self):
        with patch.dict(os.environ, {"AML_DEPLOYMENT_TARGET": "invalid"}, clear=True):
            with self.assertRaises(ValueError):
                dt.target()


if __name__ == "__main__":
    unittest.main()
