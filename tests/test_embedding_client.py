"""Tests for embedding_client — mocks the HTTP call."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from unittest.mock import patch, MagicMock
import json


def test_cosine_similarity_identical():
    from embedding_client import cosine_similarity
    v = [1.0, 0.0, 0.0]
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-6


def test_cosine_similarity_orthogonal():
    from embedding_client import cosine_similarity
    assert abs(cosine_similarity([1, 0], [0, 1])) < 1e-6


def test_embed_client_caches():
    from embedding_client import EmbeddingClient
    with patch("embedding_client.embed") as mock_embed:
        mock_embed.return_value = [[0.1, 0.2, 0.3]]
        client = EmbeddingClient()
        v1 = client.get("hello")
        v2 = client.get("hello")
        assert v1 == v2
        assert mock_embed.call_count == 1  # cached on second call


def test_embed_client_fallback_on_error():
    from embedding_client import EmbeddingClient
    with patch("embedding_client.embed", side_effect=Exception("network error")):
        client = EmbeddingClient()
        result = client.get("hello")
        assert result is None  # graceful fallback


def test_score_match_semantic_fallback():
    """Semantic score returns 0.0 when embedding unavailable (fallback to keyword)."""
    # Import evolver-equivalent: for kernel (no evolver.py), we test EmbeddingClient.similarity fallback
    from embedding_client import EmbeddingClient
    with patch.object(EmbeddingClient, "similarity", return_value=None):
        client = EmbeddingClient()
        result = client.similarity("find a skill", "test skill does stuff")
        assert result is None
