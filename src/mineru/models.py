from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from .types import BatchTaskState, ExtractionSourceKind, FileSpec, TaskState


class MinerUModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow", frozen=True)


class ExtractProgress(MinerUModel):
    extracted_pages: int | None = None
    total_pages: int | None = None
    start_time: str | None = None


class ExtractTask(MinerUModel):
    task_id: str
    state: TaskState | str | None = None
    data_id: str | None = None
    full_zip_url: str | None = None
    err_msg: str | None = None
    extract_progress: ExtractProgress | None = None


class BatchExtractTask(MinerUModel):
    file_name: str
    state: BatchTaskState | str
    data_id: str | None = None
    full_zip_url: str | None = None
    err_msg: str | None = None
    extract_progress: ExtractProgress | None = None


class BatchExtractResult(MinerUModel):
    batch_id: str
    results: tuple[BatchExtractTask, ...] = Field(validation_alias="extract_result")


class UploadBatch(MinerUModel):
    batch_id: str
    file_urls: tuple[str, ...]


class ExtractionStatus(MinerUModel):
    state: TaskState | BatchTaskState | str | None
    task_id: str | None = None
    batch_id: str | None = None
    file_name: str | None = None
    data_id: str | None = None
    full_zip_url: str | None = None
    err_msg: str | None = None
    extract_progress: ExtractProgress | None = None

    @classmethod
    def from_task(cls, task: ExtractTask) -> ExtractionStatus:
        return cls(
            task_id=task.task_id,
            state=task.state,
            data_id=task.data_id,
            full_zip_url=task.full_zip_url,
            err_msg=task.err_msg,
            extract_progress=task.extract_progress,
        )

    @classmethod
    def from_batch_task(cls, batch_id: str, task: BatchExtractTask) -> ExtractionStatus:
        return cls(
            batch_id=batch_id,
            state=task.state,
            file_name=task.file_name,
            data_id=task.data_id,
            full_zip_url=task.full_zip_url,
            err_msg=task.err_msg,
            extract_progress=task.extract_progress,
        )


class ExtractionSource(MinerUModel):
    kind: ExtractionSourceKind
    path: Path | None = None
    url: str | None = None
    file: FileSpec | None = None
