class DocIntelError(Exception):
    """Base error. `permanent` errors are never retried."""
    permanent = True


class UnsupportedFileError(DocIntelError):
    pass


class CorruptedFileError(DocIntelError):
    pass


class OCRUnavailableError(DocIntelError):
    pass


class TransientError(DocIntelError):
    """Network / rate-limit / timeout problems: safe to retry with backoff."""
    permanent = False
