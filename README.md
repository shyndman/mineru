# uminer

[![PyPI version](https://img.shields.io/pypi/v/uminer.svg)](https://pypi.org/project/uminer/)
[![CI](https://github.com/shyndman/uminer/actions/workflows/ci.yml/badge.svg)](https://github.com/shyndman/uminer/actions/workflows/ci.yml)

Thin Python wrapper for MinerU's v4 extraction API.
Submit a local file or URL, poll the job, then load Markdown and typed content objects from the returned result bundle.

## Install

```sh
uv add uminer
```

## CLI

```sh
export MINERU_API_KEY=...
uv run uminer extract ./document.pdf --output-dir ./out
```

## Python

```python
from pathlib import Path

from uminer import MinerUClient

with MinerUClient() as client:
    result = client.extract_file(Path("document.pdf")).wait()
    print(result.markdown)
```
