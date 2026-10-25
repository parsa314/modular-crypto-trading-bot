import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError

from research_bot.iran_fundamental.acquisition import capture_source, validate_url, MAX_BYTES


class AcquisitionTests(unittest.TestCase):
    def test_exact_bytes_persistent_replay_and_conflict(self):
        body = b"\xef\xbb\xbfraw\r\n"
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "evidence.db")
            first = capture_source(db, "one", "https://www.codal.ir/test",
                                   lambda url: (body, "text/plain"))
            def forbidden(url):
                raise AssertionError("replay must not fetch")
            self.assertEqual(first, capture_source(db, "one", first["url"], forbidden))
            self.assertEqual(first["sha256"], hashlib.sha256(body).hexdigest())
            self.assertEqual(first["financial_verification"], "UNVERIFIED")
            self.assertIsNone(first["publication_at"])
            with sqlite3.connect(db) as connection:
                self.assertEqual(connection.execute("SELECT raw_body FROM iran_source_captures").fetchone()[0], body)
            with self.assertRaisesRegex(ValueError, "CONFLICT"):
                capture_source(db, "one", "https://www.codal.ir/other", forbidden)

    def test_reject_unsafe_urls(self):
        for url in ["http://codal.ir/x", "https://codal.ir.evil.test/x",
                    "https://u:p@codal.ir/x", "https://codal.ir:8080/x",
                    "https://127.0.0.1/x", "https://codal.ir/x#fragment"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)

    def test_transport_failure_is_persisted_block_not_rejection(self):
        def failed(url):
            raise URLError("unavailable")
        with tempfile.TemporaryDirectory() as directory:
            receipt = capture_source(str(Path(directory)/"db"), "blocked",
                                     "https://cdn.tsetmc.com/api/test", failed)
            self.assertEqual(receipt["status"], "BLOCKED")
            self.assertEqual(receipt["failure_code"], "TRANSPORT_UNAVAILABLE")
            self.assertIsNone(receipt["sha256"])

    def test_empty_and_oversized_responses_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            for index, body in enumerate([b"", b"x"*(MAX_BYTES+1)]):
                receipt = capture_source(str(Path(directory)/"db"), str(index),
                                         "https://codal.ir/x", lambda url: (body,"text/plain"))
                self.assertEqual(receipt["failure_code"], "INVALID_SOURCE_RESPONSE")
                self.assertIsNone(receipt["retrieved_at"])


if __name__ == "__main__":
    unittest.main()
