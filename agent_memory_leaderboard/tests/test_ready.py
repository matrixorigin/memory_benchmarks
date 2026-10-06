import httpx
import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        memoria_url="http://memoria.test",
        memoria_master_key="upstream-secret",
        api_key="client-secret",
        state_dir=tmp_path,
    )


def test_ready_healthy_is_unauthenticated_and_does_not_write(settings):
    calls = []

    def healthy(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url == "http://memoria.test/health"
        assert request.extensions["timeout"]["read"] == 3
        return httpx.Response(200, json={"status": "ok"})

    with httpx.Client(transport=httpx.MockTransport(healthy)) as http:
        with TestClient(create_app(settings, client=http)) as client:
            response = client.get("/ready")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
            assert len(calls) == 1


@pytest.mark.parametrize("failure", [503, 302, "connection", "timeout"])
def test_ready_upstream_unavailable(settings, failure):
    def unavailable(request):
        if failure == "connection":
            raise httpx.ConnectError("upstream-secret", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("upstream-secret", request=request)
        return httpx.Response(failure, text="upstream-secret")

    with httpx.Client(transport=httpx.MockTransport(unavailable)) as http:
        with TestClient(create_app(settings, client=http)) as client:
            response = client.get("/ready")
            assert response.status_code == 503
            assert response.json() == {"status": "not_ready"}
            assert "upstream-secret" not in response.text
            assert client.get("/health").status_code == 200


def test_ready_journal_unavailable_does_not_call_upstream(settings):
    def unexpected(request):
        raise AssertionError("Upstream must not be called when journal is unavailable")

    with httpx.Client(transport=httpx.MockTransport(unexpected)) as http:
        with TestClient(create_app(settings, client=http)) as client:
            journal = settings.state_dir / "requests.sqlite3"
            backup = settings.state_dir / "saved.sqlite3"
            journal.rename(backup)
            journal.mkdir()  # sqlite3 cannot open a directory as its database.
            try:
                response = client.get("/ready")
                assert response.status_code == 503
                assert response.json() == {"status": "not_ready"}
                assert client.get("/health").status_code == 200
            finally:
                journal.rmdir()
                backup.rename(journal)
