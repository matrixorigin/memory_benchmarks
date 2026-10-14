"""Live adapter -> Memoria -> MatrixOne integration check using disposable users."""

import argparse
import json
import os
import random
import time
import uuid
from urllib.parse import quote

import httpx

from .config import Settings
from .diagnostics import adapter_reason_code
from .service import digest


def call_adapter(
    http: httpx.Client,
    adapter_url: str,
    headers: dict,
    path: str,
    body: dict,
    *,
    observe=None,
):
    """Published retry sets; an optional observer receives only fixed diagnostics."""
    retryable = {408, 425, 429, 500, 502, 503, 504}
    if path == "/add":
        retryable |= {409, 524}
    for attempt in range(6):
        delay = min(2**attempt / 4, 30)
        start = time.monotonic()
        try:
            response = http.post(adapter_url.rstrip("/") + path, headers=headers, json=body)
        except httpx.RequestError:
            if observe:
                observe(
                    {
                        "route": path,
                        "attempt": attempt + 1,
                        "status": "transport",
                        "reason": "transport_error",
                        "will_retry": attempt < 5,
                        "elapsed_seconds": time.monotonic() - start,
                    }
                )
            if attempt == 5:
                raise
        else:
            will_retry = response.status_code in retryable and attempt < 5
            if observe:
                observe(
                    {
                        "route": path,
                        "attempt": attempt + 1,
                        "status": response.status_code,
                        "reason": adapter_reason_code(response) or "unclassified",
                        "will_retry": will_retry,
                        "elapsed_seconds": time.monotonic() - start,
                    }
                )
            if not will_retry:
                response.raise_for_status()
                assert response.status_code == 200, f"{path} must return 200"
                return response.json()
            if response.status_code in {429, 503}:
                value = response.headers.get("Retry-After", "")
                if value.isascii() and value.isdigit() and len(value) <= 10:
                    delay = max(delay, min(int(value), 60))
                elif response.status_code == 429:
                    delay = 60
        # Never retry earlier than the server's hint; jitter spreads simultaneous callers.
        time.sleep(delay + random.uniform(0, min(1, delay / 4)))


def run(adapter_url: str) -> dict:
    settings = Settings.from_env()
    run_id = uuid.uuid4().hex
    users = [f"aml-smoke:{run_id}:{suffix}" for suffix in ("alice", "bob", "empty")]
    headers = {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
    with httpx.Client(timeout=settings.timeout_seconds, trust_env=False) as http:

        def backend(method, path, user, **kwargs):
            response = http.request(
                method,
                settings.memoria_url.rstrip("/") + path,
                headers={
                    "Authorization": f"Memoria-Owner {settings.memoria_master_key}",
                    "X-User-Id": digest([settings.namespace, user]),
                },
                **kwargs,
            )
            response.raise_for_status()
            return response

        def records(user):
            return backend("GET", "/v1/memories?limit=100", user).json()["items"]

        def call(path, body):
            return call_adapter(http, adapter_url, headers, path, body)

        def search(user, query="Orion", top_k=100):
            result = call("/search", {"user_id": user, "query": query, "top_k": top_k})
            assert isinstance(result.get("data"), list), "Missing data array"
            assert len(result["data"]) <= top_k, "Exceeded top_k"
            assert all(item.get("id") and item.get("content") for item in result["data"])
            return result["data"]

        try:
            http.get(adapter_url.rstrip("/") + "/health").raise_for_status()
            # This request also fails closed on servers lacking Memoria-Owner auth.
            for user in users:
                identity = backend("GET", "/auth/whoami", user).json()
                expected = digest([settings.namespace, user])
                assert identity["user_id"] == expected, "Memoria owner authentication mismatch"
                assert identity["scope"] == {"type": "personal", "id": expected}

            writes = [
                (users[0], "session-1", "Orion project uses Rust for the server."),
                (users[0], "session-2", "Orion project releases every Tuesday."),
                (users[1], "session-1", "Orion project uses Python and releases on Friday."),
            ]
            payloads = []
            for index, (user, session, content) in enumerate(writes):
                payload = {
                    "request_id": f"{run_id}:{index}",
                    "user_id": user,
                    "session_id": session,
                    "messages": [{"role": "user", "content": content, "timestamp": 1704067200000}],
                }
                payloads.append(payload)
                result = call("/add", payload)
                assert result == {
                    "success": True,
                    "request_id": payload["request_id"],
                    "user_id": user,
                    "session_id": session,
                }, "Add response does not match the contract"
                immediate = search(user)
                if not any(content in item["content"] for item in immediate):
                    counts = {}
                    for query in ("Orion", content, "Orion Rust Tuesday Python Friday"):
                        counts[query] = len(search(user, query=query))
                    raise AssertionError(
                        f"Add #{index} missing from query; scoped query counts: {counts}"
                    )

            before = {item["memory_id"] for item in records(users[0])}
            call("/add", payloads[0])
            assert {item["memory_id"] for item in records(users[0])} == before, (
                "Retry duplicated data"
            )
            alice = search(users[0])
            bob = search(users[1])
            assert len(alice) == 2, "Expected evidence from both source sessions"
            assert all("Python" not in item["content"] for item in alice), "Cross-user evidence"
            assert len(bob) == 1 and "Python" in bob[0]["content"], "Incorrect Bob scope"
            assert not search(users[2]), "Empty user returned evidence"
            limited = search(users[0], top_k=1)
            if not limited:
                counts = {}
                for limit in (1, 2, 10, 100):
                    direct = backend(
                        "POST",
                        "/v1/memories/search",
                        users[0],
                        json={
                            "query": "Orion",
                            "top_k": limit,
                        },
                    ).json()
                    counts[limit] = len(direct)
                raise AssertionError(
                    f"Small-top_k retrieval failed; direct Memoria counts: {counts}"
                )
            return {
                "status": "passed",
                "checks": [
                    "owner-scoped authentication",
                    "synchronous Add and immediate Search",
                    "cross-session retrieval",
                    "user isolation",
                    "idempotent retry",
                    "top_k",
                    "empty data",
                ],
            }
        finally:
            # These UUID-scoped users belong solely to this run. Never delete by topic
            # or touch another user's records. DELETE deactivates records; it does
            # not physically erase storage/snapshots for retention compliance.
            for user in users:
                for memory in records(user):
                    backend("DELETE", "/v1/memories/" + quote(memory["memory_id"], safe=""), user)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter-url", default=os.getenv("ADAPTER_URL", "http://127.0.0.1:8000"))
    args = parser.parse_args()
    print(json.dumps(run(args.adapter_url), indent=2))


if __name__ == "__main__":
    main()
