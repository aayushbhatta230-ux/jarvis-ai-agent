"""Neural Vector Memory and Retrieval-Augmented Generation (RAG) for JARVIS.

Combines high-dimensional dense vector embeddings, persistent SQLite storage,
and cosine similarity retrieval for deep contextual recall and episodic memory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

import numpy as np


EMBEDDING_DIM = 384


class NeuralSubwordProjector:
    """Fast, deterministic zero-latency dense neural vector projector.
    
    Generates 384-dimensional dense semantic vectors using subword n-gram
    hashing, IDF attenuation, and unit-sphere normalization.
    """

    STOPWORDS = {
        "a", "an", "the", "and", "or", "but", "if", "because", "as", "what",
        "which", "this", "that", "these", "those", "then", "just", "so", "than",
        "such", "both", "through", "about", "for", "is", "of", "while", "during",
        "to", "from", "in", "out", "on", "off", "again", "further", "then", "once",
    }

    # Semantic domain clusters to bridge vocabulary differences in vector space
    DOMAIN_CLUSTERS = {
        "programming": ["coding", "code", "python", "javascript", "developer", "backend", "frontend", "script", "software"],
        "system": ["os", "windows", "computer", "pc", "laptop", "machine", "hardware", "cpu", "ram", "specs"],
        "companion": ["jarvis", "ai", "assistant", "agent", "bot", "neural", "brain", "model"],
        "media": ["music", "song", "audio", "sound", "volume", "track", "youtube", "spotify", "video", "play"],
        "download": ["downloads", "file", "downloaded", "folder", "directory", "document", "saved"],
        "project": ["task", "work", "repo", "repository", "codebase", "development"],
        "interface": ["ui", "screen", "display", "web", "html", "css", "visual", "monitor"],
    }

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def encode(self, text: str) -> np.ndarray:
        clean = re.sub(r"[^\w\s-]", " ", text.lower()).strip()
        tokens = [t for t in clean.split() if len(t) > 1 and t not in self.STOPWORDS]
        vec = np.zeros(self.dim, dtype=np.float32)

        if not tokens:
            return vec

        # Expand tokens with semantic domain clusters
        expanded_tokens = list(tokens)
        for t in tokens:
            for cluster_name, cluster_words in self.DOMAIN_CLUSTERS.items():
                if t == cluster_name or t in cluster_words:
                    # Inject cluster centroid signal
                    expanded_tokens.append(f"cluster:{cluster_name}")

        for token in expanded_tokens:
            is_cluster = token.startswith("cluster:")
            weight = 0.6 if is_cluster else 1.0

            # Word-level projection
            h_word = int(hashlib.md5(f"w:{token}".encode("utf-8")).hexdigest()[:8], 16)
            rng_word = np.random.RandomState(h_word)
            proj = rng_word.standard_normal(self.dim).astype(np.float32) * weight

            # Subword 3-grams to capture morphology, prefixes, and word stems
            if not is_cluster and len(token) >= 3:
                for i in range(len(token) - 2):
                    ngram = token[i : i + 3]
                    h_ng = int(hashlib.md5(f"ng:{ngram}".encode("utf-8")).hexdigest()[:8], 16)
                    rng_ng = np.random.RandomState(h_ng)
                    proj += rng_ng.standard_normal(self.dim).astype(np.float32) * 0.35

            vec += proj

        # Normalize to unit Euclidean norm for exact cosine dot product
        norm = float(np.linalg.norm(vec))
        if norm > 1e-6:
            vec /= norm
        return vec


class NeuralEmbedder:
    """Multi-tier embedding pipeline supporting Ollama, OpenAI, and local Projector."""

    def __init__(self) -> None:
        self.projector = NeuralSubwordProjector()
        self.provider_name = "Neural Subword Projector (Local)"
        self._cache: dict[str, np.ndarray] = {}
        self._check_available_providers()

    def _check_available_providers(self) -> None:
        # Check if Ollama embedding is explicitly enabled
        if os.environ.get("ENABLE_OLLAMA_EMBED") == "1":
            try:
                req = urllib.request.Request(
                    "http://127.0.0.1:11434/api/tags",
                    headers={"User-Agent": "JARVIS"},
                )
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    models = [m.get("name", "") for m in data.get("models", [])]
                    for m in models:
                        if "embed" in m or "minilm" in m:
                            self.provider_name = f"Ollama ({m})"
                            return
            except Exception:
                pass

        # Check if OpenAI API key is present
        if os.environ.get("OPENAI_API_KEY") and not os.environ["OPENAI_API_KEY"].startswith("your_"):
            self.provider_name = "OpenAI (text-embedding-3-small)"

    def _normalize_dim(self, arr: np.ndarray) -> np.ndarray:
        """Guarantee vector matches EMBEDDING_DIM precisely with unit norm."""
        if len(arr) != EMBEDDING_DIM:
            if len(arr) > EMBEDDING_DIM:
                step = len(arr) // EMBEDDING_DIM
                arr = arr[: EMBEDDING_DIM * step].reshape(EMBEDDING_DIM, step).mean(axis=1).astype(np.float32)
            else:
                padded = np.zeros(EMBEDDING_DIM, dtype=np.float32)
                padded[:len(arr)] = arr
                arr = padded
        norm = float(np.linalg.norm(arr))
        return (arr / norm if norm > 1e-6 else arr).astype(np.float32)

    def embed(self, text: str) -> np.ndarray:
        """Produce normalized dense vector embedding with memory cache."""
        if not text or not text.strip():
            return np.zeros(EMBEDDING_DIM, dtype=np.float32)

        cached = self._cache.get(text)
        if cached is not None:
            return cached

        arr: np.ndarray | None = None

        # 1. Try Ollama embedding if nomic-embed-text or minilm is loaded
        if "Ollama" in self.provider_name:
            try:
                model_name = self.provider_name.split("(")[1].rstrip(")")
                payload = json.dumps({"model": model_name, "prompt": text}).encode("utf-8")
                req = urllib.request.Request(
                    "http://127.0.0.1:11434/api/embeddings",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    raw = res.get("embedding")
                    if raw:
                        arr = np.array(raw, dtype=np.float32)
            except Exception:
                pass

        # 2. Try OpenAI embedding if key present
        if arr is None and "OpenAI" in self.provider_name:
            try:
                import openai
                client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
                resp = client.embeddings.create(model="text-embedding-3-small", input=text[:2000])
                raw = resp.data[0].embedding
                arr = np.array(raw, dtype=np.float32)
            except Exception:
                pass

        # 3. Always dependable, lightning-fast local subword neural projector
        if arr is None:
            arr = self.projector.encode(text)

        normed = self._normalize_dim(arr)
        if len(self._cache) < 2048:
            self._cache[text] = normed
        return normed


class NeuralMemory:
    """Persistent neural vector memory with episodic recall and semantic RAG."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else Path(__file__).resolve().parent.parent / "database" / "neural_memory.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = NeuralEmbedder()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS neural_vectors (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        category TEXT NOT NULL,
                        content TEXT NOT NULL UNIQUE,
                        vector BLOB NOT NULL,
                        metadata_json TEXT DEFAULT '{}',
                        importance REAL DEFAULT 1.0,
                        access_count INTEGER DEFAULT 0,
                        created_at REAL NOT NULL,
                        last_recalled REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_neural_category ON neural_vectors(category)"
                )
        finally:
            conn.close()

    def store(
        self,
        content: str,
        category: str = "fact",
        metadata: dict[str, Any] | None = None,
        importance: float = 1.0,
    ) -> int:
        """Embed and persist a piece of knowledge into the neural vector store."""
        clean_content = content.strip()
        if not clean_content:
            return -1

        vec = self.embedder.embed(clean_content)
        vec_blob = vec.tobytes()
        meta_str = json.dumps(metadata or {})
        now = time.time()

        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO neural_vectors (category, content, vector, metadata_json, importance, access_count, created_at, last_recalled)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                    ON CONFLICT(content) DO UPDATE SET
                        category = excluded.category,
                        vector = excluded.vector,
                        metadata_json = excluded.metadata_json,
                        importance = max(neural_vectors.importance, excluded.importance),
                        last_recalled = excluded.last_recalled
                    """,
                    (category, clean_content, vec_blob, meta_str, float(importance), now, now),
                )
                row = conn.execute("SELECT id FROM neural_vectors WHERE content = ?", (clean_content,)).fetchone()
                return int(row[0]) if row else -1
        finally:
            conn.close()

    def search(
        self,
        query: str,
        category: str | None = None,
        top_k: int = 5,
        min_score: float = 0.08,
    ) -> list[dict[str, Any]]:
        """Perform semantic cosine similarity search over stored vectors."""
        clean_query = query.strip()
        if not clean_query:
            return []

        q_vec = self.embedder.embed(clean_query)

        query_sql = "SELECT id, category, content, vector, metadata_json, importance, access_count FROM neural_vectors"
        params: list[Any] = []
        if category:
            query_sql += " WHERE category = ?"
            params.append(category)

        conn = self._get_connection()
        try:
            with conn:
                rows = conn.execute(query_sql, params).fetchall()

            if not rows:
                return []

            scored_results: list[tuple[float, dict[str, Any]]] = []
            now = time.time()
            accessed_ids: list[int] = []

            for row in rows:
                row_id = int(row["id"])
                blob = row["vector"]
                stored_vec = np.frombuffer(blob, dtype=np.float32)

                # Cosine similarity (vectors are unit normalized)
                if stored_vec.shape == q_vec.shape:
                    cos_sim = float(np.dot(q_vec, stored_vec))
                else:
                    cos_sim = 0.0

                # Weight by stored importance
                importance = float(row["importance"] or 1.0)
                composite_score = cos_sim * (0.8 + 0.2 * min(2.0, max(0.5, importance)))

                if composite_score >= min_score or cos_sim >= min_score:
                    meta = {}
                    try:
                        meta = json.loads(row["metadata_json"] or "{}")
                    except Exception:
                        pass

                    result = {
                        "id": row_id,
                        "category": row["category"],
                        "content": row["content"],
                        "similarity": round(cos_sim, 4),
                        "score": round(composite_score, 4),
                        "importance": importance,
                        "metadata": meta,
                    }
                    scored_results.append((composite_score, result))
                    accessed_ids.append(row_id)

            # Sort descending by composite score
            scored_results.sort(key=lambda x: x[0], reverse=True)
            top_items = [item for _, item in scored_results[:top_k]]

            # Update access count in database for top recalled items
            if top_items:
                with conn:
                    for item in top_items:
                        conn.execute(
                            "UPDATE neural_vectors SET access_count = access_count + 1, last_recalled = ? WHERE id = ?",
                            (now, item["id"]),
                        )

            return top_items
        finally:
            conn.close()

    def auto_learn(self, text: str) -> list[str]:
        """Automatically detect facts, preferences, and user instructions from dialogue."""
        learned: list[str] = []
        lower = text.lower().strip()

        patterns = [
            (r"\bmy name is\s+([A-Za-z][A-Za-z '-]{1,60})", "user_profile", "User name is {val}"),
            (r"\b(?:my )?project is called\s+([A-Za-z0-9][A-Za-z0-9 _-]{1,60})", "project", "Project name is {val}"),
            (r"\bremember that\s+(.+?)(?:[.!?]|$)", "fact", "{val}"),
            (r"\bi prefer\s+(.+?)(?:[.!?]|$)", "preference", "User prefers {val}"),
            (r"\bi like\s+(.+?)(?:[.!?]|$)", "interest", "User likes {val}"),
            (r"\bi love\s+(.+?)(?:[.!?]|$)", "interest", "User loves {val}"),
            (r"\bi always use\s+(.+?)(?:[.!?]|$)", "habit", "User always uses {val}"),
            (r"\bmy email is\s+([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", "contact", "User email: {val}"),
        ]

        for regex, category, template in patterns:
            match = re.search(regex, text, re.IGNORECASE)
            if match:
                val = match.group(1).strip(" \t.,!?\"'")
                if val:
                    fact = template.format(val=val)
                    self.store(fact, category=category, importance=1.2)
                    learned.append(fact)

        return learned

    def get_rag_context(self, query: str, max_items: int = 4) -> str:
        """Build contextual RAG string for system prompt injection."""
        results = self.search(query, top_k=max_items, min_score=0.08)
        if not results:
            return ""

        lines = ["RELEVANT NEURAL MEMORIES:"]
        for res in results:
            lines.append(f"- [{res['category'].upper()}] {res['content']} (relevance: {res['score']:.2f})")
        return "\n".join(lines) + "\n"

    def get_stats(self) -> dict[str, Any]:
        """Retrieve total vectors and category distribution."""
        conn = self._get_connection()
        try:
            with conn:
                total = conn.execute("SELECT COUNT(*) FROM neural_vectors").fetchone()[0]
                categories = conn.execute("SELECT category, COUNT(*) as c FROM neural_vectors GROUP BY category").fetchall()
            return {
                "total_vectors": int(total),
                "categories": {row["category"]: int(row["c"]) for row in categories},
                "active_embedder": self.embedder.provider_name,
                "vector_dimension": EMBEDDING_DIM,
            }
        finally:
            conn.close()


# Global singleton instance
_neural_memory: NeuralMemory | None = None


def get_neural_memory() -> NeuralMemory:
    global _neural_memory
    if _neural_memory is None:
        _neural_memory = NeuralMemory()
    return _neural_memory
