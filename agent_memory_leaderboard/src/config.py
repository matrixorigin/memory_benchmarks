import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    memoria_url: str
    memoria_master_key: str = field(repr=False)
    api_key: str | None = field(default=None, repr=False)
    state_dir: Path = Path("var")
    namespace: str = "agent-memory-leaderboard"
    timeout_seconds: float = 120

    def __post_init__(self) -> None:
        url = urlsplit(self.memoria_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError("MEMORIA_URL must be an HTTP(S) base URL without credentials")
        if not self.memoria_master_key or not self.namespace.strip():
            raise ValueError("MEMORIA_MASTER_KEY and MEMORIA_NAMESPACE must be nonempty")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("MEMORIA_TIMEOUT_SECONDS must be positive and finite")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            memoria_url=os.environ["MEMORIA_URL"].rstrip("/"),
            memoria_master_key=os.environ["MEMORIA_MASTER_KEY"],
            api_key=os.getenv("ADAPTER_API_KEY") or None,
            state_dir=Path(os.getenv("ADAPTER_STATE_DIR", "var")),
            namespace=os.getenv("MEMORIA_NAMESPACE", "agent-memory-leaderboard"),
            timeout_seconds=float(os.getenv("MEMORIA_TIMEOUT_SECONDS", "120")),
        )
