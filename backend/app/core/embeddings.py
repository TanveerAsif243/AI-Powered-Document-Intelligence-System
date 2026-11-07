"""Embeddings + chunking. Default 'hash' backend is offline/deterministic (lexical, NOT true semantics);
set EMBEDDING_BACKEND=st for sentence-transformers (real semantic search)."""
import hashlib
import math
import re
from functools import lru_cache
from typing import List

from .config import settings

DIM = settings.embedding_dim


def chunk_text(text: str, size: int = 900, overlap: int = 120) -> List[str]:
    text = re.sub(r"[ \t]+", " ", text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    out, i = [], 0
    while i < len(text):
        end = min(len(text), i + size)
        if end < len(text):                       # prefer breaking at a newline/space
            cut = max(text.rfind("\n", i, end), text.rfind(" ", i, end))
            end = cut if cut > i + size // 2 else end
        out.append(text[i:end].strip())
        if end >= len(text):
            break
        i = max(end - overlap, i + 1)
    return [c for c in out if c]


def _hash_embed(text: str) -> List[float]:
    toks = re.findall(r"[a-z0-9]+", text.lower())
    vec = [0.0] * DIM
    feats = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
    for f in feats:
        h = int(hashlib.md5(f.encode()).hexdigest(), 16)
        vec[h % DIM] += 1.0 if (h >> 64) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


@lru_cache(maxsize=1)
def _st_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(settings.embedding_model)


def embed(texts: List[str]) -> List[List[float]]:
    if settings.embedding_backend == "st":
        return [list(map(float, v)) for v in _st_model().encode(texts, normalize_embeddings=True, batch_size=32)]
    return [_hash_embed(t) for t in texts]


def cosine(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
