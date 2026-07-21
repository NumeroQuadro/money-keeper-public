from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
from pathlib import Path

from fastapi import BackgroundTasks, UploadFile
from sqlalchemy.orm import sessionmaker

from app.api.imports import upload_pdfs
from app.config import settings
from app.db import Base
from app.models import ImportFile
from app.tests.db_test_utils import get_test_engine


class ImportApiUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = get_test_engine()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self._Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

        self._tmpdir = tempfile.TemporaryDirectory()
        self._previous_uploads_dir = settings.uploads_dir
        settings.uploads_dir = self._tmpdir.name

    def tearDown(self) -> None:
        settings.uploads_dir = self._previous_uploads_dir
        self._tmpdir.cleanup()

    def _make_pdf_upload(self, name: str, payload: bytes) -> UploadFile:
        return UploadFile(filename=name, file=io.BytesIO(payload))

    def test_upload_pdfs_marks_same_request_duplicates_and_removes_temp_file(self) -> None:
        payload = b"%PDF-1.7 same-content"

        with self._Session() as db:
            batch = asyncio.run(
                upload_pdfs(
                    background=BackgroundTasks(),
                    source="test",
                    files=[
                        self._make_pdf_upload("statement-a.pdf", payload),
                        self._make_pdf_upload("statement-b.pdf", payload),
                    ],
                    db=db,
                )
            )

            self.assertEqual(batch.summary["files_received"], 2)
            self.assertEqual(batch.summary["files_queued"], 1)
            self.assertEqual(batch.summary["duplicates"], 1)
            self.assertEqual(batch.status, "queued")

            rows = db.query(ImportFile).order_by(ImportFile.created_at.asc()).all()
            self.assertEqual(len(rows), 2)

            queued = [row for row in rows if row.status == "queued"]
            duplicates = [row for row in rows if row.status == "duplicate"]
            self.assertEqual(len(queued), 1)
            self.assertEqual(len(duplicates), 1)
            self.assertEqual(duplicates[0].file_path, "")
            self.assertTrue(Path(queued[0].file_path).exists())

            files_on_disk = list(Path(settings.uploads_dir).glob("*.pdf"))
            self.assertEqual(len(files_on_disk), 1)

    def test_upload_pdfs_marks_cross_batch_duplicate_with_empty_duplicate_path(self) -> None:
        payload = b"%PDF-1.7 existing-content"

        with self._Session() as db:
            first_batch = asyncio.run(
                upload_pdfs(
                    background=BackgroundTasks(),
                    source="test",
                    files=[self._make_pdf_upload("statement.pdf", payload)],
                    db=db,
                )
            )
            second_batch = asyncio.run(
                upload_pdfs(
                    background=BackgroundTasks(),
                    source="test",
                    files=[self._make_pdf_upload("statement-again.pdf", payload)],
                    db=db,
                )
            )

            self.assertEqual(first_batch.summary["files_queued"], 1)
            self.assertEqual(second_batch.summary["duplicates"], 1)
            self.assertEqual(second_batch.summary["files_queued"], 0)
            self.assertEqual(second_batch.status, "failed")

            duplicate = db.query(ImportFile).filter(ImportFile.batch_id == second_batch.id).one()
            self.assertEqual(duplicate.status, "duplicate")
            self.assertEqual(duplicate.file_path, "")

            files_on_disk = list(Path(settings.uploads_dir).glob("*.pdf"))
            self.assertEqual(len(files_on_disk), 1)


if __name__ == "__main__":
    unittest.main()
