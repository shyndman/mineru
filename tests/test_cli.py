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
    output_dir = tmp_path / "result"
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
