"""RAG store: FastEmbed embeddings + an in-memory Chroma collection, one per session.

What it is for: finding the exact resume / JD / rubric lines that support a claim
("evidence"), e.g. "Has Flask" for the skill "FastAPI or Django". The LLM then
quotes real text instead of guessing.

Why these choices:
- FastEmbed (ONNX, CPU, ~65 MB model) instead of sentence-transformers: no PyTorch,
  fits the ~1 GB RAM of free hosting.
- Chroma in-memory: nothing is written to disk, data disappears with the session.
- We pass our OWN vectors (embedding_function=None), so Chroma never downloads its
  default model.
- In-memory Chroma clients in one process SHARE data, so each session gets a unique
  collection name and deletes it in close().
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from dataclasses import dataclass

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # harmless Windows warning

import chromadb  # noqa: E402
from fastembed import TextEmbedding  # noqa: E402

import config  # noqa: E402

log = logging.getLogger("interview_arena.rag")

Source = str  # "resume" | "jd" | "rubric"

_model: TextEmbedding | None = None
_model_lock = threading.Lock()
_client = chromadb.EphemeralClient()  # in-memory; one per process is enough


def get_embedder() -> TextEmbedding:
    """Load the embedding model once per process (first time downloads ~65 MB)."""
    global _model
    with _model_lock:
        if _model is None:
            _model = TextEmbedding(config.EMBED_MODEL, cache_dir=str(config.EMBED_CACHE_DIR))
    return _model


def chunk_text(text: str, size: int = config.CHUNK_SIZE, overlap: int = config.CHUNK_OVERLAP) -> list[str]:
    """Split text into ~`size`-char chunks that overlap by ~`overlap` chars.

    Cuts at a space or newline (not mid-word). The overlap means a sentence that
    falls on a boundary still appears whole in at least one chunk.
    """
    text = text.strip()
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(text.rfind("\n", start + size // 2, end), text.rfind(" ", start + size // 2, end))
            end = cut if cut > start else end
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        next_start = end - overlap
        space = text.find(" ", next_start, end)  # begin the overlap at a word boundary
        start = space + 1 if space != -1 else next_start
    return [c for c in chunks if c]


@dataclass
class Evidence:
    text: str
    source: Source
    distance: float  # cosine distance: 0 = identical meaning, bigger = less related


class SessionStore:
    """One interview session's documents. Create with build_session_store()."""

    def __init__(self, session_id: str | None = None) -> None:
        # Name rules: 3–512 chars of [a-zA-Z0-9._-]; unique so sessions never mix.
        self.name = f"session-{session_id or uuid.uuid4().hex}"
        self._col = _client.create_collection(
            self.name, embedding_function=None, configuration={"hnsw": {"space": "cosine"}}
        )

    def add_document(self, text: str, source: Source) -> int:
        """Chunk, embed and store a document. Returns the number of chunks."""
        chunks = chunk_text(text)
        if not chunks:
            return 0
        vectors = [v.tolist() for v in get_embedder().passage_embed(chunks)]
        self._col.add(
            ids=[f"{source}-{i}" for i in range(len(chunks))],
            documents=chunks,
            embeddings=vectors,
            metadatas=[{"source": source, "chunk": i} for i in range(len(chunks))],
        )
        return len(chunks)

    def retrieve_evidence(self, query: str, k: int = 3, source: Source | None = None) -> list[Evidence]:
        """Top-k chunks most similar in meaning to `query`, optionally from one source only."""
        vector = [v.tolist() for v in get_embedder().query_embed([query])]
        result = self._col.query(
            query_embeddings=vector,
            n_results=k,
            where={"source": source} if source else None,
        )
        return [
            Evidence(text=doc, source=meta["source"], distance=dist)
            for doc, meta, dist in zip(
                result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True
            )  # parallel lists
        ]

    def close(self) -> None:
        """Delete this session's data from memory."""
        try:
            _client.delete_collection(self.name)
        except Exception as exc:  # e.g. already deleted — safe to ignore, but log it
            log.debug("could not delete collection %s: %s", self.name, type(exc).__name__)


def build_session_store(resume_text: str, jd_text: str, session_id: str | None = None) -> SessionStore:
    """Index the resume, the JD and the scoring rubric for one session."""
    store = SessionStore(session_id)
    store.add_document(resume_text, "resume")
    store.add_document(jd_text, "jd")
    store.add_document(config.RUBRIC_PATH.read_text(encoding="utf-8"), "rubric")
    return store
