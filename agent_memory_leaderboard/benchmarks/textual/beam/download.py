"""Download pinned public JSON only (never unpickle upstream files)."""

import concurrent.futures
import hashlib
import json
import subprocess
from pathlib import Path

REVISION = "b2da22eac88bb0874c64665f13457eb99835774a"
ROOT = Path(__file__).parent / "data"


def fetch(path):
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_suffix(".download")
        subprocess.run(
            [
                "curl",
                "-fLsS",
                "--retry",
                "3",
                "--max-time",
                "600",
                f"https://raw.githubusercontent.com/mohammadtavakoli78/BEAM/{REVISION}/{path}",
                "-o",
                str(temporary),
            ],
            check=True,
        )
        temporary.replace(target)
    return target


def main():
    samples = [
        (size, i)
        for size, count in [("100K", 20), ("500K", 35), ("1M", 35), ("10M", 10)]
        for i in range(1, count + 1)
    ]
    paths = [
        f"chats/{size}/{i}/{suffix}"
        for size, i in samples
        for suffix in ["chat.json", "probing_questions/probing_questions.json"]
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for index, path in enumerate(pool.map(fetch, paths), 1):
            print(f"{index}/{len(paths)} {path}", flush=True)
    # Stream the combined JSON to avoid retaining 800 MB of decoded chats at once.
    output = ROOT / "beam.json"
    temporary = output.with_suffix(".tmp")
    with temporary.open("w") as stream:
        stream.write("[")
        for index, (size, i) in enumerate(samples):
            if index:
                stream.write(",")
            chat = json.loads((ROOT / f"chats/{size}/{i}/chat.json").read_text())
            questions = json.loads(
                (ROOT / f"chats/{size}/{i}/probing_questions/probing_questions.json").read_text()
            )
            json.dump(
                {"sample_id": f"{size}-{i}", "chat": chat, "probing_questions": questions}, stream
            )
        stream.write("]\n")
    temporary.replace(output)
    manifest = {
        "revision": REVISION,
        "histories": len(samples),
        "variants": {"100K": 20, "500K": 35, "1M": 35, "10M": 10},
        "files": {
            path: hashlib.file_digest((ROOT / path).open("rb"), "sha256").hexdigest()
            for path in paths
        },
    }
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {output}", flush=True)


if __name__ == "__main__":
    main()
