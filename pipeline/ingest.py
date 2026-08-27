"""Work out what arrived and get it into a form the extractor can read."""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def describe(path: Path) -> dict:
    """Classify the delivered file.

    The born-digital flag is recorded because it is the strongest single
    predictor of extraction accuracy and the evaluation slices on it. It does
    not route anything: the file is handed to the reader exactly as it arrived,
    and the reader decides how to open it.

    That last point is worth being precise about, because it is easy to assume
    otherwise. Nothing here rasterises a PDF or strips its text layer first. So
    for a born-digital PDF the reader may well be using the text layer rather
    than looking at the page, and this pipeline neither controls that nor can
    observe which happened -- which is exactly why cost and latency per document
    are not comparable across containers.
    """
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return {"container": suffix.lstrip("."), "pages": 1, "has_text_layer": False}
    if suffix == ".pdf":
        doc = fitz.open(path)
        try:
            chars = sum(len(page.get_text().strip()) for page in doc)
            return {
                "container": "pdf",
                "pages": doc.page_count,
                # A scanned PDF carries an image and no meaningful text.
                "has_text_layer": chars > 200,
            }
        finally:
            doc.close()
    return {"container": suffix.lstrip(".") or "unknown", "pages": 1, "has_text_layer": False}
