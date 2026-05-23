import json
import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import final
from urllib.parse import urlparse

import click
from yaspin import yaspin as create_spinner
from yaspin.core import Yaspin
from yaspin.spinners import Spinners  # pyright: ignore[reportAny]

from .api import MinerUClient
from .errors import MinerUError, MinerUTaskFailedError
from .job import ExtractionJob
from .models import ExtractionStatus, TaskPage
from .types import DEFAULT_API_KEY_ENV, DEFAULT_BASE_URL

DEFAULT_ENV_FILE: Path = Path.home() / ".config" / "uminer" / "uminer.env"
CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

type RgbColor = tuple[int, int, int]
type CliColor = str | RgbColor

_HOME_PREFIX: str = str(Path.home())

HEADER_BACKGROUND: RgbColor = (38, 34, 59)
HEADER_FOREGROUND: RgbColor = (255, 255, 255)
STATE: RgbColor = (95, 130, 220)
STATE_FAILED: RgbColor = (186, 68, 68)
LIST_STATE_DONE: RgbColor = (86, 120, 224)
STATE_COLORS: dict[str, RgbColor] = {
    "waiting-file": STATE,
    "uploading": STATE,
    "pending": STATE,
    "running": STATE,
    "converting": STATE,
    "failed": STATE_FAILED,
    "done": LIST_STATE_DONE,
}
LABEL: RgbColor = (140, 140, 140)
PUNCT: RgbColor = (120, 120, 120)
REF: RgbColor = (108, 160, 172)


@final
class _StatusPrinter:
    _spinner: Yaspin | None
    _use_spinner: bool

    def __init__(self) -> None:
        self._use_spinner = sys.stderr.isatty()
        self._spinner = None

    def update(self, message: str) -> None:
        if self._use_spinner:
            if self._spinner is None:
                self._spinner = create_spinner(
                    Spinners.dots,  # pyright: ignore[reportAny]
                    stream=sys.stderr,
                )
                self._spinner.start()
            self._spinner.text = message
        else:
            click.echo(message, err=True)

    def complete(self, message: str) -> None:
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None
        click.echo(message, err=True)

    def finish(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None


@dataclass(slots=True, frozen=True)
class CLIContext:
    client: MinerUClient


def _parse_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if not sep:
                continue
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                result[key] = value
    return result


def _resolve_api_key(*, explicit: str | None, env_file: Path) -> str:
    if explicit:
        return explicit
    if env_file.exists():
        parsed = _parse_env_file(env_file)
        from_file = parsed.get(DEFAULT_API_KEY_ENV)
        if from_file:
            return from_file
    from_env = os.environ.get(DEFAULT_API_KEY_ENV)
    if from_env:
        return from_env
    message = (
        f"No MinerU API key found. Pass --api-key, set {DEFAULT_API_KEY_ENV}, "
        + f"or place one in {env_file}."
    )
    raise click.UsageError(message)


def _is_url(source: str) -> bool:
    return urlparse(source).scheme in {"http", "https"}


def _format_created_at(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _get_ctx(ctx: click.Context) -> CLIContext:
    obj = ctx.find_object(CLIContext)
    if obj is None:
        raise RuntimeError("CLI context not initialized")
    return obj


def _state_color(state: str | None) -> RgbColor | None:
    if state is None:
        return None
    return STATE_COLORS.get(state)


def _tilde(path: Path | str) -> str:
    s = str(path)
    if s.startswith(_HOME_PREFIX):
        return "~" + s[len(_HOME_PREFIX) :]
    return s


def _styled_job_reference(job: ExtractionJob) -> str:
    status = job.last_status
    if status.task_id is not None:
        return click.style("task ", fg=LABEL) + click.style(status.task_id, fg=REF)
    if status.batch_id is not None:
        return click.style("batch ", fg=LABEL) + click.style(status.batch_id, fg=REF)
    return click.style("job", fg=LABEL)


def _source_label(job: ExtractionJob) -> str:
    source = job.source
    if source.kind == "url":
        if source.url is None:
            raise RuntimeError("URL extraction job missing source URL")
        return source.url
    if source.path is not None:
        return _tilde(source.path)
    return "file"


def _progress_message(status: ExtractionStatus) -> str:
    state = status.state or "submitted"
    progress = status.extract_progress
    if progress is None:
        return state
    if progress.extracted_pages is None or progress.total_pages is None:
        return state
    return f"{state} {progress.extracted_pages}/{progress.total_pages} pages"


def _format_task_failure(exc: MinerUTaskFailedError) -> str:
    if exc.task_id is not None:
        return f"task {exc.task_id} failed: {exc.message}"
    if exc.batch_id is not None:
        return f"batch {exc.batch_id} failed: {exc.message}"
    return exc.message


def _wait_for_result(
    job: ExtractionJob,
    *,
    output_dir: Path | None,
    printer: _StatusPrinter,
) -> Path:
    seen_message: str | None = None

    def on_update(status: ExtractionStatus) -> None:
        nonlocal seen_message
        message = _progress_message(status)
        if message == seen_message:
            return
        printer.update(click.style(message, fg=_state_color(status.state)))
        seen_message = message

    def on_download_start() -> None:
        zip_url = job.last_status.full_zip_url
        if zip_url is None:
            raise RuntimeError("Extraction job missing result ZIP URL")
        printer.update(
            click.style("zip URL", fg=LABEL) + click.style(": ", fg=PUNCT) + zip_url
        )
        printer.update(click.style("downloading", fg=_state_color("running")))

    result = job.wait(
        output_dir=output_dir,
        on_update=on_update,
        on_download_start=on_download_start,
    )
    printer.complete(click.style("saved to ", fg=LABEL) + _tilde(result.output_dir))
    return result.output_dir


def render_task_table(page: TaskPage) -> str:
    headers: tuple[str, str, str, str] = ("FILENAME", "STATE", "CREATED", "TASK_ID")
    rows: list[tuple[str, str, str, str]] = [
        (
            task.file_name,
            task.state,
            _format_created_at(task.created_at),
            task.task_id,
        )
        for task in page.tasks
    ]
    column_count = len(headers)
    widths: list[int] = []
    for index in range(column_count):
        header_width = len(headers[index])
        row_width = max((len(row[index]) for row in rows), default=0)
        widths.append(max(header_width, row_width))

    def fmt_row(cells: Sequence[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    lines: list[str] = [
        click.style(
            fmt_row(headers),
            fg=HEADER_FOREGROUND,
            bg=HEADER_BACKGROUND,
            bold=True,
        )
    ]
    for row in rows:
        state_text = row[1].ljust(widths[1])
        state_color = _state_color(row[1])
        styled_state = click.style(state_text, fg=state_color)
        lines.append(
            "  ".join(
                (
                    row[0].ljust(widths[0]),
                    styled_state,
                    row[2].ljust(widths[2]),
                    row[3].ljust(widths[3]),
                )
            )
        )
    return "\n".join(lines)


@click.group(help="uminer command-line interface.", context_settings=CONTEXT_SETTINGS)
@click.option(
    "--env-file",
    type=click.Path(path_type=Path, dir_okay=False),
    default=DEFAULT_ENV_FILE,
    show_default=True,
    help=(
        "Path to an env file containing MINERU_API_KEY (typically a "
        "1Password-managed FIFO). Skipped silently if absent."
    ),
)
@click.option(
    "--api-key",
    type=str,
    default=None,
    help=("MinerU API key. Overrides --env-file and the shell environment when set."),
)
@click.option(
    "--base-url",
    type=str,
    default=DEFAULT_BASE_URL,
    show_default=True,
    help="MinerU API base URL.",
)
@click.pass_context
def main(
    ctx: click.Context,
    env_file: Path,
    api_key: str | None,
    base_url: str,
) -> None:
    """Entry point for the `uminer` console script."""
    resolved_key = _resolve_api_key(explicit=api_key, env_file=env_file)
    client = MinerUClient(api_key=resolved_key, base_url=base_url)
    ctx.obj = CLIContext(client=client)
    _ = ctx.call_on_close(client.close)


@main.command(
    "extract",
    help="Extract a document from a URL or local file path.",
    context_settings=CONTEXT_SETTINGS,
)
@click.argument("source", type=str)
@click.option(
    "-o",
    "--output-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help=(
        "Directory to download the result into. Defaults to the per-job cache "
        "under ~/.cache/uminer/results/."
    ),
)
@click.option(
    "--ocr/--no-ocr",
    "is_ocr",
    default=None,
    help="(URL only) Force OCR on or off. Unspecified leaves the API default.",
)
@click.option(
    "--formula/--no-formula",
    "enable_formula",
    default=None,
    help="Enable or disable formula extraction. Unspecified leaves the API default.",
)
@click.option(
    "--table/--no-table",
    "enable_table",
    default=None,
    help="Enable or disable table extraction. Unspecified leaves the API default.",
)
@click.option(
    "--language",
    type=str,
    default=None,
    help="Language hint forwarded to MinerU (e.g. 'en', 'zh').",
)
@click.option(
    "--page-ranges",
    type=str,
    default=None,
    help="(URL only) Page ranges to extract, e.g. '1-3,5'.",
)
@click.option(
    "--poll-interval",
    type=click.FloatRange(min=0.1),
    default=2.0,
    show_default=True,
    help="Seconds between status polls while waiting for the job.",
)
@click.pass_context
def extract_cmd(
    ctx: click.Context,
    source: str,
    output_dir: Path | None,
    is_ocr: bool | None,
    enable_formula: bool | None,
    enable_table: bool | None,
    language: str | None,
    page_ranges: str | None,
    poll_interval: float,
) -> None:
    cli = _get_ctx(ctx)
    extra_formats: Iterable[str] | None = None
    _ = extra_formats  # reserved for a later flag
    printer = _StatusPrinter()
    seen_upload_percentage: int | None = None

    def on_upload_progress(path: Path, uploaded_bytes: int, total_bytes: int) -> None:
        nonlocal seen_upload_percentage
        percentage = 100 if total_bytes == 0 else uploaded_bytes * 100 // total_bytes
        if percentage == seen_upload_percentage:
            return
        printer.update(
            click.style("uploading ", fg=LABEL)
            + _tilde(path)
            + click.style(" · ", fg=PUNCT)
            + str(percentage)
            + click.style("%", fg=PUNCT)
        )
        seen_upload_percentage = percentage

    try:
        if _is_url(source):
            job: ExtractionJob = cli.client.extract_url(
                source,
                is_ocr=is_ocr,
                enable_formula=enable_formula,
                enable_table=enable_table,
                language=language,
                page_ranges=page_ranges,
                poll_interval_seconds=poll_interval,
            )
        else:
            if is_ocr is not None:
                raise click.UsageError(
                    "--ocr/--no-ocr is only supported for URL extraction."
                )
            if page_ranges is not None:
                raise click.UsageError(
                    "--page-ranges is only supported for URL extraction."
                )
            file_path = Path(source).expanduser()
            if not file_path.exists():
                raise click.UsageError(f"File not found: {file_path}")
            printer.update(
                click.style("uploading ", fg=LABEL)
                + _tilde(file_path)
                + click.style(" · ", fg=PUNCT)
                + "0"
                + click.style("%", fg=PUNCT)
            )
            seen_upload_percentage = 0
            job = cli.client.extract_file(
                file_path,
                enable_formula=enable_formula,
                enable_table=enable_table,
                language=language,
                on_upload_progress=on_upload_progress,
                poll_interval_seconds=poll_interval,
            )
        printer.update(
            click.style("submitted ", fg=LABEL)
            + _styled_job_reference(job)
            + click.style(" · ", fg=PUNCT)
            + _source_label(job)
        )
        result_dir = _wait_for_result(job, output_dir=output_dir, printer=printer)
    except MinerUTaskFailedError as exc:
        printer.finish()
        raise click.ClickException(_format_task_failure(exc)) from exc
    except MinerUError as exc:
        printer.finish()
        raise click.ClickException(str(exc)) from exc
    click.echo(str(result_dir))


@main.command(
    "list",
    help="List recent extraction tasks.",
    context_settings=CONTEXT_SETTINGS,
)
@click.option(
    "--page",
    "page_no",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help="Page number (1-indexed).",
)
@click.option(
    "--page-size",
    type=click.IntRange(min=1, max=200),
    default=20,
    show_default=True,
    help="Number of tasks per page.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the raw response as JSON instead of a human-readable table.",
)
@click.pass_context
def list_cmd(
    ctx: click.Context,
    page_no: int,
    page_size: int,
    as_json: bool,
) -> None:
    cli = _get_ctx(ctx)
    try:
        page = cli.client.list_tasks(page_no=page_no, page_size=page_size)
    except MinerUError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(page.model_dump(mode="json"), indent=2))
        return
    click.echo(render_task_table(page))
    click.echo(
        click.style(
            f"page {page_no} · showing {len(page.tasks)} of {page.total}", dim=True
        )
    )


if __name__ == "__main__":
    main()
