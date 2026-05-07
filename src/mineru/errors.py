from __future__ import annotations


class MinerUError(Exception):
    pass


class MinerUConfigError(MinerUError):
    pass


class MinerUApiError(MinerUError):
    code: int | str
    message: str
    trace_id: str | None

    def __init__(
        self, code: int | str, message: str, trace_id: str | None = None
    ) -> None:
        self.code = code
        self.message = message
        self.trace_id = trace_id
        suffix = f" (trace_id={trace_id})" if trace_id else ""
        super().__init__(f"MinerU API error {code}: {message}{suffix}")


class MinerUTaskFailedError(MinerUError):
    task_id: str | None
    batch_id: str | None
    message: str

    def __init__(
        self, message: str, *, task_id: str | None = None, batch_id: str | None = None
    ) -> None:
        self.task_id = task_id
        self.batch_id = batch_id
        self.message = message
        super().__init__(message)


class MinerUResultError(MinerUError):
    pass
