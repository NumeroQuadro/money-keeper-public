from __future__ import annotations

from dataclasses import dataclass
from typing import List

from pypdf import PdfReader


@dataclass(frozen=True)
class PdfText:
    pages: List[str]

    @property
    def full_text(self) -> str:
        return "\n".join(self.pages)


def extract_pdf_text(pdf_path: str) -> PdfText:
    reader = PdfReader(pdf_path)
    pages: List[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
    return PdfText(pages=pages)
