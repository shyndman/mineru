from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypeAlias

DEFAULT_BASE_URL = "https://mineru.net"
DEFAULT_API_KEY_ENV = "MINERU_API_KEY"

MODEL_VERSION: Literal["vlm"] = "vlm"
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
Json: TypeAlias = object
FileSpec: TypeAlias = Mapping[str, object]
ExtractionSourceKind = Literal["url", "file"]
