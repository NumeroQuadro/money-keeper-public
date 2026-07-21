from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from fastapi import UploadFile

from app.services.importer import save_upload


class SaveUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_upload(self, *, filename: str, payload: bytes) -> UploadFile:
        return UploadFile(filename=filename, file=io.BytesIO(payload))

    def test_save_upload_sanitizes_filename_and_hashes_content(self) -> None:
        payload = b"%PDF-1.7 fake"
        upload = self._make_upload(
            filename="../nested/../../evil file?.pdf",
            payload=payload,
        )

        file_path, file_hash = save_upload(upload, self._tmpdir.name)
        resolved_dir = Path(self._tmpdir.name).resolve()
        resolved_path = Path(file_path).resolve()

        self.assertTrue(resolved_path.exists())
        self.assertEqual(resolved_path.parent, resolved_dir)
        self.assertEqual(resolved_path.suffix.lower(), ".pdf")
        self.assertNotIn("..", resolved_path.name)
        self.assertNotIn(" ", resolved_path.name)
        self.assertEqual(file_hash, hashlib.sha256(payload).hexdigest())

    def test_save_upload_generates_unique_path_for_same_filename(self) -> None:
        payload = b"%PDF-1.7 fake duplicate"
        upload_one = self._make_upload(filename="statement.pdf", payload=payload)
        upload_two = self._make_upload(filename="statement.pdf", payload=payload)

        path_one, hash_one = save_upload(upload_one, self._tmpdir.name)
        path_two, hash_two = save_upload(upload_two, self._tmpdir.name)

        self.assertNotEqual(path_one, path_two)
        self.assertTrue(Path(path_one).exists())
        self.assertTrue(Path(path_two).exists())
        self.assertEqual(hash_one, hash_two)


if __name__ == "__main__":
    unittest.main()
