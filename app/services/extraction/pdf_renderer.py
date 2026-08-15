"""
Stage 1: PDF -> high-resolution page images.

Uses PyMuPDF (fitz) rather than pdf2image/poppler as the primary path —
no external `poppler` binary dependency, faster, and gives direct access to
each page's vector drawing commands (useful later if you want to bypass CV
detection for vector-sourced drawings, see the note in pid-intelligence
history). pdf2image is kept as an optional fallback for environments that
already standardize on poppler.
"""
import os
from dataclasses import dataclass

from app.core.errors import UnsupportedFileError
from app.core.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class RenderedPage:
    page_number: int  # 1-indexed
    image_path: str
    width_px: int
    height_px: int


def render_pdf_to_images(pdf_path: str, output_dir: str, dpi: int = 300) -> list[RenderedPage]:
    if not pdf_path.lower().endswith(".pdf"):
        raise UnsupportedFileError(f"Expected a .pdf file, got: {pdf_path}")

    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]

    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF (fitz) is not installed. Run `pip install pymupdf`."
        ) from exc

    pages: list[RenderedPage] = []
    zoom = dpi / 72.0  # PDF base unit is 72 DPI
    matrix = fitz.Matrix(zoom, zoom)

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise UnsupportedFileError(f"Could not open PDF: {pdf_path}", {"error": str(exc)})

    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        image_path = os.path.join(output_dir, f"{base_name}_page_{i}.png")
        pix.save(image_path)
        pages.append(RenderedPage(page_number=i, image_path=image_path, width_px=pix.width, height_px=pix.height))
        logger.info("page_rendered", extra={"context": {"page": i, "path": image_path}})

    doc.close()
    return pages
