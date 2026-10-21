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
