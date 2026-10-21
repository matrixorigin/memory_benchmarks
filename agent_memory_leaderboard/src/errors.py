class AdapterError(Exception):
    def __init__(
        self,
        status: int,
        reason: str,
        *,
        write_not_sent: bool = False,
        retry_after: str | None = None,
    ):
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.retry_after = retry_after
        # Only true when it is certain that a failed POST cannot have committed.
        self.write_not_sent = write_not_sent


class VisibilityPending(AdapterError):
    """A valid empty read/retrieval result; other 503s must not enter this retry loop."""


class CatalogVisibilityPending(AdapterError):
    """Recognized upstream missing-table error; a write may already have committed."""
