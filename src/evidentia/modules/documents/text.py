"""Page-level text extraction.

Extraction is one replaceable step with a fixed output (page number -> text), so a
vision OCR engine for image-only scans can plug in without touching downstream code.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Protocol

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from evidentia.ai.structured import sanitize_text

_INLINE_SPACE = re.compile(r"[ \t\u00a0\u2000-\u200b\u3000]+")
_BLANK_LINES = re.compile(r"\n{3,}")


class UnreadableDocumentError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PageText:
    number: int
    text: str

    @property
    def is_blank(self) -> bool:
        return not self.text.strip()


class TextExtractor(Protocol):
    def extract(self, data: bytes) -> list[PageText]: ...


def normalize_page_text(raw: str) -> str:
    text = sanitize_text(raw.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [_INLINE_SPACE.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def is_scanned(pages: list[PageText]) -> bool:
    """No page yields any text: an image-only scan that needs OCR."""
    return all(page.is_blank for page in pages)


class PdfTextExtractor:
    def extract(self, data: bytes) -> list[PageText]:
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise UnreadableDocumentError("The PDF is password protected.")
            return [
                PageText(number=index, text=normalize_page_text(page.extract_text() or ""))
                for index, page in enumerate(reader.pages, start=1)
            ]
        except PdfReadError as exc:
            raise UnreadableDocumentError(f"The PDF could not be parsed: {exc}") from exc
