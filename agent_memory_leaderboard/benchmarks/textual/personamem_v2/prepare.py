"""Download the official text test split's histories and prepare JSON records."""

import argparse
import csv
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

BASE = "https://huggingface.co/datasets/bowen-upenn/PersonaMem-v2/resolve/main/"
ROOT = Path(__file__).parent / "data"


def prepare(size="32k"):
    rows = list(csv.DictReader((ROOT / "benchmark.csv").open()))
    groups = {}
    for row in rows:
        groups.setdefault(row[f"chat_history_{size}_link"], []).append(row)

    def fetch(entry):
        link, questions = entry
        path = ROOT / link
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with urlopen(BASE + link, timeout=120) as response:
                payload = response.read()
            json.loads(payload)
            path.write_bytes(payload)
        return {
            "persona_id": questions[0]["persona_id"],
            "history": json.loads(path.read_text())["chat_history"],
            "questions": questions,
            "history_source": link,
        }

    with ThreadPoolExecutor(max_workers=6) as pool:
        records = list(pool.map(fetch, groups.items()))
    output = ROOT / f"personamem_v2_{size}.json"
    output.write_text(json.dumps(records))
    print(
        json.dumps(
            {
                "output": str(output),
                "personas": len(records),
                "questions": len(rows),
                "context_size": size,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", choices=["32k", "128k"], default="32k")
    prepare(parser.parse_args().size)
