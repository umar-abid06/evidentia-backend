"""Reading chunks: consecutive whole pages, packed up to a character budget.

A page is never split, so tables stay intact, and every page carries a
``[PAGE n]`` marker so each extracted fact can cite where it came from.
"""

from __future__ import annotations

from dataclasses import dataclass

from evidentia.modules.documents.text import PageText

DEFAULT_CHUNK_CHARS = 18_000


@dataclass(frozen=True, slots=True)
class ReadingChunk:
    index: int
    pages: tuple[int, ...]
    text: str

    @property
    def first_page(self) -> int:
        return self.pages[0]

    @property
    def last_page(self) -> int:
        return self.pages[-1]


def _render(page: PageText) -> str:
    return f"[PAGE {page.number}]\n{page.text}"


def build_reading_chunks(
    pages: list[PageText], *, max_chars: int = DEFAULT_CHUNK_CHARS
) -> list[ReadingChunk]:
    chunks: list[ReadingChunk] = []
    current: list[PageText] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            text = "\n\n".join(_render(page) for page in current)
            chunks.append(ReadingChunk(len(chunks), tuple(page.number for page in current), text))
        current, size = [], 0

    for page in pages:
        if page.is_blank:
            continue
        rendered = len(_render(page)) + 2
        if current and size + rendered > max_chars:
            flush()
        current.append(page)
        size += rendered
    flush()
    return chunks
