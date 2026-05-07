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
TINY_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT /F1 12 Tf 40 120 Td (MinerU smoke test) Tj ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000232 00000 n 
0000000326 00000 n 
trailer
<< /Size 6 /Root 1 0 R >>
startxref
396
%%EOF
"""


@unittest.skipUnless(
    LIVE_API_ENABLED and LIVE_API_KEY_PRESENT,
    "set RUN_MINERU_LIVE_API=1 and MINERU_API_KEY to call the live MinerU API",
)
class MinerULiveApiSmokeTests(unittest.TestCase):
    def test_upload_tiny_pdf_and_read_live_batch_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "mineru-smoke.pdf"
            _ = pdf_path.write_bytes(TINY_PDF)

            with MinerUClient(timeout=30.0) as client:
                batch = client.create_file_upload_extract_tasks(
                    [pdf_path],
                    files=[{"name": pdf_path.name, "data_id": "mineru-python-smoke-test"}],
                    model_version="pipeline",
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
                    {"waiting-file", "pending", "running", "converting", "done", "failed"},
                )


if __name__ == "__main__":
    _ = unittest.main()
