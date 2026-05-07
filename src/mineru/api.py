from __future__ import annotations

import os
import hashlib
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TypeAlias, cast

import httpx

from .errors import MinerUApiError, MinerUConfigError
from .job import ExtractionJob
from .models import BatchExtractResult, ExtractionSource, ExtractionStatus, ExtractTask, UploadBatch
from .results import MinerUParsedResult, default_result_cache_dir
from .types import DEFAULT_API_KEY_ENV, DEFAULT_BASE_URL, MODEL_VERSION, ExtraFormat, FileSpec, Json


class MinerUClient:
    api_key: str
    _owns_client: bool
    _client: httpx.Client

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float | httpx.Timeout = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        resolved_api_key = api_key or os.getenv(DEFAULT_API_KEY_ENV)
        if not resolved_api_key:
            raise MinerUConfigError(
                f"MinerU API key required. Pass api_key or set {DEFAULT_API_KEY_ENV}."
            )
        self.api_key = resolved_api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> MinerUClient:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        _ = exc_type, exc_value, traceback
        self.close()

    def extract_url(
        self,
        url: str,
        *,
        is_ocr: bool | None = None,
        enable_formula: bool | None = None,
        enable_table: bool | None = None,
        language: str | None = None,
        data_id: str | None = None,
        extra_formats: Iterable[ExtraFormat] | None = None,
        page_ranges: str | None = None,
        no_cache: bool | None = None,
        cache_tolerance: int | None = None,
        poll_interval_seconds: float = 2.0,
    ) -> ExtractionJob:
        task = self.create_extract_task(
            url,
            is_ocr=is_ocr,
            enable_formula=enable_formula,
            enable_table=enable_table,
            language=language,
            data_id=data_id,
            extra_formats=extra_formats,
            page_ranges=page_ranges,
            no_cache=no_cache,
            cache_tolerance=cache_tolerance,
        )
        return ExtractionJob.from_task(
            self,
            task.task_id,
            source=ExtractionSource(kind="url", url=url),
            status=ExtractionStatus.from_task(task),
            poll_interval_seconds=poll_interval_seconds,
        )

    def extract_file(
        self,
        path: str | Path,
        *,
        file: FileSpec | None = None,
        enable_formula: bool | None = None,
        enable_table: bool | None = None,
        language: str | None = None,
        extra_formats: Iterable[ExtraFormat] | None = None,
        poll_interval_seconds: float = 2.0,
    ) -> ExtractionJob:
        file_path = Path(path)
        file_spec = file if file is not None else {"name": file_path.name}
        batch = self.create_file_upload_extract_tasks(
            [file_path],
            files=[file_spec],
            enable_formula=enable_formula,
            enable_table=enable_table,
            language=language,
            extra_formats=extra_formats,
        )
        return ExtractionJob.from_batch(
            self,
            batch.batch_id,
            source=ExtractionSource(kind="file", path=file_path, file=file_spec),
            status=ExtractionStatus(
                batch_id=batch.batch_id,
                state="waiting-file",
                file_name=str(file_spec.get("name", file_path.name)),
            ),
            poll_interval_seconds=poll_interval_seconds,
        )

    def create_extract_task(
        self,
        url: str,
        *,
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
            json_body=_compact(
                {
                    "url": url,
                    "model_version": MODEL_VERSION,
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
        return ExtractTask.model_validate(data)

    def get_extract_task(self, task_id: str) -> ExtractTask:
        data = self._request("GET", f"/api/v4/extract/task/{task_id}")
        return ExtractTask.model_validate(data)

    def create_upload_batch(
        self,
        files: Iterable[FileSpec],
        *,
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
            json_body=_compact(
                {
                    "files": list(files),
                    "model_version": MODEL_VERSION,
                    "enable_formula": enable_formula,
                    "enable_table": enable_table,
                    "language": language,
                    "callback": callback,
                    "seed": seed,
                    "extra_formats": list(extra_formats) if extra_formats is not None else None,
                }
            ),
        )
        return UploadBatch.model_validate(data)

    def upload_file(self, upload_url: str, path: str | Path) -> None:
        with Path(path).open("rb") as file:
            response = self._client.put(upload_url, content=file)
        _ = response.raise_for_status()

    def upload_files(self, upload_urls: Iterable[str], paths: Iterable[str | Path]) -> None:
        for upload_url, path in zip(upload_urls, paths, strict=True):
            self.upload_file(upload_url, path)

    def create_file_upload_extract_tasks(
        self,
        paths: Iterable[str | Path],
        *,
        files: Iterable[FileSpec] | None = None,
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
        files: Iterable[FileSpec],
        *,
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
            json_body=_compact(
                {
                    "files": list(files),
                    "model_version": MODEL_VERSION,
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
        batch_id = data["batch_id"]
        if not isinstance(batch_id, str):
            raise TypeError("Expected batch_id to be str")
        return batch_id

    def get_batch_extract_result(self, batch_id: str) -> BatchExtractResult:
        data = self._request("GET", f"/api/v4/extract-results/batch/{batch_id}")
        return BatchExtractResult.model_validate(data)

    def download_result(self, full_zip_url: str, *, output_dir: Path | None = None) -> MinerUParsedResult:
        result_dir = output_dir or default_result_cache_dir(_result_id(full_zip_url))
        result_dir.mkdir(parents=True, exist_ok=True)
        zip_path = result_dir / "result.zip"
        with self._client.stream("GET", full_zip_url) as response:
            _ = response.raise_for_status()
            with zip_path.open("wb") as file:
                for chunk in response.iter_bytes():
                    _ = file.write(chunk)
        return MinerUParsedResult.from_zip_file(zip_path, result_dir)

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: Mapping[str, Json] | None = None,
    ) -> dict[str, object]:
        response = self._client.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "*/*",
            },
            json=json_body,
        )
        _ = response.raise_for_status()
        body = cast(dict[str, object], response.json())
        if body.get("code") != 0:
            raise MinerUApiError(
                _required_int_or_str(body, "code"),
                _optional_str(body, "msg") or "",
                _optional_str(body, "trace_id"),
            )
        data = body["data"]
        if not isinstance(data, dict):
            raise TypeError("Expected data to be object")
        return cast(dict[str, object], data)


TransportHandler: TypeAlias = Callable[[httpx.Request], httpx.Response]


def _compact(data: Mapping[str, Json | None]) -> dict[str, Json]:
    return {key: value for key, value in data.items() if value is not None}


def _optional_str(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"Expected {key} to be str")
    return value


def _required_int_or_str(data: Mapping[str, object], key: str) -> int | str:
    value = data[key]
    if not isinstance(value, int | str):
        raise TypeError(f"Expected {key} to be int or str")
    return value


def _result_id(full_zip_url: str) -> str:
    return hashlib.sha256(full_zip_url.encode("utf-8")).hexdigest()[:24]
