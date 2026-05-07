from __future__ import annotations

import json
import zipfile
from functools import cached_property
from io import BytesIO
from typing import ClassVar, cast

from pydantic import BaseModel, ConfigDict

from .content import ContentList
from .errors import MinerUResultError
from .types import Json


class MinerUZipFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    path: str
    data: bytes


class MinerUParsedResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    markdown: str | None
    content_list: ContentList
    files: tuple[MinerUZipFile, ...]

    @cached_property
    def raw_output(self) -> Json:
        return _read_json_suffix(self.files, "_model.json")

    @cached_property
    def layout(self) -> Json:
        return _read_json_named_or_suffix(self.files, "layout.json", "_middle.json")

    @classmethod
    def from_zip_bytes(cls, data: bytes) -> MinerUParsedResult:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            files = tuple(
                MinerUZipFile(path=name, data=archive.read(name))
                for name in archive.namelist()
                if not name.endswith("/")
            )
        # MinerU also emits legacy content_list.json; this client ignores it.
        content_list = _read_json_named_or_suffix(files, "content_list_v2.json", "_content_list_v2.json")
        return cls(
            markdown=_read_text_file(files, "full.md"),
            content_list=ContentList.from_mineru(content_list),
            files=files,
        )


def _read_text_file(files: tuple[MinerUZipFile, ...], path: str) -> str | None:
    for file in files:
        if file.path == path or file.path.endswith(f"/{path}"):
            return file.data.decode("utf-8")
    return None


def _read_json_suffix(files: tuple[MinerUZipFile, ...], suffix: str) -> Json:
    matches = [file for file in files if file.path.endswith(suffix)]
    if not matches:
        return None
    if len(matches) > 1:
        raise MinerUResultError(f"Expected one {suffix} file, found {len(matches)}")
    return cast(Json, json.loads(matches[0].data.decode("utf-8")))


def _read_json_named_or_suffix(files: tuple[MinerUZipFile, ...], name: str, suffix: str) -> Json:
    matches = [file for file in files if file.path == name or file.path.endswith(f"/{name}")]
    if matches:
        if len(matches) > 1:
            raise MinerUResultError(f"Expected one {name} file, found {len(matches)}")
        return cast(Json, json.loads(matches[0].data.decode("utf-8")))
    return _read_json_suffix(files, suffix)
