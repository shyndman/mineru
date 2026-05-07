from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from dotenv import load_dotenv

from mineru import MinerUClient

_ = load_dotenv(".testing.env", override=True)

LIVE_API_ENABLED = os.getenv("RUN_MINERU_LIVE_API") == "1"
LIVE_API_KEY_PRESENT = bool(os.getenv("MINERU_API_KEY"))


def tiny_pdf() -> bytes:
    stream = b"BT /F1 12 Tf 40 120 Td (MinerU smoke test) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b" ".join(
            [
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200]",
                b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            ]
        ),
        b"\n".join(
            [
                f"<< /Length {len(stream)} >>".encode(),
                b"stream",
                stream,
                b"endstream",
            ]
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    trailer = (
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    )
    output.extend(trailer.encode())
    return bytes(output)


@unittest.skipUnless(
    LIVE_API_ENABLED and LIVE_API_KEY_PRESENT,
    "set RUN_MINERU_LIVE_API=1 and MINERU_API_KEY to call the live MinerU API",
)
class MinerULiveApiSmokeTests(unittest.TestCase):
    def test_upload_tiny_pdf_and_read_live_batch_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "mineru-smoke.pdf"
            _ = pdf_path.write_bytes(tiny_pdf())

            with MinerUClient(timeout=30.0) as client:
                batch = client.create_file_upload_extract_tasks(
                    [pdf_path],
                    files=[
                        {"name": pdf_path.name, "data_id": "mineru-python-smoke-test"}
                    ],
                    enable_formula=False,
                    enable_table=False,
                )
                self.assertTrue(batch.batch_id)
                self.assertEqual(len(batch.file_urls), 1)

                result = client.get_batch_extract_result(batch.batch_id)
                self.assertEqual(result.batch_id, batch.batch_id)
                self.assertEqual(len(result.results), 1)
                self.assertEqual(result.results[0].file_name, pdf_path.name)
                self.assertIn(
                    result.results[0].state,
                    {
                        "waiting-file",
                        "pending",
                        "running",
                        "converting",
                        "done",
                        "failed",
                    },
                )


if __name__ == "__main__":
    _ = unittest.main()
