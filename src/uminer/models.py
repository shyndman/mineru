from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, cast

from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    NonNegativeInt,
    field_validator,
    model_validator,
)

from .types import (
    BatchTaskState,
    ExtractionSourceKind,
    FileSpec,
    TaskListState,
    TaskState,
)


class MinerUModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow", frozen=True)


class ExtractProgress(MinerUModel):
    extracted_pages: int | None = None
    total_pages: int | None = None
    start_time: str | None = None


class ExtractTask(MinerUModel):
    task_id: UUID4
    state: TaskState | str | None = None
    data_id: str | None = None
    full_zip_url: str | None = None
    err_msg: str | None = None
    extract_progress: ExtractProgress | None = None


class TaskError(MinerUModel):
    code: int | None = None
    message: str | None = None


class TaskListItem(MinerUModel):
    file_name: str
    task_id: UUID4
    file_type: str | None = Field(validation_alias="type")
    state: TaskListState
    full_md_link: HttpUrl | None = None
    error: TaskError | None = None
    created_at: datetime
    model_version: str
    extract_progress: ExtractProgress | None = None
    file_size: NonNegativeInt
    has_chemical_formula: bool = Field(validation_alias="is_chem")
    can_retry: bool
    is_expired: bool = Field(validation_alias="is_expire")
    cover_path: HttpUrl | None = None
    file_url: HttpUrl | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_task_payload(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value

        raw_payload = cast(dict[str, object], value)
        payload = raw_payload.copy()
        _ = payload.pop("rank", None)

        message: object | None = payload.pop("err_msg", None)
        if message == "":
            message = None

        code: object | None = payload.pop("err_code", None)
        if message is not None or code is not None:
            payload["error"] = {"code": code, "message": message}

        return payload

    @field_validator(
        "file_type", "full_md_link", "cover_path", "file_url", mode="before"
    )
    @classmethod
    def empty_string_to_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_created_at(cls, value: object) -> object:
        if isinstance(value, int):
            return datetime.fromtimestamp(value / 1000, UTC)
        return value


class TaskPage(MinerUModel):
    tasks: tuple[TaskListItem, ...] = Field(validation_alias="list")
    total: NonNegativeInt


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
    task_id: UUID4 | None = None
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
