uminer: MinerU Python API client.

VLM-only wrapper around MinerU's v4 extraction API. It supports URL extraction, local file upload extraction, polling, awaiting final results, and parsing the returned zip into Markdown plus typed Pydantic objects.

## Install

```sh
uv add git+https://github.com/shyndman/mineru.git
```

Set `MINERU_API_KEY`, or pass `api_key=` to `MinerUClient`.

## Happy path

```python
import asyncio
from pathlib import Path

from uminer import MinerUClient


async def main() -> None:
    with MinerUClient() as client:
        job = client.extract_file(Path("document.pdf"))

        print(job.source.path)
        print(job.last_status.state)

        status = job.refresh()
        print(status.state, status.extract_progress)

        result = await job
        print(result.output_dir)
        print(result.zip_path)
        print(result.markdown)
        for page in result.content_list.pages:
            for block in page.blocks:
                print(page.index, block.type, block.bbox)
        print(result.raw_output)
        print(result.layout)


asyncio.run(main())
```

For a URL:

```python
with MinerUClient() as client:
    job = client.extract_url("https://example.com/document.pdf")
    result = job.wait(output_dir=Path("./tmp/result"))
```

`ExtractionJob.refresh()` updates `last_status`. `await job` and `job.wait()` poll until MinerU returns `done`, then download the result zip to `~/.cache/uminer/results/...` unless `output_dir` is supplied. Extracted files stay on disk; Markdown, raw output, and layout are loaded lazily from those files.
