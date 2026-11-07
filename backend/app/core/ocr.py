"""Image preprocessing + Tesseract OCR with per-page confidence."""
from typing import List, Tuple

import numpy as np
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from .config import settings
from .errors import CorruptedFileError, OCRUnavailableError
from .models import PageResult

Image.MAX_IMAGE_PIXELS = 150_000_000  # decompression-bomb guard
MAX_FRAMES = 300


def _flatten(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img


def _estimate_skew(gray: Image.Image) -> float:
    """Projection-profile deskew search in [-8, 8] degrees. Returns 0 if no clear gain."""
    try:
        import cv2
    except ImportError:
        return 0.0
    small = gray.copy()
    small.thumbnail((900, 900))
    arr = np.array(small)
    _, bw = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = bw.shape

    def score(angle: float) -> float:
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        r = cv2.warpAffine(bw, m, (w, h), flags=cv2.INTER_NEAREST)
        return float(np.var(r.sum(axis=1).astype(float)))

    base = score(0.0)
    best_a, best_s = 0.0, base
    for a in np.arange(-8, 8.01, 1.0):
        s = score(float(a))
        if s > best_s:
            best_a, best_s = float(a), s
    for a in np.arange(best_a - 0.75, best_a + 0.76, 0.25):
        s = score(float(a))
        if s > best_s:
            best_a, best_s = float(a), s
    return best_a if best_s > base * 1.05 and abs(best_a) >= 0.5 else 0.0


def preprocess(img: Image.Image) -> Image.Image:
    img = _flatten(ImageOps.exif_transpose(img))
    gray = ImageOps.grayscale(img)
    w, h = gray.size
    if w < 1600:
        s = 2000 / w
        gray = gray.resize((int(w * s), int(h * s)), Image.LANCZOS)
    elif w > 3600:
        s = 3000 / w
        gray = gray.resize((int(w * s), int(h * s)), Image.LANCZOS)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    angle = _estimate_skew(gray)
    if angle:
        gray = gray.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    return gray


def _binarize(gray: Image.Image) -> Image.Image:
    """Fallback for noisy/blurred scans: Gaussian blur + Otsu threshold (much faster and cleaner for Tesseract)."""
    try:
        import cv2
    except ImportError:
        return gray
    arr = cv2.GaussianBlur(np.array(gray), (0, 0), 1.5)
    _, bw = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return Image.fromarray(bw)


def _tesseract(pytesseract, gray: Image.Image) -> Tuple[str, float]:
    try:
        d = pytesseract.image_to_data(gray, lang=settings.ocr_lang, config=f"--oem 3 --psm {settings.ocr_psm}",
                                      output_type=pytesseract.Output.DICT, timeout=settings.ocr_timeout)
    except pytesseract.TesseractNotFoundError:
        raise OCRUnavailableError("Tesseract binary not found on PATH")
    except RuntimeError:               # per-pass timeout: treat as "no result", caller falls back
        return "", 0.0
    lines, confs = {}, []
    for i, t in enumerate(d["text"]):
        t = (t or "").strip()
        if not t:
            continue
        lines.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(t)
        c = float(d["conf"][i])
        if c >= 0:
            confs.append(c)
    text = "\n".join(" ".join(ws) for _, ws in sorted(lines.items()))
    return text, (sum(confs) / len(confs) / 100.0 if confs else 0.0)


def ocr_image(img: Image.Image) -> Tuple[str, float]:
    """Two-pass OCR. Returns (text, mean_confidence 0..1)."""
    try:
        import pytesseract
    except ImportError:
        raise OCRUnavailableError("pytesseract is not installed")
    gray = preprocess(img)
    text, conf = _tesseract(pytesseract, gray)
    if conf < 0.75 or len(text.strip()) < 20:          # poor first pass -> retry binarised
        text2, conf2 = _tesseract(pytesseract, _binarize(gray))
        if conf2 > conf or len(text.strip()) < 20:
            text, conf = text2, conf2
    return text, conf


def extract_image(path: str) -> List[PageResult]:
    """OCR a JPG/PNG/TIFF (multi-frame TIFF -> one page per frame)."""
    try:
        im = Image.open(path)
        im.load()  # raises on truncated data
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError) as e:
        raise CorruptedFileError(f"Unreadable image: {e}")
    results: List[PageResult] = []
    for n, frame in enumerate(ImageSequence.Iterator(im), start=1):
        if n > MAX_FRAMES:
            break
        try:
            text, conf = ocr_image(frame.copy())
            results.append(PageResult(n, text, "ocr", conf))
        except OCRUnavailableError:
            raise
        except Exception as e:  # keep going: partial results are better than none
            results.append(PageResult(n, "", "failed", None, f"OCR failed: {e}"))
    if not results or all(r.method == "failed" for r in results):
        raise CorruptedFileError(results[0].error if results else "Image has no frames")
    return results
