"""Render both chart variants using synthetic values, without cluster access."""

import subprocess
from pathlib import Path


def main() -> None:
    chart = Path(__file__).resolve().parents[1] / "chart"
    common = [
        "--set-string", "image.repository=registry.example.invalid/adapter",
        "--set-string", "image.digest=sha256:" + "0" * 64,
        "--set-string", "memoria.image.repository=registry.example.invalid/memoria",
        "--set-string", "memoria.image.digest=sha256:" + "1" * 64,
        "--set-string", "memoria.embeddingBaseURL=https://embedding.example.invalid/v1",
    ]
    for public, workers in [(False, 1), (True, 2)]:
        values = common + [
            "--set", f"ingress.enabled={str(public).lower()}",
            "--set", f"workers={workers}",
        ]
        if public:
            values += ["--set-string", "ingress.host=aml.example.invalid"]
        subprocess.run(["helm", "lint", "--strict", str(chart), *values], check=True)
        subprocess.run(
            ["helm", "template", "aml", str(chart), "--namespace", "aml-memory-benchmarks",
             "--kube-version", "1.28.9", *values],
            check=True, stdout=subprocess.DEVNULL,
        )
        print(f"Chart rendered: ingress={public}, workers={workers}")


if __name__ == "__main__":
    main()
