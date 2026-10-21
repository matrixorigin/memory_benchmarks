import pytest


@pytest.fixture
def fake_encoding(monkeypatch):
    """Deterministic local tokenizer; tests never download a vocabulary."""
    import tiktoken

    class Encoder:
        calls = 0

        def encode(self, text, **kwargs):
            self.calls += 1
            return text.encode("utf-8")

        def decode(self, tokens):
            return bytes(tokens).decode("utf-8", errors="replace")

    encoder = Encoder()
    monkeypatch.setattr(tiktoken, "get_encoding", lambda name: encoder)
    return encoder


@pytest.fixture
def model_env(monkeypatch):
    for role in ("ANSWER", "JUDGE"):
        monkeypatch.setenv(f"BENCHMARK_{role}_MODEL", "synthetic-model")
        monkeypatch.setenv(f"BENCHMARK_{role}_BASE_URL", f"http://{role.lower()}.test/v1")
        monkeypatch.setenv(f"BENCHMARK_{role}_API_KEY", f"synthetic-{role.lower()}-key")
    monkeypatch.setenv("ADAPTER_API_KEY", "synthetic-adapter-key")


@pytest.fixture
def mock_http(monkeypatch):
    import httpx

    client = httpx.Client

    def install(handler):
        monkeypatch.setattr(
            httpx,
            "Client",
            lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
        )

    return install
