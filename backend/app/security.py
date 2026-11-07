import secrets

from fastapi import Header, HTTPException

from .core.config import settings


def require_api_key(x_api_key: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_api_key.encode(), settings.api_key.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
