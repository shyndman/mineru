from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
import zipfile
from collections.abc import Callable, Mapping
from io import BytesIO
from pathlib import Path

import httpx
from dotenv import load_dotenv

from mineru import (
    ExtractionJob,
    MinerUApiError,
    MinerUClient,
    MinerUConfigError,
    MinerUParsedResult,
    MinerUTaskFailedError,
    ParagraphBlock,
)

_ = load_dotenv(".testing.env", override=True)


def mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(
        base_url="https://mineru.net", transport=httpx.MockTransport(handler)
    )


class MinerUClientTests(unittest.TestCase):
    def test_requires_api_key(self) -> None:
        old_value = os.environ.pop("MINERU_API_KEY", None)
        try:
            with self.assertRaises(MinerUConfigError):
                _ = MinerUClient()
        finally:
            if old_value is not None:
                os.environ["MINERU_API_KEY"] = old_value

    def test_uses_api_key_from_environment(self) -> None:
        os.environ["MINERU_API_KEY"] = "env-token"
        client = MinerUClient(client=mock_client(self._ok_response))
        self.assertEqual(client.api_key, "env-token")

    def test_create_extract_task_posts_expected_body(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/api/v4/extract/task")
            self.assertEqual(request.headers["authorization"], "Bearer token")
            self.assertEqual(
                json.loads(request.content),
                {
                    "url": "https://example.com/demo.pdf",
                    "model_version": "vlm",
                    "enable_table": True,
                    "extra_formats": ["docx", "html"],
                },
            )
            return self._json_response({"task_id": "task-1"})

        client = MinerUClient(api_key="token", client=mock_client(handler))
        task = client.create_extract_task(
            "https://example.com/demo.pdf",
            enable_table=True,
            extra_formats=["docx", "html"],
        )

        self.assertEqual(task.task_id, "task-1")
        self.assertEqual(len(requests), 1)

    def test_get_extract_task_maps_result(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/v4/extract/task/task-1")
            return self._json_response(
                {
                    "task_id": "task-1",
                    "state": "running",
                    "err_msg": "",
                    "extract_progress": {
                        "extracted_pages": 1,
                        "total_pages": 2,
                        "start_time": "2025-01-20 11:43:20",
                    },
                }
            )

        client = MinerUClient(api_key="token", client=mock_client(handler))
        task = client.get_extract_task("task-1")

        self.assertEqual(task.state, "running")
        progress = task.extract_progress
        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertEqual(progress.extracted_pages, 1)

    def test_create_upload_batch_and_upload_files(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "POST":
                self.assertEqual(request.url.path, "/api/v4/file-urls/batch")
                self.assertEqual(
                    json.loads(request.content),
                    {
                        "files": [{"name": "demo.pdf", "data_id": "doc-1"}],
                        "model_version": "vlm",
                    },
                )
                return self._json_response(
                    {
                        "batch_id": "batch-1",
                        "file_urls": ["https://uploads.example/demo.pdf"],
                    }
                )
            self.assertEqual(request.method, "PUT")
            self.assertEqual(str(request.url), "https://uploads.example/demo.pdf")
            self.assertEqual(request.content, b"pdf bytes")
            return httpx.Response(200)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "demo.pdf"
            _ = path.write_bytes(b"pdf bytes")
            client = MinerUClient(api_key="token", client=mock_client(handler))
            batch = client.create_file_upload_extract_tasks(
                [path],
                files=[{"name": "demo.pdf", "data_id": "doc-1"}],
            )

        self.assertEqual(batch.batch_id, "batch-1")
        self.assertEqual(batch.file_urls, ("https://uploads.example/demo.pdf",))
        self.assertEqual([request.method for request in requests], ["POST", "PUT"])

    def test_create_url_batch_returns_batch_id(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/api/v4/extract/task/batch")
            self.assertEqual(
                json.loads(request.content),
                {
                    "files": [
                        {"url": "https://example.com/demo.pdf", "data_id": "doc-1"}
                    ],
                    "model_version": "vlm",
                    "no_cache": True,
                },
            )
            return self._json_response({"batch_id": "batch-1"})

        client = MinerUClient(api_key="token", client=mock_client(handler))
        batch_id = client.create_url_batch(
            [{"url": "https://example.com/demo.pdf", "data_id": "doc-1"}],
            no_cache=True,
        )

        self.assertEqual(batch_id, "batch-1")

    def test_get_batch_extract_result_maps_results(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/v4/extract-results/batch/batch-1")
            return self._json_response(
                {
                    "batch_id": "batch-1",
                    "extract_result": [
                        {
                            "file_name": "demo.pdf",
                            "state": "done",
                            "full_zip_url": "https://cdn.example/demo.zip",
                            "err_msg": "",
                        }
                    ],
                }
            )

        client = MinerUClient(api_key="token", client=mock_client(handler))
        result = client.get_batch_extract_result("batch-1")

        self.assertEqual(result.batch_id, "batch-1")
        self.assertEqual(result.results[0].file_name, "demo.pdf")
        self.assertEqual(result.results[0].full_zip_url, "https://cdn.example/demo.zip")

    def test_api_error_raises_with_code_and_trace_id(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"code": "A0202", "msg": "Invalid Token", "trace_id": "trace-1"},
            )

        client = MinerUClient(api_key="token", client=mock_client(handler))

        with self.assertRaises(MinerUApiError) as raised:
            _ = client.get_extract_task("task-1")

        self.assertEqual(raised.exception.code, "A0202")
        self.assertEqual(raised.exception.trace_id, "trace-1")

    def test_download_result_parses_zip_outputs(self) -> None:
        zip_bytes = self._result_zip_bytes()

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), "https://cdn.example/result.zip")
            return httpx.Response(200, content=zip_bytes)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "result"
            client = MinerUClient(api_key="token", client=mock_client(handler))
            result = client.download_result(
                "https://cdn.example/result.zip", output_dir=output_dir
            )

            self.assertEqual(result.output_dir, output_dir)
            self.assertEqual(result.zip_path, output_dir / "result.zip")
            self.assertEqual(result.markdown, "# Smoke\n")
            self.assertEqual(len(result.content_list.pages), 1)
            block = result.content_list.pages[0].blocks[0]
            self.assertIsInstance(block, ParagraphBlock)
            assert isinstance(block, ParagraphBlock)
            self.assertEqual(block.content.paragraph_content[0].content, "Smoke")
            self.assertEqual(
                result.raw_output, [[{"type": "text", "content": "Smoke"}]]
            )
            self.assertEqual(result.layout, {"_backend": "vlm", "pdf_info": []})
            full_md = next(file for file in result.files if file.path == "full.md")
            self.assertTrue(full_md.local_path.exists())

    def test_extract_url_job_reports_status_and_waits_for_result(self) -> None:
        requests: list[str] = []
        zip_bytes = self._result_zip_bytes()

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(f"{request.method} {request.url}")
            if request.method == "POST":
                return self._json_response({"task_id": "task-1"})
            if str(request.url).endswith("/api/v4/extract/task/task-1"):
                return self._json_response(
                    {
                        "task_id": "task-1",
                        "state": "done",
                        "full_zip_url": "https://cdn.example/result.zip",
                        "err_msg": "",
                    }
                )
            return httpx.Response(200, content=zip_bytes)

        client = MinerUClient(api_key="token", client=mock_client(handler))
        job = client.extract_url(
            "https://example.com/demo.pdf", poll_interval_seconds=0
        )

        self.assertEqual(job.source.kind, "url")
        self.assertEqual(job.source.url, "https://example.com/demo.pdf")
        self.assertEqual(job.last_status.task_id, "task-1")
        self.assertIsNone(job.last_status.state)
        status = job()
        result = job.wait()
        awaited = asyncio.run(self._await_job(job))

        self.assertEqual(status.state, "done")
        self.assertEqual(job.last_status.state, "done")
        self.assertEqual(job.last_status.full_zip_url, "https://cdn.example/result.zip")
        self.assertEqual(result.markdown, "# Smoke\n")
        self.assertEqual(awaited.markdown, "# Smoke\n")
        self.assertEqual(requests[0], "POST https://mineru.net/api/v4/extract/task")

    def test_extract_file_job_reports_batch_status(self) -> None:
        zip_bytes = self._result_zip_bytes()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return self._json_response(
                    {
                        "batch_id": "batch-1",
                        "file_urls": ["https://uploads.example/demo.pdf"],
                    }
                )
            if request.method == "PUT":
                return httpx.Response(200)
            if str(request.url).endswith("/api/v4/extract-results/batch/batch-1"):
                return self._json_response(
                    {
                        "batch_id": "batch-1",
                        "extract_result": [
                            {
                                "file_name": "demo.pdf",
                                "state": "done",
                                "full_zip_url": "https://cdn.example/result.zip",
                                "err_msg": "",
                            }
                        ],
                    }
                )
            return httpx.Response(200, content=zip_bytes)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "demo.pdf"
            _ = path.write_bytes(b"pdf bytes")
            client = MinerUClient(api_key="token", client=mock_client(handler))
            job = client.extract_file(path, poll_interval_seconds=0)

        self.assertEqual(job.source.kind, "file")
        self.assertEqual(job.source.path, path)
        self.assertEqual(job.source.file, {"name": "demo.pdf"})
        self.assertEqual(job.last_status.batch_id, "batch-1")
        self.assertEqual(job.last_status.file_name, "demo.pdf")
        self.assertEqual(job().state, "done")
        self.assertEqual(job.last_status.state, "done")
        self.assertEqual(job.wait().markdown, "# Smoke\n")

    def test_wait_raises_for_failed_task(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return self._json_response({"task_id": "task-1"})
            return self._json_response(
                {"task_id": "task-1", "state": "failed", "err_msg": "Unsupported file"}
            )

        client = MinerUClient(api_key="token", client=mock_client(handler))
        job = client.extract_url(
            "https://example.com/demo.pdf", poll_interval_seconds=0
        )

        with self.assertRaises(MinerUTaskFailedError) as raised:
            _ = job.wait()

        self.assertEqual(raised.exception.task_id, "task-1")

    @staticmethod
    def _result_zip_bytes() -> bytes:
        output = BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("full.md", "# Smoke\n")
            archive.writestr(
                "content_list_v2.json",
                json.dumps(
                    [
                        [
                            {
                                "type": "paragraph",
                                "content": {
                                    "paragraph_content": [
                                        {"type": "text", "content": "Smoke"}
                                    ]
                                },
                            }
                        ]
                    ]
                ),
            )
            archive.writestr(
                "demo_model.json", json.dumps([[{"type": "text", "content": "Smoke"}]])
            )
            archive.writestr(
                "layout.json", json.dumps({"_backend": "vlm", "pdf_info": []})
            )
        return output.getvalue()

    @staticmethod
    async def _await_job(job: ExtractionJob) -> MinerUParsedResult:
        return await job

    @staticmethod
    def _json_response(data: Mapping[str, object]) -> httpx.Response:
        return httpx.Response(
            200, json={"code": 0, "msg": "ok", "trace_id": "trace-1", "data": data}
        )

    @staticmethod
    def _ok_response(_request: httpx.Request) -> httpx.Response:
        return MinerUClientTests._json_response({"task_id": "task-1"})


if __name__ == "__main__":
    _ = unittest.main()
