"""PDF text extraction with automatic OCR fallback for scanned pages."""
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional

from .config import settings
from .errors import CorruptedFileError, OCRUnavailableError, UnsupportedFileError
from .models import PageResult
from .ocr import ocr_image


def extract_pdf(path: str, progress: Optional[Callable[[int, int], None]] = None) -> List[PageResult]:
    try:
        from pypdf import PdfReader
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")
            except Exception:
                ok = 0
            if not ok:
                raise CorruptedFileError("PDF is password protected")
        n = len(reader.pages)
    except CorruptedFileError:
        raise
    except Exception as e:
        raise CorruptedFileError(f"Unreadable PDF: {e}")
    if n == 0:
        raise CorruptedFileError("PDF has no pages")
    if n > settings.max_pdf_pages:
        raise UnsupportedFileError(f"PDF has {n} pages; limit is {settings.max_pdf_pages}")

    results: List[PageResult] = []
    need_ocr: List[int] = []
    for i in range(n):  # pass 1: embedded text (fast)
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception:
            text = ""
        results.append(PageResult(i + 1, text, "text", 1.0))
        if len(text.strip()) < settings.min_text_chars:
            need_ocr.append(i)
        if progress and (i % 25 == 0 or i == n - 1):
            progress(i + 1, n)

    def do_ocr(i: int) -> PageResult:
        try:
            from pdf2image import convert_from_path
            imgs = convert_from_path(path, dpi=settings.ocr_dpi, first_page=i + 1, last_page=i + 1)
            if not imgs:
                raise RuntimeError("rasterisation returned no image")
            text, conf = ocr_image(imgs[0])
            # keep embedded text if it is longer (e.g. blank-ish page with a stamp)
            prev = results[i].text
            if len(prev.strip()) > len(text.strip()):
                return PageResult(i + 1, prev, "text", 1.0)
            return PageResult(i + 1, text, "ocr", conf)
        except OCRUnavailableError as e:
            return PageResult(i + 1, results[i].text, "failed", None, str(e))
        except Exception as e:
            return PageResult(i + 1, results[i].text, "failed", None, f"OCR failed: {e}")

    if need_ocr:  # pass 2: OCR only the pages that need it, in parallel
        with ThreadPoolExecutor(max_workers=max(1, settings.ocr_workers)) as ex:
            for k, (i, r) in enumerate(zip(need_ocr, ex.map(do_ocr, need_ocr)), start=1):
                results[i] = r
                if progress:
                    progress(n, n)
    if all(r.method == "failed" for r in results):
        raise CorruptedFileError(results[0].error or "No readable content")
    return results
