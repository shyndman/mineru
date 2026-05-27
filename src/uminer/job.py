from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlparse
from uuid import UUID

import httpx

from .errors import MinerUResultError, MinerUTaskFailedError
from .models import BatchExtractResult, ExtractionSource, ExtractionStatus, ExtractTask
from .results import MinerUParsedResult, default_local_output_dir

type StatusCallback = Callable[[ExtractionStatus], None]
type DownloadStartCallback = Callable[[], None]
type BatchStatusCallback = Callable[[tuple[ExtractionStatus, ...]], None]
type BatchDownloadStartCallback = Callable[[int, ExtractionStatus], None]
type BatchItemResultCallback = Callable[[int, ExtractionBatchItemResult], None]

BATCH_RESULT_RETRY_COUNT = 6
BATCH_RESULT_RETRY_DELAY_SECONDS = 10.0


class MinerUClientProtocol(Protocol):
    def get_extract_task(self, task_id: str | UUID) -> ExtractTask: ...
    def get_batch_extract_result(self, batch_id: str) -> BatchExtractResult: ...
    def download_result(
        self,
        full_zip_url: str,
        *,
        output_dir: Path | None = None,
        extract_dir: Path | None = None,
    ) -> MinerUParsedResult: ...


@dataclass(slots=True, frozen=True)
class ExtractionBatchItemResult:
    source: ExtractionSource
    status: ExtractionStatus
    result: MinerUParsedResult | None = None
    output_dir: Path | None = None
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class ExtractionJob:
    _client: MinerUClientProtocol
    _task_id: str | UUID | None
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
        task_id: str | UUID | None = None,
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
        task_id: str | UUID,
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

    def _get_batch_result_with_retry(self, batch_id: str) -> BatchExtractResult:
        retries_remaining = BATCH_RESULT_RETRY_COUNT
        while True:
            try:
                return self._client.get_batch_extract_result(batch_id)
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code != 403 or retries_remaining == 0:
                    raise
                retries_remaining -= 1
                time.sleep(BATCH_RESULT_RETRY_DELAY_SECONDS)

    def refresh(self) -> ExtractionStatus:
        if self._task_id is not None:
            self.last_status = ExtractionStatus.from_task(
                self._client.get_extract_task(self._task_id)
            )
            return self.last_status
        if self._batch_id is None:
            raise MinerUResultError("Extraction job has neither task_id nor batch_id")
        result = self._get_batch_result_with_retry(self._batch_id)
        if len(result.results) != 1:
            raise MinerUResultError(
                f"Expected one extraction result, got {len(result.results)}"
            )
        self.last_status = ExtractionStatus.from_batch_task(
            result.batch_id, result.results[0]
        )
        return self.last_status

    def wait(
        self,
        *,
        output_dir: Path | None = None,
        on_update: StatusCallback | None = None,
        on_download_start: DownloadStartCallback | None = None,
    ) -> MinerUParsedResult:
        while True:
            status = self.status()
            if on_update is not None:
                on_update(status)
            if status.state == "done":
                if status.full_zip_url is None:
                    raise MinerUResultError("Extraction completed without full_zip_url")
                if on_download_start is not None:
                    on_download_start()
                extract_dir = (
                    None if output_dir is not None else _source_output_dir(self.source)
                )
                return self._client.download_result(
                    status.full_zip_url,
                    output_dir=output_dir,
                    extract_dir=extract_dir,
                )
            if status.state == "failed":
                raise MinerUTaskFailedError(
                    status.err_msg or "MinerU extraction failed",
                    task_id=str(status.task_id) if status.task_id is not None else None,
                    batch_id=status.batch_id,
                )
            time.sleep(self._poll_interval_seconds)


class ExtractionBatch:
    _client: MinerUClientProtocol
    _batch_id: str
    _item_data_ids: tuple[str, ...]
    _poll_interval_seconds: float
    _results: tuple[ExtractionBatchItemResult | None, ...]
    sources: tuple[ExtractionSource, ...]
    last_statuses: tuple[ExtractionStatus, ...]

    def __init__(
        self,
        client: MinerUClientProtocol,
        batch_id: str,
        *,
        sources: tuple[ExtractionSource, ...],
        statuses: tuple[ExtractionStatus, ...],
        item_data_ids: tuple[str, ...],
        poll_interval_seconds: float = 2.0,
        results: tuple[ExtractionBatchItemResult | None, ...] | None = None,
    ) -> None:
        self._client = client
        self._batch_id = batch_id
        self.sources = sources
        self.last_statuses = statuses
        self._item_data_ids = item_data_ids
        self._poll_interval_seconds = poll_interval_seconds
        if results is None:
            self._results = tuple(None for _ in sources)
        else:
            self._results = results

    @classmethod
    def from_batch(
        cls,
        client: MinerUClientProtocol,
        batch_id: str,
        *,
        sources: tuple[ExtractionSource, ...],
        statuses: tuple[ExtractionStatus, ...],
        item_data_ids: tuple[str, ...],
        poll_interval_seconds: float,
        results: tuple[ExtractionBatchItemResult | None, ...] | None = None,
    ) -> ExtractionBatch:
        return cls(
            client,
            batch_id,
            sources=sources,
            statuses=statuses,
            item_data_ids=item_data_ids,
            poll_interval_seconds=poll_interval_seconds,
            results=results,
        )

    @property
    def batch_id(self) -> str:
        return self._batch_id

    @property
    def item_results(self) -> tuple[ExtractionBatchItemResult | None, ...]:
        return self._results

    def __call__(self) -> tuple[ExtractionStatus, ...]:
        return self.refresh()

    def __await__(
        self,
    ) -> Generator[object, None, tuple[ExtractionBatchItemResult, ...]]:
        return self._await_result().__await__()

    async def _await_result(self) -> tuple[ExtractionBatchItemResult, ...]:
        return await asyncio.to_thread(self.wait)

    def status(self) -> tuple[ExtractionStatus, ...]:
        return self.refresh()

    def _get_batch_result_with_retry(self) -> BatchExtractResult:
        retries_remaining = BATCH_RESULT_RETRY_COUNT
        while True:
            try:
                return self._client.get_batch_extract_result(self._batch_id)
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code != 403 or retries_remaining == 0:
                    raise
                retries_remaining -= 1
                time.sleep(BATCH_RESULT_RETRY_DELAY_SECONDS)

    def refresh(self) -> tuple[ExtractionStatus, ...]:
        active_indices = [
            index for index, result in enumerate(self._results) if result is None
        ]
        if not active_indices:
            return self.last_statuses

        batch_result = self._get_batch_result_with_retry()
        statuses = list(self.last_statuses)
        matched_indices: set[int] = set()
        tasks_by_data_id = {
            task.data_id: task
            for task in batch_result.results
            if task.data_id is not None
        }

        for index in active_indices:
            task = tasks_by_data_id.get(self._item_data_ids[index])
            if task is None:
                continue
            statuses[index] = ExtractionStatus.from_batch_task(
                batch_result.batch_id, task
            )
            matched_indices.add(index)

        unmatched_indices = [
            index for index in active_indices if index not in matched_indices
        ]
        remaining_tasks = [
            task
            for task in batch_result.results
            if task.data_id is None or task.data_id not in self._item_data_ids
        ]
        if remaining_tasks and len(remaining_tasks) == len(unmatched_indices):
            for index, task in zip(unmatched_indices, remaining_tasks, strict=True):
                statuses[index] = ExtractionStatus.from_batch_task(
                    batch_result.batch_id, task
                )

        self.last_statuses = tuple(statuses)
        return self.last_statuses

    def wait(
        self,
        *,
        output_dir: Path | None = None,
        on_update: BatchStatusCallback | None = None,
        on_download_start: BatchDownloadStartCallback | None = None,
        on_item_result: BatchItemResultCallback | None = None,
    ) -> tuple[ExtractionBatchItemResult, ...]:
        results = list(self._results)
        pending = {index for index, result in enumerate(results) if result is None}

        if on_item_result is not None:
            for index, result in enumerate(results):
                if result is not None:
                    on_item_result(index, result)

        while pending:
            statuses = self.status()
            if on_update is not None:
                on_update(statuses)
            for index in list(pending):
                status = statuses[index]
                if status.state == "failed":
                    result = ExtractionBatchItemResult(
                        source=self.sources[index],
                        status=status,
                        error=MinerUTaskFailedError(
                            status.err_msg or "MinerU extraction failed",
                            task_id=str(status.task_id)
                            if status.task_id is not None
                            else None,
                            batch_id=status.batch_id,
                        ),
                    )
                elif status.state == "done":
                    if status.full_zip_url is None:
                        result = ExtractionBatchItemResult(
                            source=self.sources[index],
                            status=status,
                            error=MinerUResultError(
                                "Extraction completed without full_zip_url"
                            ),
                        )
                    else:
                        if on_download_start is not None:
                            on_download_start(index, status)
                        item_output_dir = _batch_item_output_dir(
                            output_dir,
                            len(self.sources),
                            index,
                            self.sources[index],
                            status,
                        )
                        item_extract_dir = (
                            None
                            if item_output_dir is not None
                            else _source_output_dir(self.sources[index])
                        )
                        item_result_dir = item_output_dir or item_extract_dir
                        try:
                            parsed = self._client.download_result(
                                status.full_zip_url,
                                output_dir=item_output_dir,
                                extract_dir=item_extract_dir,
                            )
                        except Exception as exc:
                            result = ExtractionBatchItemResult(
                                source=self.sources[index],
                                status=status,
                                output_dir=item_result_dir,
                                error=exc,
                            )
                        else:
                            result = ExtractionBatchItemResult(
                                source=self.sources[index],
                                status=status,
                                result=parsed,
                                output_dir=parsed.output_dir,
                            )
                else:
                    continue

                results[index] = result
                pending.remove(index)
                self._results = tuple(results)
                if on_item_result is not None:
                    on_item_result(index, result)
            if pending:
                time.sleep(self._poll_interval_seconds)

        return cast(tuple[ExtractionBatchItemResult, ...], tuple(results))


def _batch_item_output_dir(
    output_dir: Path | None,
    item_count: int,
    index: int,
    source: ExtractionSource,
    status: ExtractionStatus,
) -> Path | None:
    if output_dir is None:
        return None
    if item_count == 1:
        return output_dir
    return output_dir / _batch_item_dir_name(index, source, status)


def _batch_item_dir_name(
    index: int, source: ExtractionSource, status: ExtractionStatus
) -> str:
    label = status.file_name or _source_label(source) or status.data_id or "result"
    return f"{index + 1:03d}-{_sanitize_label(label)}"


def _source_label(source: ExtractionSource) -> str | None:
    if source.path is not None:
        return source.path.name
    if source.url is None:
        return None
    path = urlparse(source.url).path.rsplit("/", maxsplit=1)[-1]
    return path or None


def _source_output_dir(source: ExtractionSource) -> Path | None:
    if source.path is None:
        return None
    return default_local_output_dir(source.path)


def _sanitize_label(label: str) -> str:
    sanitized = "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "-"
        for character in label
    ).strip("-.")
    return sanitized or "result"
