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
    connect_timeout_seconds: float = field(default=30, kw_only=True)
    request_timeout_seconds: float = field(default=1700, kw_only=True)
    visibility_timeout_seconds: float = field(default=60, kw_only=True)
    catalog_retry_timeout_seconds: float = field(default=5, kw_only=True)
    backend_max_concurrency: int = 0
    backend_requests_per_second: float = 0
    backend_queue_timeout_seconds: float = 120
    max_active_requests: int = 40
    journal_log_all_operations: bool = False
    journal_slow_ms: float = 100
    journal_queue_timeout_seconds: float = 30
    time_normalization_enabled: bool = False

    def __post_init__(self) -> None:
        if type(self.time_normalization_enabled) is not bool:
            raise ValueError("MEMORY_TIME_NORMALIZATION_ENABLED must be a boolean")
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
        for name, value, maximum in [
            ("MEMORIA_CONNECT_TIMEOUT_SECONDS", self.connect_timeout_seconds, 120),
            ("ADAPTER_REQUEST_TIMEOUT_SECONDS", self.request_timeout_seconds, 1740),
        ]:
            if not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError(f"{name} must be in (0, {maximum}]")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("MEMORIA_TIMEOUT_SECONDS must be positive and finite")
        if (
            not math.isfinite(self.journal_queue_timeout_seconds)
            or not 0 < self.journal_queue_timeout_seconds <= 120
        ):
            raise ValueError("ADAPTER_JOURNAL_QUEUE_TIMEOUT_SECONDS must be in (0, 120]")
        if self.backend_max_concurrency < 0:
            raise ValueError("MEMORIA_MAX_CONCURRENCY must be nonnegative")
        if self.max_active_requests <= 0:
            raise ValueError("ADAPTER_MAX_ACTIVE_REQUESTS must be positive")
        if type(self.journal_log_all_operations) is not bool:
            raise ValueError("ADAPTER_JOURNAL_LOG_ALL_OPERATIONS must be a boolean")
        if not math.isfinite(self.journal_slow_ms) or self.journal_slow_ms < 0:
            raise ValueError("ADAPTER_JOURNAL_SLOW_MS must be nonnegative and finite")
        if (
            not math.isfinite(self.catalog_retry_timeout_seconds)
            or not 0 <= self.catalog_retry_timeout_seconds <= 120
        ):
            raise ValueError("MEMORIA_CATALOG_RETRY_TIMEOUT_SECONDS must be in [0, 120]")
        if (
            not math.isfinite(self.visibility_timeout_seconds)
            or not 0 < self.visibility_timeout_seconds <= 120
        ):
            raise ValueError("MEMORIA_VISIBILITY_TIMEOUT_SECONDS must be in (0, 120]")
        if (
            not math.isfinite(self.backend_requests_per_second)
            or self.backend_requests_per_second < 0
        ):
            raise ValueError("MEMORIA_REQUESTS_PER_SECOND must be nonnegative and finite")
        if (
            not math.isfinite(self.backend_queue_timeout_seconds)
            or self.backend_queue_timeout_seconds <= 0
        ):
            raise ValueError("MEMORIA_QUEUE_TIMEOUT_SECONDS must be positive and finite")

    @classmethod
    def from_env(cls) -> "Settings":
        time_normalization = os.getenv("MEMORY_TIME_NORMALIZATION_ENABLED", "false").lower()
        if time_normalization not in {"true", "false", "1", "0"}:
            raise ValueError("MEMORY_TIME_NORMALIZATION_ENABLED must be true/false or 1/0")
        journal_logging = os.getenv("ADAPTER_JOURNAL_LOG_ALL_OPERATIONS", "false").lower()
        if journal_logging not in {"true", "false", "1", "0"}:
            raise ValueError("ADAPTER_JOURNAL_LOG_ALL_OPERATIONS must be true/false or 1/0")
        return cls(
            time_normalization_enabled=time_normalization in {"true", "1"},
            memoria_url=os.environ["MEMORIA_URL"].rstrip("/"),
            memoria_master_key=os.environ["MEMORIA_MASTER_KEY"],
            api_key=os.getenv("ADAPTER_API_KEY") or None,
            state_dir=Path(os.getenv("ADAPTER_STATE_DIR", "var")),
            namespace=os.getenv("MEMORIA_NAMESPACE", "agent-memory-leaderboard"),
            timeout_seconds=float(os.getenv("MEMORIA_TIMEOUT_SECONDS", "120")),
            connect_timeout_seconds=float(os.getenv("MEMORIA_CONNECT_TIMEOUT_SECONDS", "30")),
            request_timeout_seconds=float(os.getenv("ADAPTER_REQUEST_TIMEOUT_SECONDS", "1700")),
            visibility_timeout_seconds=float(os.getenv("MEMORIA_VISIBILITY_TIMEOUT_SECONDS", "60")),
            catalog_retry_timeout_seconds=float(
                os.getenv("MEMORIA_CATALOG_RETRY_TIMEOUT_SECONDS", "5")
            ),
            backend_max_concurrency=int(os.getenv("MEMORIA_MAX_CONCURRENCY", "0")),
            backend_requests_per_second=float(os.getenv("MEMORIA_REQUESTS_PER_SECOND", "0")),
            backend_queue_timeout_seconds=float(os.getenv("MEMORIA_QUEUE_TIMEOUT_SECONDS", "120")),
            max_active_requests=int(os.getenv("ADAPTER_MAX_ACTIVE_REQUESTS", "40")),
            journal_log_all_operations=journal_logging in {"true", "1"},
            journal_slow_ms=float(os.getenv("ADAPTER_JOURNAL_SLOW_MS", "100")),
            journal_queue_timeout_seconds=float(
                os.getenv("ADAPTER_JOURNAL_QUEUE_TIMEOUT_SECONDS", "30")
            ),
        )
