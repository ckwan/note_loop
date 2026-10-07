class NotFoundError(Exception):
    """The requested record does not exist."""


class ConflictError(Exception):
    """The action is not allowed in the record's current state."""


class LLMOutputError(Exception):
    """The LLM failed or returned output that did not match the schema."""


class RateLimitedError(Exception):
    """The caller made too many requests in the current window."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Too many requests. Try again in {retry_after} seconds.")
        self.retry_after = retry_after
