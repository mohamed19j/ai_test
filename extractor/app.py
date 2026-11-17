"""Sidecar d'extraction de texte : PHP poste un fichier, ce service renvoie du texte ou des images."""

from __future__ import annotations

import base64
import io
import shutil
from importlib.metadata import version
from pathlib import PurePosixPath

import openpyxl
import pymupdf
from docx import Document
from fastapi import FastAPI, File, HTTPException, UploadFile
from pptx import Presentation

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 120_000
MAX_RENDER_PAGES = 8
MIN_CHARS_PER_PDF_PAGE = 40
MAX_SPREADSHEET_ROWS = 2000

TEXT_SUFFIXES = {
    ".txt", ".md", ".php", ".html", ".htm", ".css", ".js", ".ts",
    ".py", ".json", ".sql", ".xml", ".ini", ".yml", ".yaml", ".log",
}
SPREADSHEET_SUFFIXES = {".xlsx", ".xlsm", ".csv"}
OFFICE_SUFFIXES = {".docx", ".pptx"}

app = FastAPI(title="TP attachment extractor", docs_url=None, redoc_url=None)


def _suffix(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).suffix.lower()


def _clip(text: str) -> tuple[str, bool]:
    if len(text) > MAX_TEXT_CHARS:
        return text[:MAX_TEXT_CHARS], True
    return text, False


def _extract_plain_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_docx(data: bytes) -> str:
    document = Document(io.BytesIO(data))
    lines = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(lines)


def _extract_pptx(data: bytes) -> str:
    presentation = Presentation(io.BytesIO(data))
    lines = []
    for index, slide in enumerate(presentation.slides, start=1):
        lines.append(f"--- Diapositive {index} ---")
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    text = "".join(run.text for run in paragraph.runs).strip()
                    if text:
                        lines.append(text)
            if shape.has_table:
                for row in shape.table.rows:
                    lines.append("\t".join(cell.text.strip() for cell in row.cells))
    return "\n".join(lines)


def _extract_spreadsheet(data: bytes, suffix: str) -> str:
    if suffix == ".csv":
        return _extract_plain_text(data)

    book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    lines = []
    try:
        for sheet in book.worksheets:
            lines.append(f"--- Feuille: {sheet.title} ---")
            for row_index, row in enumerate(sheet.iter_rows(values_only=True)):
                if row_index >= MAX_SPREADSHEET_ROWS:
                    lines.append("... (feuilles tronquees)")
                    break
                cells = ["" if value is None else str(value) for value in row]
                if any(cells):
                    lines.append("\t".join(cells))
    finally:
        book.close()
    return "\n".join(lines)


def _ocr_pages(images_b64: list[str]) -> str:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        return ""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""

    pytesseract.pytesseract.tesseract_cmd = tesseract
    lines = []
    for index, encoded in enumerate(images_b64, start=1):
        image = Image.open(io.BytesIO(base64.b64decode(encoded)))
        lines.append(f"--- Page {index} (OCR) ---")
        lines.append(pytesseract.image_to_string(image))
    return "\n".join(lines)


def _extract_pdf(data: bytes) -> dict:
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        page_count = document.page_count
        pages = [page.get_text("text") for page in document]
    finally:
        document.close()

    text = "\n\n".join(pages).strip()
    if len(text) >= MIN_CHARS_PER_PDF_PAGE * max(page_count, 1):
        return {"kind": "text", "text": text, "images": [], "pages": page_count}

    document = pymupdf.open(stream=data, filetype="pdf")
    images = []
    try:
        for index in range(min(page_count, MAX_RENDER_PAGES)):
            pixmap = document[index].get_pixmap(dpi=180, colorspace=pymupdf.csRGB)
            images.append(base64.b64encode(pixmap.tobytes("png")).decode("ascii"))
    finally:
        document.close()

    return {
        "kind": "images" if images else "empty",
        "text": _ocr_pages(images),
        "images": images,
        "pages": page_count,
    }


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "pymupdf": version("pymupdf"),
        "tesseract": shutil.which("tesseract") is not None,
        "max_render_pages": MAX_RENDER_PAGES,
    }


@app.post("/extract")
async def extract(file: UploadFile = File(...)) -> dict:
    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Le fichier depasse 25 Mo.")

    suffix = _suffix(file.filename or "")
    try:
        if suffix in TEXT_SUFFIXES:
            result = {"kind": "text", "text": _extract_plain_text(data), "images": [], "pages": 1}
        elif suffix in SPREADSHEET_SUFFIXES:
            result = {"kind": "text", "text": _extract_spreadsheet(data, suffix), "images": [], "pages": 1}
        elif suffix == ".docx":
            result = {"kind": "text", "text": _extract_docx(data), "images": [], "pages": 1}
        elif suffix == ".pptx":
            result = {"kind": "text", "text": _extract_pptx(data), "images": [], "pages": 1}
        elif suffix == ".pdf":
            result = _extract_pdf(data)
        else:
            raise HTTPException(status_code=415, detail=f"Extension non supportee: {suffix or 'inconnue'}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Fichier illisible: {exc}") from exc

    result["text"], result["truncated"] = _clip(result["text"])
    if not result["text"] and not result["images"]:
        result["kind"] = "empty"
    return result
