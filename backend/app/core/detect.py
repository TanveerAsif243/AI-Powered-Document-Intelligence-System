"""Content sniffing: never trust the file extension."""
import os
import zipfile
from typing import List, Optional, Tuple

from .errors import CorruptedFileError, UnsupportedFileError

EXT_KIND = {".pdf": "pdf", ".jpg": "image", ".jpeg": "image", ".png": "image", ".tif": "image",
            ".tiff": "image", ".xlsx": "xlsx", ".xls": "xls", ".csv": "csv"}


def ext_kind(filename: str) -> Optional[str]:
    return EXT_KIND.get(os.path.splitext(filename or "")[1].lower())


def _decodes(head: bytes) -> Optional[str]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return head.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def sniff_kind(path: str) -> Optional[str]:
    with open(path, "rb") as f:
        head = f.read(8192)
    if not head:
        raise CorruptedFileError("File is empty")
    if b"%PDF-" in head[:1024]:
        return "pdf"
    if head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG\r\n\x1a\n") or head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as z:
                return "xlsx" if "xl/workbook.xml" in z.namelist() else None
        except zipfile.BadZipFile:
            raise CorruptedFileError("Corrupted XLSX/ZIP container")
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "xls"
    if b"\x00" not in head:
        text = _decodes(head)
        if text and "\n" in text and any(d in text for d in (",", ";", "\t", "|")):
            return "csv"
    return None


def detect_kind(path: str, filename: Optional[str] = None) -> Tuple[str, List[str]]:
    """Return (kind, warnings). kind in pdf|image|xlsx|xls|csv."""
    declared = ext_kind(filename or path)
    sniffed = sniff_kind(path)
    warnings: List[str] = []
    if sniffed is None:
        if declared == "csv":
            with open(path, "rb") as f:
                if _decodes(f.read(8192)) is not None and b"\x00" not in open(path, "rb").read(8192):
                    return "csv", warnings
        if declared:
            raise CorruptedFileError(f"Content is not a valid {declared.upper()} file")
        raise UnsupportedFileError("Unsupported file type")
    if declared and declared != sniffed:
        warnings.append(f"Extension suggests {declared} but content is {sniffed}; processed as {sniffed}")
    return sniffed, warnings
