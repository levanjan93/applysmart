"""In-memory resume uploads for the local browser session."""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from secrets import token_urlsafe
from threading import RLock
from xml.etree import ElementTree

MAX_RESUME_BYTES = 10 * 1024 * 1024
ALLOWED_RESUME_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


@dataclass(frozen=True)
class ResumeUpload:
    upload_id: str
    original_name: str
    content: bytes
    sha256: str
    text: str


class ResumeUploadStore:
    """Hold user-selected resume bytes without exposing filesystem paths."""

    def __init__(self):
        self._uploads: dict[str, ResumeUpload] = {}
        self._drafts: dict[str, dict] = {}
        self._lock = RLock()

    def add(self, original_name: str, content: bytes) -> ResumeUpload:
        name = Path(str(original_name)).name
        if not name or name != str(original_name) or len(name) > 255:
            raise ValueError("Resume filename is invalid.")
        if Path(name).suffix.casefold() not in ALLOWED_RESUME_SUFFIXES:
            raise ValueError("Resume must be a PDF, DOCX, TXT, or Markdown file.")
        if not isinstance(content, bytes) or not content:
            raise ValueError("The uploaded resume is empty.")
        if len(content) > MAX_RESUME_BYTES:
            raise ValueError("Resume file is larger than the 10 MB limit.")
        text = extract_resume_bytes(name, content)
        upload = ResumeUpload(
            upload_id="UPL-" + token_urlsafe(18),
            original_name=name,
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            text=text,
        )
        with self._lock:
            self._uploads[upload.upload_id] = upload
        return upload

    def get(self, upload_id: str) -> ResumeUpload:
        with self._lock:
            upload = self._uploads.get(str(upload_id))
            if upload is None:
                raise ValueError("The resume upload is unavailable. Upload it again.")
            return upload

    def set_draft(self, upload_id: str, draft: dict) -> None:
        """Keep the parsed draft that the approval card shows and the import saves."""
        with self._lock:
            self.get(upload_id)
            self._drafts[str(upload_id)] = draft

    def draft(self, upload_id: str) -> dict | None:
        with self._lock:
            return self._drafts.get(str(upload_id))

    def discard(self, upload_id: str) -> None:
        with self._lock:
            self._uploads.pop(str(upload_id), None)
            self._drafts.pop(str(upload_id), None)

    def clear(self) -> None:
        with self._lock:
            self._uploads.clear()
            self._drafts.clear()


def extract_resume_bytes(filename: str, content: bytes) -> str:
    """Extract text from uploaded bytes without accepting a local path."""
    suffix = Path(filename).suffix.casefold()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF support requires the pypdf package from requirements.txt.") from exc
        try:
            reader = PdfReader(BytesIO(content))
            text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)
        except Exception as exc:
            raise ValueError("The PDF resume could not be read.") from exc
    elif suffix == ".docx":
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                xml = archive.read("word/document.xml")
            root = ElementTree.fromstring(xml)
            namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            paragraphs = [
                "".join(node.text or "" for node in paragraph.iter(namespace + "t"))
                for paragraph in root.iter(namespace + "p")
            ]
            text = "\n".join(paragraphs)
        except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise ValueError("The DOCX resume could not be read.") from exc
    elif suffix in {".txt", ".md"}:
        try:
            text = content.decode("utf-8")
        except UnicodeError as exc:
            raise ValueError("Plain-text resumes must use UTF-8 encoding.") from exc
    else:
        raise ValueError("Resume must be a PDF, DOCX, TXT, or Markdown file.")
    text = text.strip()
    if not text:
        raise ValueError("No readable text was found in the resume.")
    return text


resume_uploads = ResumeUploadStore()

