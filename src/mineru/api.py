from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

DEFAULT_BASE_URL = "https://mineru.net"
DEFAULT_API_KEY_ENV = "MINERU_API_KEY"

ModelVersion = Literal["pipeline", "vlm", "MinerU-HTML"]
ExtraFormat = Literal["docx", "html", "latex"]
TaskState = Literal["done", "pending", "running", "failed", "converting"]
BatchTaskState = Literal[
    "done",
    "waiting-file",
    "pending",
    "running",
    "failed",
    "converting",
]


class MinerUError(Exception):
    pass


class MinerUConfigError(MinerUError):
    pass


class MinerUApiError(MinerUError):
    def __init__(self, code: int | str, message: str, trace_id: str | None = None) -> None:
        self.code = code
        self.message = message
        self.trace_id = trace_id
        suffix = f" (trace_id={trace_id})" if trace_id else ""
        super().__init__(f"MinerU API error {code}: {message}{suffix}")


@dataclass(frozen=True)
class ExtractProgress:
    extracted_pages: int | None = None
    total_pages: int | None = None
    start_time: str | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any] | None) -> ExtractProgress | None:
        if not data:
            return None
        return cls(
            extracted_pages=data.get("extracted_pages"),
            total_pages=data.get("total_pages"),
            start_time=data.get("start_time"),
        )


@dataclass(frozen=True)
class ExtractTask:
    task_id: str
    state: TaskState | str | None = None
    data_id: str | None = None
    full_zip_url: str | None = None
    err_msg: str | None = None
    extract_progress: ExtractProgress | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ExtractTask:
        return cls(
            task_id=data["task_id"],
            state=data.get("state"),
            data_id=data.get("data_id"),
            full_zip_url=data.get("full_zip_url"),
            err_msg=data.get("err_msg"),
            extract_progress=ExtractProgress.from_json(data.get("extract_progress")),
        )


@dataclass(frozen=True)
class BatchExtractTask:
    file_name: str
    state: BatchTaskState | str
    data_id: str | None = None
    full_zip_url: str | None = None
    err_msg: str | None = None
    extract_progress: ExtractProgress | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> BatchExtractTask:
        return cls(
            file_name=data["file_name"],
            state=data["state"],
            data_id=data.get("data_id"),
            full_zip_url=data.get("full_zip_url"),
            err_msg=data.get("err_msg"),
            extract_progress=ExtractProgress.from_json(data.get("extract_progress")),
        )


@dataclass(frozen=True)
class BatchExtractResult:
    batch_id: str
    results: tuple[BatchExtractTask, ...]

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> BatchExtractResult:
        return cls(
            batch_id=data["batch_id"],
            results=tuple(
                BatchExtractTask.from_json(item)
                for item in data.get("extract_result", ())
            ),
        )


@dataclass(frozen=True)
class UploadBatch:
    batch_id: str
    file_urls: tuple[str, ...]

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> UploadBatch:
        return cls(batch_id=data["batch_id"], file_urls=tuple(data["file_urls"]))


class MinerUClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float | httpx.Timeout = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv(DEFAULT_API_KEY_ENV)
        if not self.api_key:
            raise MinerUConfigError(
                f"MinerU API key required. Pass api_key or set {DEFAULT_API_KEY_ENV}."
            )
        self._owns_client = client is None
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> MinerUClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def create_extract_task(
        self,
        url: str,
        *,
        model_version: ModelVersion | None = None,
        is_ocr: bool | None = None,
        enable_formula: bool | None = None,
        enable_table: bool | None = None,
        language: str | None = None,
        data_id: str | None = None,
        callback: str | None = None,
        seed: str | None = None,
        extra_formats: Iterable[ExtraFormat] | None = None,
        page_ranges: str | None = None,
        no_cache: bool | None = None,
        cache_tolerance: int | None = None,
    ) -> ExtractTask:
        data = self._request(
            "POST",
            "/api/v4/extract/task",
            json=self._compact(
                {
                    "url": url,
                    "model_version": model_version,
                    "is_ocr": is_ocr,
                    "enable_formula": enable_formula,
                    "enable_table": enable_table,
                    "language": language,
                    "data_id": data_id,
                    "callback": callback,
                    "seed": seed,
                    "extra_formats": list(extra_formats) if extra_formats is not None else None,
                    "page_ranges": page_ranges,
                    "no_cache": no_cache,
                    "cache_tolerance": cache_tolerance,
                }
            ),
        )
        return ExtractTask.from_json(data)

    def get_extract_task(self, task_id: str) -> ExtractTask:
        data = self._request("GET", f"/api/v4/extract/task/{task_id}")
        return ExtractTask.from_json(data)

    def create_upload_batch(
        self,
        files: Iterable[Mapping[str, Any]],
        *,
        model_version: ModelVersion | None = None,
        enable_formula: bool | None = None,
        enable_table: bool | None = None,
        language: str | None = None,
        callback: str | None = None,
        seed: str | None = None,
        extra_formats: Iterable[ExtraFormat] | None = None,
    ) -> UploadBatch:
        data = self._request(
            "POST",
            "/api/v4/file-urls/batch",
            json=self._compact(
                {
                    "files": list(files),
                    "model_version": model_version,
                    "enable_formula": enable_formula,
                    "enable_table": enable_table,
                    "language": language,
                    "callback": callback,
                    "seed": seed,
                    "extra_formats": list(extra_formats) if extra_formats is not None else None,
                }
            ),
        )
        return UploadBatch.from_json(data)

    def upload_file(self, upload_url: str, path: str | Path) -> None:
        with Path(path).open("rb") as file:
            response = self._client.put(upload_url, content=file)
        response.raise_for_status()

    def upload_files(self, upload_urls: Iterable[str], paths: Iterable[str | Path]) -> None:
        for upload_url, path in zip(upload_urls, paths, strict=True):
            self.upload_file(upload_url, path)

    def create_file_upload_extract_tasks(
        self,
        paths: Iterable[str | Path],
        *,
        files: Iterable[Mapping[str, Any]] | None = None,
        model_version: ModelVersion | None = None,
        enable_formula: bool | None = None,
        enable_table: bool | None = None,
        language: str | None = None,
        callback: str | None = None,
        seed: str | None = None,
        extra_formats: Iterable[ExtraFormat] | None = None,
    ) -> UploadBatch:
        path_tuple = tuple(Path(path) for path in paths)
        file_specs = list(files) if files is not None else [{"name": path.name} for path in path_tuple]
        batch = self.create_upload_batch(
            file_specs,
            model_version=model_version,
            enable_formula=enable_formula,
            enable_table=enable_table,
            language=language,
            callback=callback,
            seed=seed,
            extra_formats=extra_formats,
        )
        self.upload_files(batch.file_urls, path_tuple)
        return batch

    def create_url_batch(
        self,
        files: Iterable[Mapping[str, Any]],
        *,
        model_version: ModelVersion | None = None,
        enable_formula: bool | None = None,
        enable_table: bool | None = None,
        language: str | None = None,
        callback: str | None = None,
        seed: str | None = None,
        extra_formats: Iterable[ExtraFormat] | None = None,
        no_cache: bool | None = None,
        cache_tolerance: int | None = None,
    ) -> str:
        data = self._request(
            "POST",
            "/api/v4/extract/task/batch",
            json=self._compact(
                {
                    "files": list(files),
                    "model_version": model_version,
                    "enable_formula": enable_formula,
                    "enable_table": enable_table,
                    "language": language,
                    "callback": callback,
                    "seed": seed,
                    "extra_formats": list(extra_formats) if extra_formats is not None else None,
                    "no_cache": no_cache,
                    "cache_tolerance": cache_tolerance,
                }
            ),
        )
        return data["batch_id"]

    def get_batch_extract_result(self, batch_id: str) -> BatchExtractResult:
        data = self._request("GET", f"/api/v4/extract-results/batch/{batch_id}")
        return BatchExtractResult.from_json(data)

    def _request(self, method: str, url: str, **kwargs: Any) -> Mapping[str, Any]:
        response = self._client.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "*/*",
            },
            **kwargs,
        )
        body = response.raise_for_status().json()
        if body.get("code") != 0:
            raise MinerUApiError(body.get("code"), body.get("msg", ""), body.get("trace_id"))
        return body["data"]

    @staticmethod
    def _compact(data: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in data.items() if value is not None}
