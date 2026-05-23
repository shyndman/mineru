from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Protocol, cast, final

from click.testing import CliRunner
from pytest import MonkeyPatch

from uminer.cli import main, render_task_table
from uminer.errors import MinerUTaskFailedError
from uminer.models import ExtractionSource, ExtractionStatus, ExtractProgress, TaskPage


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
                    "task_id": "task-1",
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
                    "task_id": "task-2",
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
        del source
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
        "\x1b[38;2;255;255;255m\x1b[48;2;43;43;43m\x1b[1mFILENAME    STATE"
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
        last_status=ExtractionStatus(task_id="task-1", state=None),
        statuses=[
            ExtractionStatus(task_id="task-1", state="pending"),
            ExtractionStatus(
                task_id="task-1",
                state="running",
                extract_progress=ExtractProgress(extracted_pages=3, total_pages=12),
            ),
            ExtractionStatus(
                task_id="task-1",
                state="running",
                extract_progress=ExtractProgress(extracted_pages=3, total_pages=12),
            ),
            ExtractionStatus(task_id="task-1", state="converting"),
            ExtractionStatus(
                task_id="task-1",
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
    assert "submitted task task-1 · https://example.com/demo.pdf" in result.stderr
    assert "pending" in result.stderr
    assert "running 3/12 pages" in result.stderr
    assert result.stderr.count("running 3/12 pages") == 1
    assert "converting" in result.stderr
    assert "zip URL: https://cdn.example/result.zip" in result.stderr
    assert "downloading result" in result.stderr
    assert f"saved to {output_dir}" in result.stderr


def test_extract_failure_message_includes_task_id(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("uminer.cli.MinerUClient", FakeMinerUClient)
    FakeMinerUClient.job = FakeJob(
        source=ExtractionSource(kind="url", url="https://example.com/demo.pdf"),
        last_status=ExtractionStatus(task_id="task-1", state=None),
        statuses=[ExtractionStatus(task_id="task-1", state="failed")],
        failure=MinerUTaskFailedError("Unsupported file", task_id="task-1"),
    )

    result = _invoke_main(
        ["--api-key", "token", "extract", "https://example.com/demo.pdf"],
        color=False,
    )

    assert result.exit_code != 0
    assert "task task-1 failed: Unsupported file" in result.stderr


def test_render_task_table_uses_renamed_header() -> None:
    page = TaskPage.model_validate(
        {
            "list": [
                {
                    "file_name": "demo.pdf",
                    "task_id": "task-1",
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
