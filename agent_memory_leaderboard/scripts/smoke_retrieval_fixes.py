"""Live regression: preserve long-query tail recall, scope and credential blocking."""

import json
import uuid

import httpx

from src.benchmark import save
from src.config import Settings
from src.service import digest
from src.smoke import call_adapter


def main():
    from pathlib import Path

    settings = Settings.from_env()
    run_id = "retrieval-fix:" + uuid.uuid4().hex
    users = [run_id + ":alice", run_id + ":bob"]
    headers = {"Authorization": "Bearer " + settings.api_key}
    with httpx.Client(timeout=1800, trust_env=False) as http:

        def call(route, body):
            return call_adapter(http, "http://127.0.0.1:8000", headers, route, body)

        def backend(method, path, user):
            response = http.request(
                method,
                settings.memoria_url + path,
                headers={
                    "Authorization": "Memoria-Owner " + settings.memoria_master_key,
                    "X-User-Id": digest([settings.namespace, user]),
                },
            )
            response.raise_for_status()
            return response

        try:
            for index, user in enumerate(users):
                call(
                    "/add",
                    dict(
                        user_id=user,
                        request_id=user,
                        session_id="test",
                        messages=[
                            {
                                "role": "user",
                                "content": f"ZirconBeacon belongs to owner {index}. ________",
                            }
                        ],
                    ),
                )
            words = " ".join(f"unmatchedword{i}" for i in range(3000))
            query = " ".join([words] * 4) + " _________ ZirconBeacon"
            result = call("/search", dict(user_id=users[0], query=query, top_k=1))
            assert len(result["data"]) == 1
            assert "owner 0" in result["data"][0]["content"]
            assert "owner 1" not in result["data"][0]["content"]
            # A synthetic test credential must still be rejected, including when
            # wrapped in a function call; no real credential is used here.
            for index, content in enumerate(
                ("password=regression-test-only", "password=str('regression-test-only')")
            ):
                rejected = http.post(
                    "http://127.0.0.1:8000/add",
                    headers=headers,
                    json={
                        "user_id": users[0],
                        "request_id": users[0] + f":rejected:{index}",
                        "session_id": "test",
                        "messages": [{"role": "user", "content": content}],
                    },
                )
                assert rejected.status_code == 403
            report = {
                "status": "passed",
                "query_bytes": len(query.encode()),
                "distinct_query_terms": 3001,
                "tail_term_retrieved": True,
                "user_isolation": True,
                "top_k": 1,
                "credential_literals_blocked": True,
            }
            save(Path("state/retrieval-fix-smoke.json"), report)
            print(json.dumps(report, indent=2))
        finally:
            for user in users:
                items = backend("GET", "/v1/memories?limit=100", user).json()["items"]
                for item in items:
                    backend("DELETE", "/v1/memories/" + item["memory_id"], user)


if __name__ == "__main__":
    main()
