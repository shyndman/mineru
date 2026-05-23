from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

DEFAULT_BASE_URL = "https://mineru.net"
DEFAULT_API_KEY_ENV = "MINERU_API_KEY"
MODEL_VERSION: Literal["vlm"] = "vlm"

type Json = object
type FileSpec = Mapping[str, object]

ExtractionSourceKind = Literal["url", "file"]
ExtraFormat = Literal["docx", "html", "latex"]
TaskState = Literal["done", "pending", "running", "failed", "converting"]
TaskListState = Literal[
    "waiting-file",
    "uploading",
    "pending",
    "running",
    "failed",
    "converting",
    "done",
]
BatchTaskState = Literal[
    "done",
    "waiting-file",
    "pending",
    "running",
    "failed",
    "converting",
]
