from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Protocol, cast, final
from uuid import UUID

import click
from click.testing import CliRunner
from pytest import MonkeyPatch

import uminer.cli as cli_module
from uminer import ExtractionBatchItemResult
from uminer.cli import main, render_task_table
from uminer.errors import MinerUTaskFailedError
from uminer.models import ExtractionSource, ExtractionStatus, ExtractProgress, TaskPage

FAKE_TASK_ID = "550e8400-e29b-41d4-a716-446655440000"
FAKE_TASK_ID_2 = "550e8400-e29b-41d4-a716-446655440001"
FAKE_TASK_UUID = UUID(FAKE_TASK_ID)
FAKE_TASK_UUID_2 = UUID(FAKE_TASK_ID_2)


class CliResult(Protocol):
    exit_code: int
    output: str
    stdout: str
    stderr: str


@final
class SplitCliRunner(CliRunner):
    mix_stderr: bool

    def __init__(self) -> None:
        super().__init__()
        self.mix_stderr = False


@final
class FakeResult:
    output_dir: Path | None

    def __init__(self, output_dir: Path | None) -> None:
        self.output_dir = output_dir


def _invoke_main(args: list[str], *, color: bool = False) -> CliResult:
    runner = SplitCliRunner()
    return cast(CliResult, cast(object, runner.invoke(main, args, color=color)))


@final
class FakeJob:
    source: ExtractionSource
    last_status: ExtractionStatus
    _statuses: list[ExtractionStatus]
    _output_dir: Path | None
    _failure: MinerUTaskFailedError | None

    def __init__(
        self,
        *,
        source: ExtractionSource,
        last_status: ExtractionStatus,
        statuses: list[ExtractionStatus],
        output_dir: Path | None = None,
        failure: MinerUTaskFailedError | None = None,
    ) -> None:
        self.source = source
        self.last_status = last_status
        self._statuses = statuses
        self._output_dir = output_dir
        self._failure = failure

    def wait(
        self,
        *,
        output_dir: Path | None = None,
        on_update: Callable[[ExtractionStatus], None] | None = None,
        on_download_start: Callable[[], None] | None = None,
    ) -> FakeResult:
        del output_dir
        for status in self._statuses:
            self.last_status = status
            if on_update is not None:
                on_update(status)
        if self._failure is not None:
            raise self._failure
        if on_download_start is not None:
            on_download_start()
        return FakeResult(self._output_dir)


@final
class FakeBatch:
    sources: tuple[ExtractionSource, ...]
    last_statuses: tuple[ExtractionStatus, ...]
    item_results: tuple[ExtractionBatchItemResult | None, ...]
    _status_updates: tuple[tuple[ExtractionStatus, ...], ...]
    _final_results: tuple[ExtractionBatchItemResult, ...]

    def __init__(
        self,
        *,
        sources: tuple[ExtractionSource, ...],
        last_statuses: tuple[ExtractionStatus, ...],
        item_results: tuple[ExtractionBatchItemResult | None, ...],
        status_updates: tuple[tuple[ExtractionStatus, ...], ...],
        final_results: tuple[ExtractionBatchItemResult, ...],
    ) -> None:
        self.sources = sources
        self.last_statuses = last_statuses
        self.item_results = item_results
        self._status_updates = status_updates
        self._final_results = final_results

    def wait(
        self,
        *,
        output_dir: Path | None = None,
        on_update: Callable[[tuple[ExtractionStatus, ...]], None] | None = None,
        on_download_start: Callable[[int, ExtractionStatus], None] | None = None,
        on_item_result: Callable[[int, ExtractionBatchItemResult], None] | None = None,
    ) -> tuple[ExtractionBatchItemResult, ...]:
        del output_dir
        for index, result in enumerate(self.item_results):
            if result is not None and on_item_result is not None:
                on_item_result(index, result)
        for statuses in self._status_updates:
            self.last_statuses = statuses
            if on_update is not None:
                on_update(statuses)
        for index, result in enumerate(self._final_results):
            if self.item_results[index] is not None:
                continue
            if result.error is None and on_download_start is not None:
                on_download_start(index, result.status)
            if on_item_result is not None:
                on_item_result(index, result)
        self.item_results = self._final_results
        return self._final_results


@final
class FakeMinerUClient:
    page: ClassVar[TaskPage] = TaskPage.model_validate(
        {
            "list": [
                {
                    "file_name": "done.pdf",
                    "task_id": FAKE_TASK_ID,
                    "type": "pdf",
                    "state": "done",
                    "full_md_link": "",
                    "err_msg": "",
                    "created_at": 1778173950469,
                    "model_version": "vlm",
                    "file_size": 10,
                    "is_chem": False,
                    "can_retry": False,
                    "is_expire": False,
                    "cover_path": "",
                    "file_url": "",
                },
                {
                    "file_name": "failed.pdf",
                    "task_id": FAKE_TASK_ID_2,
                    "type": "pdf",
                    "state": "failed",
                    "full_md_link": "",
                    "err_msg": "broken",
                    "err_code": -1,
                    "created_at": 1778173950469,
                    "model_version": "vlm",
                    "file_size": 10,
                    "is_chem": False,
                    "can_retry": True,
                    "is_expire": False,
                    "cover_path": "",
                    "file_url": "",
                },
            ],
            "total": 2,
        }
    )
    job: FakeJob | None = None
    batch: FakeBatch | None = None

    api_key: str
    base_url: str

    def __init__(self, api_key: str, base_url: str) -> None:
        self.api_key = api_key
        self.base_url = base_url

    def close(self) -> None:
        return None

    def list_tasks(self, *, page_no: int, page_size: int) -> TaskPage:
        del page_no, page_size
        return self.page

    def extract_url(self, source: str, **_: object) -> FakeJob:
        del source
        if self.job is None:
            raise AssertionError("test did not configure FakeMinerUClient.job")
        return self.job

    def extract_urls(self, sources: tuple[str, ...], **_: object) -> FakeBatch:
        del sources
        if self.batch is None:
            raise AssertionError("test did not configure FakeMinerUClient.batch")
        return self.batch

    def extract_file(self, source: Path, **_: object) -> FakeJob:
        on_upload_progress = cast(
            Callable[[Path, int, int], None] | None, _.get("on_upload_progress")
        )
        if on_upload_progress is not None:
            total_bytes = source.stat().st_size
            halfway = total_bytes // 2
            if 0 < halfway < total_bytes:
                on_upload_progress(source, halfway, total_bytes)
            on_upload_progress(source, total_bytes, total_bytes)
        if self.job is None:
            raise AssertionError("test did not configure FakeMinerUClient.job")
        return self.job

    def extract_files(self, sources: tuple[Path, ...], **_: object) -> FakeBatch:
        on_upload_progress = cast(
            Callable[[int, Path, int, int], None] | None, _.get("on_upload_progress")
        )
        if on_upload_progress is not None:
            for index, source in enumerate(sources):
                total_bytes = source.stat().st_size
                on_upload_progress(index, source, total_bytes, total_bytes)
        if self.batch is None:
            raise AssertionError("test did not configure FakeMinerUClient.batch")
        return self.batch

    get_extract_task_result: ClassVar[object] = None
    download_result_value: ClassVar[object] = None
    get_extract_task_results: ClassVar[dict[str, object] | None] = None
    download_result_values: ClassVar[dict[str, object] | None] = None

    def get_extract_task(self, task_id: str | object) -> object:
        if (
            isinstance(task_id, str)
            and self.get_extract_task_results is not None
            and task_id in self.get_extract_task_results
        ):
            return self.get_extract_task_results[task_id]
        if self.get_extract_task_result is None:
            raise AssertionError(
                "test did not configure FakeMinerUClient.get_extract_task_result"
            )
        return self.get_extract_task_result

    def download_result(
        self,
        full_zip_url: str,
        *,
        output_dir: object = None,
        extract_dir: object = None,
    ) -> object:
        del output_dir, extract_dir
        if (
            self.download_result_values is not None
            and full_zip_url in self.download_result_values
        ):
            return self.download_result_values[full_zip_url]
        if self.download_result_value is None:
            raise AssertionError(
                "test did not configure FakeMinerUClient.download_result_value"
            )
        return self.download_result_value


def test_short_help_aliases_show_help(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("uminer.cli.MinerUClient", FakeMinerUClient)

    runner = CliRunner()
    main_help = runner.invoke(main, ["--api-key", "token", "-h"])
    extract_help = runner.invoke(main, ["--api-key", "token", "extract", "-h"])
    list_help = runner.invoke(main, ["--api-key", "token", "list", "-h"])

    assert main_help.exit_code == 0, main_help.output
    assert extract_help.exit_code == 0, extract_help.output
    assert list_help.exit_code == 0, list_help.output
    assert "extract" in main_help.output
    assert "--poll-interval" in extract_help.output
    assert "SOURCE.uminer/" in extract_help.output
    assert "--page-size" in list_help.output


def test_list_output_uses_filename_header_and_colors(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("uminer.cli.MinerUClient", FakeMinerUClient)

    result = CliRunner().invoke(main, ["--api-key", "token", "list"], color=True)

    assert result.exit_code == 0, result.output
    assert "FILENAME" in result.stdout
    assert "FILE_NAME" not in result.stdout
    assert (
        "\x1b[38;2;255;255;255m\x1b[48;2;38;34;59m\x1b[1mFILENAME    STATE"
        in result.stdout
    )
    assert "\x1b[38;2;86;120;224m" in result.stdout
    assert "\x1b[38;2;186;68;68m" in result.stdout
    assert "\x1b[2mpage 1 · showing 2 of 2\x1b[0m" in result.stdout


def test_extract_reports_progress_and_keeps_stdout_clean(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("uminer.cli.MinerUClient", FakeMinerUClient)
    output_dir = tmp_path / "result"
    FakeMinerUClient.job = FakeJob(
        source=ExtractionSource(kind="url", url="https://example.com/demo.pdf"),
        last_status=ExtractionStatus(task_id=FAKE_TASK_UUID, state=None),
        statuses=[
            ExtractionStatus(task_id=FAKE_TASK_UUID, state="pending"),
            ExtractionStatus(
                task_id=FAKE_TASK_UUID,
                state="running",
                extract_progress=ExtractProgress(extracted_pages=3, total_pages=12),
            ),
            ExtractionStatus(
                task_id=FAKE_TASK_UUID,
                state="running",
                extract_progress=ExtractProgress(extracted_pages=3, total_pages=12),
            ),
            ExtractionStatus(task_id=FAKE_TASK_UUID, state="converting"),
            ExtractionStatus(
                task_id=FAKE_TASK_UUID,
                state="done",
                full_zip_url="https://cdn.example/result.zip",
            ),
        ],
        output_dir=output_dir,
    )

    result = _invoke_main(
        ["--api-key", "token", "extract", "https://example.com/demo.pdf"],
        color=False,
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == f"{output_dir}\n"
    assert "\r" not in result.stderr
    assert (
        f"submitted task {FAKE_TASK_ID} · https://example.com/demo.pdf" in result.stderr
    )
    assert "pending" in result.stderr
    assert "running 3/12 pages" in result.stderr
    assert result.stderr.count("running 3/12 pages") == 1
    assert "converting" in result.stderr
    assert "extracting 12 pages · done" in result.stderr
    assert result.stderr.count("extracting 12 pages · done") == 1
    assert "converting · done" in result.stderr
    assert "zip URL: https://cdn.example/result.zip" in result.stderr
    assert "downloading" in result.stderr
    assert "downloading · done" in result.stderr
    assert f"saved to {output_dir}" in result.stderr


def test_extract_uses_rgb_colors(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("uminer.cli.MinerUClient", FakeMinerUClient)
    output_dir = tmp_path / "result"
    FakeMinerUClient.job = FakeJob(
        source=ExtractionSource(kind="url", url="https://example.com/demo.pdf"),
        last_status=ExtractionStatus(task_id=FAKE_TASK_UUID, state=None),
        statuses=[
            ExtractionStatus(task_id=FAKE_TASK_UUID, state="pending"),
            ExtractionStatus(
                task_id=FAKE_TASK_UUID,
                state="done",
                full_zip_url="https://cdn.example/result.zip",
            ),
        ],
        output_dir=output_dir,
    )

    result = _invoke_main(
        ["--api-key", "token", "extract", "https://example.com/demo.pdf"],
        color=True,
    )
    unstyled_stderr = click.unstyle(result.stderr)

    assert result.exit_code == 0, result.output
    # STATE blue for transitions
    assert "\x1b[38;2;95;130;220m" in result.stderr
    # LABEL gray for labels
    assert "\x1b[38;2;140;140;140m" in result.stderr
    # PUNCT gray for punctuation
    assert "\x1b[38;2;120;120;120m" in result.stderr
    # REF teal for task IDs
    assert "\x1b[38;2;108;160;172m" in result.stderr
    assert "pending" in result.stderr
    assert "zip URL" in unstyled_stderr
    assert "https://cdn.example/result.zip" in unstyled_stderr
    assert "downloading · done" in unstyled_stderr
    assert "saved to" in unstyled_stderr
    assert str(output_dir) in unstyled_stderr
    # No old named terminal colors
    assert "\x1b[93m" not in result.stderr
    assert "\x1b[96m" not in result.stderr
    assert "\x1b[92m" not in result.stderr


def test_extract_file_reports_upload_progress_before_submission(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("uminer.cli.MinerUClient", FakeMinerUClient)
    source_path = tmp_path / "demo.pdf"
    _ = source_path.write_bytes(b"1234")
    output_dir = tmp_path / "demo.pdf.uminer"
    FakeMinerUClient.job = FakeJob(
        source=ExtractionSource(
            kind="file", path=source_path, file={"name": source_path.name}
        ),
        last_status=ExtractionStatus(batch_id="batch-1", state="waiting-file"),
        statuses=[
            ExtractionStatus(batch_id="batch-1", state="pending"),
            ExtractionStatus(
                batch_id="batch-1",
                state="done",
                full_zip_url="https://cdn.example/result.zip",
            ),
        ],
        output_dir=output_dir,
    )

    result = _invoke_main(["--api-key", "token", "extract", str(source_path)])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"{output_dir}\n"
    upload_start = f"uploading {source_path} · 0%"
    upload_half = f"uploading {source_path} · 50%"
    upload_done = f"uploading {source_path} · done"
    submitted = f"submitted batch batch-1 · {source_path}"
    assert upload_start in result.stderr
    assert upload_half in result.stderr
    assert upload_done in result.stderr
    assert f"uploading {source_path} · 100%" not in result.stderr
    assert submitted in result.stderr
    assert result.stderr.index(upload_start) < result.stderr.index(upload_half)
    assert result.stderr.index(upload_half) < result.stderr.index(upload_done)
    assert result.stderr.index(upload_done) < result.stderr.index(submitted)


def test_upload_progress_keeps_percentage_when_path_is_truncated() -> None:
    upload_progress_message = cast(
        Callable[[Path, int, int], str], vars(cli_module)["_upload_progress_message"]
    )
    message = upload_progress_message(
        Path("/very/long/path/to/a/document/with/a/long/name.pdf"), 37, 24
    )
    unstyled_message = click.unstyle(message)

    assert len(unstyled_message) <= 24
    assert unstyled_message.endswith("37%")
    assert "…" in unstyled_message
    assert "\x1b[38;2;140;140;140m" in message
    assert "\x1b[38;2;120;120;120m" in message


def test_extract_uses_singular_page_completion(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("uminer.cli.MinerUClient", FakeMinerUClient)
    output_dir = tmp_path / "result"
    FakeMinerUClient.job = FakeJob(
        source=ExtractionSource(kind="url", url="https://example.com/demo.pdf"),
        last_status=ExtractionStatus(task_id=FAKE_TASK_UUID, state=None),
        statuses=[
            ExtractionStatus(
                task_id=FAKE_TASK_UUID,
                state="running",
                extract_progress=ExtractProgress(extracted_pages=1, total_pages=1),
            ),
            ExtractionStatus(
                task_id=FAKE_TASK_UUID,
                state="done",
                full_zip_url="https://cdn.example/result.zip",
            ),
        ],
        output_dir=output_dir,
    )

    result = _invoke_main(
        ["--api-key", "token", "extract", "https://example.com/demo.pdf"],
        color=False,
    )

    assert result.exit_code == 0, result.output
    assert "extracting 1 page · done" in result.stderr
    assert "extracting 1 pages · done" not in result.stderr


def test_extract_failure_message_includes_task_id(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("uminer.cli.MinerUClient", FakeMinerUClient)
    FakeMinerUClient.job = FakeJob(
        source=ExtractionSource(kind="url", url="https://example.com/demo.pdf"),
        last_status=ExtractionStatus(task_id=FAKE_TASK_UUID, state=None),
        statuses=[ExtractionStatus(task_id=FAKE_TASK_UUID, state="failed")],
        failure=MinerUTaskFailedError("Unsupported file", task_id=FAKE_TASK_ID),
    )

    result = _invoke_main(
        ["--api-key", "token", "extract", "https://example.com/demo.pdf"],
        color=False,
    )

    assert result.exit_code != 0
    assert f"task {FAKE_TASK_ID} failed: Unsupported file" in result.stderr


def test_render_task_table_uses_renamed_header() -> None:
    page = TaskPage.model_validate(
        {
            "list": [
                {
                    "file_name": "demo.pdf",
                    "task_id": FAKE_TASK_ID,
                    "type": "pdf",
                    "state": "done",
                    "full_md_link": "",
                    "err_msg": "",
                    "created_at": int(
                        datetime(2026, 5, 23, tzinfo=UTC).timestamp() * 1000
                    ),
                    "model_version": "vlm",
                    "file_size": 10,
                    "is_chem": False,
                    "can_retry": False,
                    "is_expire": False,
                    "cover_path": "",
                    "file_url": "",
                }
            ],
            "total": 1,
        }
    )

    rendered = render_task_table(page)

    assert "FILENAME" in rendered
    assert "FILE_NAME" not in rendered
    assert rendered.index("FILENAME") < rendered.index("STATE")
    assert rendered.index("STATE") < rendered.index("CREATED")
    assert rendered.index("CREATED") < rendered.index("TASK_ID")


def test_extract_by_task_id_downloads_result(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("uminer.cli.MinerUClient", FakeMinerUClient)
    from uminer.models import ExtractTask

    output_dir = tmp_path / "result"
    FakeMinerUClient.get_extract_task_result = ExtractTask(
        task_id=FAKE_TASK_UUID,
        state="done",
        full_zip_url="https://cdn.example/result.zip",
    )

    FakeMinerUClient.download_result_value = FakeResult(output_dir)

    result = _invoke_main(
        ["--api-key", "token", "extract", FAKE_TASK_ID],
        color=False,
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == f"{output_dir}\n"
    assert "downloading · done" in result.stderr
    assert f"saved to {output_dir}" in result.stderr


def test_extract_by_task_id_errors_if_not_done(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("uminer.cli.MinerUClient", FakeMinerUClient)
    from uminer.models import ExtractTask

    FakeMinerUClient.get_extract_task_result = ExtractTask(
        task_id=FAKE_TASK_UUID,
        state="running",
    )

    result = _invoke_main(
        ["--api-key", "token", "extract", FAKE_TASK_ID],
        color=False,
    )

    assert result.exit_code != 0
    assert (
        f"Task {FAKE_TASK_ID} is not done" in result.stderr
        or f"Task {FAKE_TASK_ID} is not done" in result.output
    )


def test_extract_multiple_urls_reports_ordinal_results_and_continues(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("uminer.cli.MinerUClient", FakeMinerUClient)
    output_dir = tmp_path / "out"
    success_dir = output_dir / "001-demo.pdf"
    batch_sources = (
        ExtractionSource(kind="url", url="https://example.com/demo.pdf"),
        ExtractionSource(kind="url", url="https://example.com/broken.pdf"),
    )
    success_status = ExtractionStatus(
        batch_id="batch-1",
        state="done",
        file_name="demo.pdf",
        full_zip_url="https://cdn.example/demo.zip",
    )
    failed_status = ExtractionStatus(
        batch_id="batch-1",
        state="failed",
        file_name="broken.pdf",
        err_msg="Unsupported file",
    )
    FakeMinerUClient.batch = FakeBatch(
        sources=batch_sources,
        last_statuses=(
            ExtractionStatus(batch_id="batch-1", state=None),
            ExtractionStatus(batch_id="batch-1", state=None),
        ),
        item_results=(None, None),
        status_updates=(
            (
                ExtractionStatus(batch_id="batch-1", state="running"),
                ExtractionStatus(batch_id="batch-1", state="pending"),
            ),
        ),
        final_results=(
            ExtractionBatchItemResult(
                source=batch_sources[0],
                status=success_status,
                output_dir=success_dir,
            ),
            ExtractionBatchItemResult(
                source=batch_sources[1],
                status=failed_status,
                error=MinerUTaskFailedError("Unsupported file", batch_id="batch-1"),
            ),
        ),
    )

    result = _invoke_main(
        [
            "--api-key",
            "token",
            "extract",
            "https://example.com/demo.pdf",
            "https://example.com/broken.pdf",
            "-o",
            str(output_dir),
        ]
    )

    assert result.exit_code != 0
    assert result.stdout == f"{success_dir}\n"
    assert "1. submitted batch batch-1 · https://example.com/demo.pdf" in result.stderr
    assert (
        "2. submitted batch batch-1 · https://example.com/broken.pdf" in result.stderr
    )
    assert f"1. saved to {success_dir}" in result.stderr
    assert "2. failed · Unsupported file" in result.stderr


def test_extract_multiple_files_reports_upload_failures_and_successes(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("uminer.cli.MinerUClient", FakeMinerUClient)
    first = tmp_path / "one.pdf"
    second = tmp_path / "two.pdf"
    _ = first.write_bytes(b"one")
    _ = second.write_bytes(b"two")
    output_dir = tmp_path / "out"
    success_dir = output_dir / "001-one.pdf"
    batch_sources = (
        ExtractionSource(kind="file", path=first, file={"name": first.name}),
        ExtractionSource(kind="file", path=second, file={"name": second.name}),
    )
    failed_status = ExtractionStatus(
        batch_id="batch-1",
        state="failed",
        file_name=second.name,
        err_msg="upload denied",
    )
    FakeMinerUClient.batch = FakeBatch(
        sources=batch_sources,
        last_statuses=(
            ExtractionStatus(
                batch_id="batch-1",
                state="waiting-file",
                file_name=first.name,
            ),
            failed_status,
        ),
        item_results=(
            None,
            ExtractionBatchItemResult(
                source=batch_sources[1],
                status=failed_status,
                error=RuntimeError("upload denied"),
            ),
        ),
        status_updates=(
            (
                ExtractionStatus(
                    batch_id="batch-1",
                    state="running",
                    file_name=first.name,
                ),
                failed_status,
            ),
        ),
        final_results=(
            ExtractionBatchItemResult(
                source=batch_sources[0],
                status=ExtractionStatus(
                    batch_id="batch-1",
                    state="done",
                    file_name=first.name,
                    full_zip_url="https://cdn.example/one.zip",
                ),
                output_dir=success_dir,
            ),
            ExtractionBatchItemResult(
                source=batch_sources[1],
                status=failed_status,
                error=RuntimeError("upload denied"),
            ),
        ),
    )

    result = _invoke_main(
        [
            "--api-key",
            "token",
            "extract",
            str(first),
            str(second),
            "-o",
            str(output_dir),
        ]
    )

    assert result.exit_code != 0
    assert result.stdout == f"{success_dir}\n"
    assert f"1. submitted batch batch-1 · {first}" in result.stderr
    assert "2. failed · upload denied" in result.stderr
    assert f"1. saved to {success_dir}" in result.stderr


def test_extract_multiple_task_ids_reports_each_result(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("uminer.cli.MinerUClient", FakeMinerUClient)
    from uminer.models import ExtractTask

    output_dir = tmp_path / "out"
    success_dir = output_dir / f"001-{FAKE_TASK_ID}"
    FakeMinerUClient.get_extract_task_results = {
        FAKE_TASK_ID: ExtractTask(
            task_id=FAKE_TASK_UUID,
            state="done",
            full_zip_url="https://cdn.example/result.zip",
        ),
        FAKE_TASK_ID_2: ExtractTask(
            task_id=FAKE_TASK_UUID_2,
            state="running",
        ),
    }
    FakeMinerUClient.download_result_values = {
        "https://cdn.example/result.zip": FakeResult(success_dir)
    }

    result = _invoke_main(
        [
            "--api-key",
            "token",
            "extract",
            FAKE_TASK_ID,
            FAKE_TASK_ID_2,
            "-o",
            str(output_dir),
        ]
    )

    assert result.exit_code != 0
    assert result.stdout == f"{success_dir}\n"
    assert f"1. saved to {success_dir}" in result.stderr
    assert (
        f"2. failed · task {FAKE_TASK_ID_2} is not done (state: running)"
        in result.stderr
    )


def test_extract_rejects_mixed_source_kinds(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("uminer.cli.MinerUClient", FakeMinerUClient)
    source_path = tmp_path / "demo.pdf"
    _ = source_path.write_bytes(b"demo")

    result = _invoke_main(
        [
            "--api-key",
            "token",
            "extract",
            str(source_path),
            "https://example.com/demo.pdf",
        ]
    )

    assert result.exit_code != 0
    assert "same kind" in result.stderr or "same kind" in result.output
