from pathlib import Path
import tempfile
import unittest
from unittest import mock

import server


class PreviewServerTest(unittest.TestCase):
    def test_browser_origin_must_match_loopback_preview(self):
        self.assertTrue(server.trusted_browser_origin(
            "http://127.0.0.1:8895", "http", "127.0.0.1:8895"
        ))
        self.assertTrue(server.trusted_browser_origin(
            "http://localhost:8895", "http", "localhost:8895"
        ))
        self.assertFalse(server.trusted_browser_origin(
            "https://attacker.example", "http", "127.0.0.1:8895"
        ))
        self.assertFalse(server.trusted_browser_origin(
            "https://attacker.example", "https", "attacker.example"
        ))
        self.assertFalse(server.trusted_browser_origin(None, "http", "127.0.0.1:8895"))

    def test_missing_optional_asset_does_not_block_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            required = root / "index.html"
            required.write_text("ready")
            with (
                mock.patch.object(server, "REQUIRED_STATIC_FILES", {"/": required}),
                mock.patch.object(server, "OPTIONAL_STATIC_FILES", {
                    "/optional.glb": root / "missing.glb"
                }),
            ):
                self.assertEqual(server.available_static_files(), {"/": required})


if __name__ == "__main__":
    unittest.main()
