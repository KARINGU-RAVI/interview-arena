"""Turn a resume PDF into plain text (no LLM)."""

from __future__ import annotations

import io
import re
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

import config


class ScannedPdfError(ValueError):
    """The PDF has (almost) no text layer — probably a scanned image."""


class PdfTooLargeError(ValueError):
    """The PDF is bigger than 2 MB."""


def parse_resume_pdf(path_or_bytes: str | Path | bytes) -> str:
    """Extract text from a PDF given as a file path or raw bytes (e.g. a Streamlit upload)."""
    data = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    if len(data) > config.MAX_PDF_BYTES:
        raise PdfTooLargeError("The PDF is larger than 2 MB. Please upload a smaller file.")

    try:
        reader = PdfReader(io.BytesIO(data))
        # extract_text() can return None for image-only pages, so use "" instead.
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except PdfReadError as exc:
        raise ValueError(f"Could not read this PDF ({exc}). Please paste the resume text.") from exc

    # Tidy up: collapse runs of spaces and blank lines left by the PDF layout.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()

    if len(text) < config.MIN_PDF_TEXT_CHARS:
        raise ScannedPdfError(
            "This PDF has almost no readable text (it may be a scanned image). "
            "Please paste your resume text instead."
        )
    return text
