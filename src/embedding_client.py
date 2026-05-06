"""Thin client for the ai-server embeddings endpoint."""
import json
import logging
import math
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8770/embeddings"


def embed(texts: list[str], url: str = _DEFAULT_URL) -> list[list[float]]:
    """Embed a list of texts. Returns list of float vectors. Raises on failure."""
    payload = json.dumps({"input": texts}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    return result["data"]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingClient:
    """Caching embedding client. Falls back silently to None on network error."""

    def __init__(self, url: str = _DEFAULT_URL):
        self.url = url
        self._cache: dict[str, list[float]] = {}

    def get(self, text: str) -> Optional[list[float]]:
        """Get embedding for text, using cache. Returns None on failure."""
        if text in self._cache:
            return self._cache[text]
        try:
            vecs = embed([text], self.url)
            if vecs:
                self._cache[text] = vecs[0]
                return vecs[0]
        except Exception as e:
            logger.warning(f"[EmbeddingClient] embed failed: {e}")
        return None

    def get_batch(self, texts: list[str]) -> dict[str, list[float]]:
        """Embed multiple texts in one call. Returns dict text->vector for successful ones."""
        uncached = [t for t in texts if t not in self._cache]
        if uncached:
            try:
                vecs = embed(uncached, self.url)
                for text, vec in zip(uncached, vecs):
                    self._cache[text] = vec
            except Exception as e:
                logger.warning(f"[EmbeddingClient] batch embed failed: {e}")
        return {t: self._cache[t] for t in texts if t in self._cache}

    def similarity(self, a: str, b: str) -> Optional[float]:
        """Cosine similarity between two texts. Returns None if either embed fails."""
        va, vb = self.get(a), self.get(b)
        if va is None or vb is None:
            return None
        return cosine_similarity(va, vb)
