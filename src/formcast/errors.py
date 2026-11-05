"""Exception types for formcast."""


class FormcastError(Exception):
    """Base class for all formcast errors."""


class OpError(FormcastError):
    """An operation (edit op) was rejected or failed.

    Attributes:
        op: the operation dict that caused the error.
        reason: human-readable explanation.
    """

    def __init__(self, op: dict, reason: str):
        super().__init__(op, reason)  # both in args, so the error pickles (e.g. across processes)
        self.op = op
        self.reason = reason

    def __str__(self) -> str:
        return self.reason


class LLMRequestError(FormcastError):
    """A model call failed: the endpoint refused it, or it could not be reached in the call's time.

    Attributes:
        status: the HTTP status of the last answer, or None when none came (a network error or the time ran out).
        too_large: the endpoint said the request is too large for the model (HTTP 413, or a 400 about the context
            length), so sending it again unchanged cannot work.
    """

    def __init__(self, message: str, *, status: int | None = None, too_large: bool = False):
        super().__init__(message)
        self.status = status
        self.too_large = too_large

    def __reduce__(self):  # keep the attributes across processes, as OpError does
        return (_llm_request_error, (str(self), self.status, self.too_large))


def _llm_request_error(message: str, status: int | None, too_large: bool) -> LLMRequestError:
    return LLMRequestError(message, status=status, too_large=too_large)


class SessionCancelled(FormcastError):
    """Raised from an agent session's ``on_step`` callback to stop the session cleanly."""
