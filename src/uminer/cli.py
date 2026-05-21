import json
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import click

from .api import MinerUClient
from .errors import MinerUError
from .job import ExtractionJob
from .models import TaskPage
from .types import DEFAULT_API_KEY_ENV, DEFAULT_BASE_URL

DEFAULT_ENV_FILE: Path = Path.home() / ".config" / "uminer" / "uminer.env"


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


def render_task_table(page: TaskPage) -> str:
    headers: tuple[str, str, str, str] = ("TASK_ID", "STATE", "CREATED", "FILE_NAME")
    rows: list[tuple[str, str, str, str]] = [
        (
            task.task_id,
            task.state,
            _format_created_at(task.created_at),
            task.file_name,
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

    lines: list[str] = [fmt_row(headers)]
    lines.extend(fmt_row(row) for row in rows)
    return "\n".join(lines)


@click.group(help="uminer command-line interface.")
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


@main.command("extract", help="Extract a document from a URL or local file path.")
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
            job = cli.client.extract_file(
                file_path,
                enable_formula=enable_formula,
                enable_table=enable_table,
                language=language,
                poll_interval_seconds=poll_interval,
            )
        result = job.wait(output_dir=output_dir)
    except MinerUError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(str(result.output_dir))


@main.command("list", help="List recent extraction tasks.")
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
    click.echo(f"page {page_no} · showing {len(page.tasks)} of {page.total}")


if __name__ == "__main__":
    main()
