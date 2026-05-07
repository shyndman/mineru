from __future__ import annotations

import asyncio
import time
from collections.abc import Generator
from pathlib import Path
from typing import Protocol

from .errors import MinerUResultError, MinerUTaskFailedError
from .models import BatchExtractResult, ExtractTask, ExtractionSource, ExtractionStatus
from .results import MinerUParsedResult


class MinerUClientProtocol(Protocol):
    def get_extract_task(self, task_id: str) -> ExtractTask: ...
    def get_batch_extract_result(self, batch_id: str) -> BatchExtractResult: ...
    def download_result(self, full_zip_url: str, *, output_dir: Path | None = None) -> MinerUParsedResult: ...


class ExtractionJob:
    _client: MinerUClientProtocol
    _task_id: str | None
    _batch_id: str | None
    _poll_interval_seconds: float
    source: ExtractionSource
    last_status: ExtractionStatus

    def __init__(
        self,
        client: MinerUClientProtocol,
        *,
        source: ExtractionSource,
        status: ExtractionStatus,
        task_id: str | None = None,
        batch_id: str | None = None,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        self._client = client
        self._task_id = task_id
        self._batch_id = batch_id
        self._poll_interval_seconds = poll_interval_seconds
        self.source = source
        self.last_status = status

    @classmethod
    def from_task(
        cls,
        client: MinerUClientProtocol,
        task_id: str,
        *,
        source: ExtractionSource,
        status: ExtractionStatus,
        poll_interval_seconds: float,
    ) -> ExtractionJob:
        return cls(
            client,
            source=source,
            status=status,
            task_id=task_id,
            poll_interval_seconds=poll_interval_seconds,
        )

    @classmethod
    def from_batch(
        cls,
        client: MinerUClientProtocol,
        batch_id: str,
        *,
        source: ExtractionSource,
        status: ExtractionStatus,
        poll_interval_seconds: float,
    ) -> ExtractionJob:
        return cls(
            client,
            source=source,
            status=status,
            batch_id=batch_id,
            poll_interval_seconds=poll_interval_seconds,
        )

    def __call__(self) -> ExtractionStatus:
        return self.refresh()

    def __await__(self) -> Generator[object, None, MinerUParsedResult]:
        return self._await_result().__await__()

    async def _await_result(self) -> MinerUParsedResult:
        return await asyncio.to_thread(self.wait)

    def status(self) -> ExtractionStatus:
        return self.refresh()

    def refresh(self) -> ExtractionStatus:
        if self._task_id is not None:
            self.last_status = ExtractionStatus.from_task(self._client.get_extract_task(self._task_id))
            return self.last_status
        if self._batch_id is None:
            raise MinerUResultError("Extraction job has neither task_id nor batch_id")
        result = self._client.get_batch_extract_result(self._batch_id)
        if len(result.results) != 1:
            raise MinerUResultError(f"Expected one extraction result, got {len(result.results)}")
        self.last_status = ExtractionStatus.from_batch_task(result.batch_id, result.results[0])
        return self.last_status

    def wait(self, *, output_dir: Path | None = None) -> MinerUParsedResult:
        while True:
            status = self.status()
            if status.state == "done":
                if status.full_zip_url is None:
                    raise MinerUResultError("Extraction completed without full_zip_url")
                return self._client.download_result(status.full_zip_url, output_dir=output_dir)
            if status.state == "failed":
                raise MinerUTaskFailedError(
                    status.err_msg or "MinerU extraction failed",
                    task_id=status.task_id,
                    batch_id=status.batch_id,
                )
            time.sleep(self._poll_interval_seconds)
