"""Work out what arrived and get it into a form the extractor can read."""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def describe(path: Path) -> dict:
    """Classify the delivered file.

    The born-digital flag is recorded because it is the strongest single
    predictor of extraction accuracy and the evaluation slices on it. It is not
    used to choose an extraction path: everything is read as pixels, so there is
    one code path to explain and one failure mode to reason about. Using the
    text layer where it exists is an obvious optimisation and is deliberately
    not built here.
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


def rasterize(path: Path, out_path: Path, dpi: int = 190) -> Path:
    """Render page 1 of a PDF to PNG. Images are passed through unchanged."""
    if path.suffix.lower() != ".pdf":
        return path
    doc = fitz.open(path)
    try:
        doc[0].get_pixmap(dpi=dpi).save(out_path)
    finally:
        doc.close()
    return out_path
