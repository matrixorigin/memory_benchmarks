"""Convert public parquet to an issue-only endpoint diagnostic, excluding solution fields."""

import hashlib
import json
from collections import defaultdict
from pathlib import Path


def build(experiences, related, relationships):
    links = defaultdict(set)
    for row in relationships:
        links[row["related_instance_id"]].add(row["experience_instance_id"])
    target_ids = {row["instance_id"] for row in related}
    experience_by_id = {row["instance_id"]: row for row in experiences}
    result = []
    for repo in sorted({row["repo"] for row in related}):
        # Some upstream links cross repository names; include linked repositories.
        pool_repos = {repo}
        for task in related:
            if task["repo"] == repo:
                for expected in links[task["instance_id"]]:
                    pool_repos.add(experience_by_id[expected]["repo"])
        history = [row for row in experiences
                   if row["repo"] in pool_repos and row["instance_id"] not in target_ids]
        ids = {row["instance_id"] for row in history}
        questions = []
        for row in related:
            if row["repo"] != repo:
                continue
            if row["instance_id"] in ids:
                raise ValueError("Target task overlaps experience pool")
            expected = links[row["instance_id"]]
            if not expected or not expected <= ids:
                raise ValueError("Missing linked experience for " + row["instance_id"])
            questions.append({
                "id": row["instance_id"], "question": row["problem_statement"],
                "category": "historical_issue_retrieval", "evidence": sorted(expected),
            })
        if not questions:
            continue
        conversation = {}
        for index, row in enumerate(history, 1):
            session = f"session_{index}"
            conversation[session + "_date_time"] = row["created_at"]
            conversation[session] = [{
                "dia_id": row["instance_id"], "speaker": "historical issue",
                "text": row["problem_statement"],
            }]
        result.append({"sample_id": repo.replace("/", "__"),
                       "conversation": conversation, "qa": questions})
    return result


def main():
    import pyarrow.parquet as pq

    directory = Path(__file__).parent / "data"
    manifest = json.loads((directory / "source_manifest.json").read_text())
    rows = {}
    checksums = {}
    for name in ("Experience", "Related", "Relationship"):
        path = directory / f"SWEContextBench_{name}.parquet"
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        entry = next(x for x in manifest if x["path"] == "data/" + path.name)
        if checksum != entry["lfs"]["oid"]:
            raise ValueError("Source hash mismatch: " + path.name)
        checksums[path.name] = checksum
        rows[name] = pq.read_table(path).to_pylist()
    result = build(rows["Experience"], rows["Related"], rows["Relationship"])
    (directory / "retrieval.json").write_text(json.dumps(result) + "\n")
    audit = {"source_sha256": checksums, "source_rows": {k: len(v) for k, v in rows.items()},
             "repositories": len(result), "questions": sum(len(x["qa"]) for x in result),
             "ingested_fields": ["instance_id", "problem_statement", "created_at"],
             "excluded_fields": ["patch", "test_patch", "FAIL_TO_PASS",
                                 "PASS_TO_PASS", "hints_text"],
             "official_aml_score": False, "native_coding_score": None}
    (directory / "preparation.json").write_text(json.dumps(audit, indent=2) + "\n")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
