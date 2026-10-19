"""The benchmark must fail closed when its commit instrumentation is bypassed."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("implicit_commit", [False, True])
def test_benchmark_rejects_unmeasured_context_manager_commits(tmp_path, implicit_commit):
    shutil.copytree(ROOT / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__"))
    if implicit_commit:
        journal = tmp_path / "src/journal.py"
        lines = journal.read_text().splitlines(keepends=True)
        replaced = False
        for i, line in enumerate(lines):
            if 'timing.run("commit", db.commit)' in line:
                indent = line[: len(line) - len(line.lstrip())]
                lines[i] = indent + "with db:\n" + indent + "    pass\n"
                replaced = True
        assert replaced
        journal.write_text("".join(lines))
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/benchmark_journal.py"),
            "--source-root",
            str(tmp_path),
            "--requests",
            "2",
            "--concurrency",
            "1",
            "--commit-delay-ms",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if implicit_commit:
        assert result.returncode != 0
        assert "implicit context-manager commits are not measured" in result.stderr
        assert not result.stdout.strip()  # No misleading result for the rejected case.
    else:
        assert result.returncode == 0, result.stderr
        summary = json.loads(result.stdout)
        assert summary["statuses"] == {"200": 2}
        assert summary["commits"] == 6
        assert summary["unique_backend_records"] == 2
