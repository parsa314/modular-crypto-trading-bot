import hashlib
import io
import unittest
import zipfile

from scripts.collect_binance_archive_2022_2023 import collect_month


class BinanceArchiveTest(unittest.TestCase):
    def test_checksum_and_market_month(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("BTCUSDT-4h-2022-01.csv",
                       "1640995200000,100,101,99,100,2,1641009599999,200,1,1,100,0\n")
        raw = out.getvalue()
        def getter(url):
            return (hashlib.sha256(raw).hexdigest() + "  BTCUSDT-4h-2022-01.zip\n").encode() \
                if url.endswith("CHECKSUM") else raw
        frame, meta = collect_month("BTCUSDT", "2022-01", getter)
        self.assertEqual(meta["archive_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(frame.timestamp.iloc[0].isoformat(), "2022-01-01T00:00:00+00:00")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            collect_month("BTCUSDT", "2022-01",
                          lambda url: b"0" * 64 if url.endswith("CHECKSUM") else raw)


if __name__ == "__main__":
    unittest.main()
