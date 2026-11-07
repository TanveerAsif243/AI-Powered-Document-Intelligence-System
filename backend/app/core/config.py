"""Runtime configuration read from environment variables (12-factor style)."""
import os


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self) -> None:
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./docintel.db")
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.upload_dir = os.getenv("UPLOAD_DIR", "./uploads")
        self.api_key = os.getenv("API_KEY", "dev-key-change-me")
        self.cors_origins = [o for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o]
        # Upload limits
        self.max_upload_mb = _int("MAX_UPLOAD_MB", 200)
        self.max_files_per_request = _int("MAX_FILES_PER_REQUEST", 50)
        self.max_pdf_pages = _int("MAX_PDF_PAGES", 5000)
        # Background jobs: inline=True runs in FastAPI BackgroundTasks (dev/tests); False uses Celery+Redis
        self.inline_jobs = _bool("INLINE_JOBS", True)
        # OCR
        self.ocr_lang = os.getenv("OCR_LANG", "eng")
        self.ocr_psm = _int("OCR_PSM", 6)
        self.ocr_dpi = _int("OCR_DPI", 220)
        self.ocr_timeout = _int("OCR_TIMEOUT_S", 25)
        self.ocr_workers = _int("OCR_WORKERS", 2)
        self.min_text_chars = _int("MIN_TEXT_CHARS", 40)  # below this a PDF page is treated as scanned
        self.low_ocr_confidence = float(os.getenv("LOW_OCR_CONFIDENCE", "0.6"))
        # LLM
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.llm_model = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
        self.llm_max_chars = _int("LLM_MAX_CHARS", 12000)
        self.use_llm = _bool("USE_LLM", True)
        # Embeddings: "hash" (offline, lexical) or "st" (sentence-transformers, real semantic)
        self.embedding_backend = os.getenv("EMBEDDING_BACKEND", "hash")
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        self.embedding_dim = 384


settings = Settings()
