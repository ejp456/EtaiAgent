import os
import re
import math
import time
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Set, Tuple

from llm import (
    _bge_small_embedding,
    create_bge_small_embedding_store,
    PersistentEmbeddingStore,
    BGE_EMBEDDINGS_FILE,
)


@dataclass
class MemoryDocument:
    """Represents a raw memory file loaded from disk."""
    doc_id: str
    content: str
    updated_at: float = field(default_factory=time.time)


@dataclass
class MemoryChunk:
    """Represents a contextual chunk derived from a MemoryDocument with parent context."""
    chunk_id: str
    doc_id: str
    section_title: str
    raw_text: str
    contextualized_text: str
    parent_context: str
    updated_at: float
    embedding: List[float] = field(default_factory=list)


@dataclass
class SearchResult:
    """Search result returned by MemorySearchEngine."""
    doc_id: str
    chunk_id: str
    score: float
    snippet: str
    parent_context: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    if n1 == 0.0 or n2 == 0.0:
        return 0.0
    return dot / (n1 * n2)


def chunk_document_contextually(doc: MemoryDocument) -> List[MemoryChunk]:
    """Split a MemoryDocument into structure-aware contextual chunks with breadcrumb headers."""
    text = doc.content.strip()
    if not text:
        return []

    # Normalize inline numbered items (e.g., "green.3. At 6am" -> newline)
    normalized = re.sub(r"(\.)(\d+\.\s+)", r"\1\n\2", text)
    lines = normalized.splitlines()

    raw_basename = os.path.basename(doc.doc_id)
    clean_file_topic = os.path.splitext(raw_basename)[0].replace("_", " ").strip()
    doc_title = clean_file_topic

    for line in lines:
        s = line.strip()
        if s.startswith("#"):
            doc_title = s.lstrip("#").strip()
            break
        elif s.startswith("**") and s.endswith("**") and len(s) < 80:
            doc_title = s.strip("*").strip()
            break

    # Split into logical sections by markdown headers (#) or bold subheaders
    sections: List[Tuple[str, List[str]]] = []
    current_section_title = doc_title
    current_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        is_md_header = stripped.startswith("#")
        is_bold_header = (
            (stripped.startswith("**") and stripped.endswith("**:"))
            or (stripped.startswith("**") and stripped.endswith("**") and len(stripped) < 80)
        )

        # Attach bold metadata lines (e.g. date/location) to current section title
        if is_bold_header and len(current_lines) == 1 and current_lines[0].strip().startswith("#"):
            sub_meta = stripped.strip("*").rstrip(":").strip()
            current_section_title = f"{current_section_title} ({sub_meta})"
            current_lines.append(line)
            continue

        is_header = is_md_header or is_bold_header
        if is_header and current_lines:
            has_body = any(not ln.strip().startswith("#") for ln in current_lines)
            new_title = stripped.lstrip("#").strip("*").rstrip(":").strip()
            if has_body:
                sections.append((current_section_title, current_lines))
                current_section_title = new_title
                current_lines = [line]
            else:
                if current_section_title.lower() != doc_title.lower():
                    current_section_title = f"{current_section_title} > {new_title}"
                else:
                    current_section_title = new_title
                current_lines = [line]
        else:
            if is_header:
                current_section_title = stripped.lstrip("#").strip("*").rstrip(":").strip()
            current_lines.append(line)

    if current_lines:
        sections.append((current_section_title, current_lines))

    chunks: List[MemoryChunk] = []
    chunk_counter = 0

    for sec_title, sec_lines in sections:
        sec_text = "\n".join(sec_lines).strip()
        bullet_lines = [
            ln.strip() for ln in sec_lines
            if re.match(r"^(?:[-*]|\d+\.)\s+", ln.strip())
        ]

        breadcrumb_parts = [clean_file_topic]
        if doc_title.lower() != clean_file_topic.lower():
            breadcrumb_parts.append(doc_title)
        if sec_title.lower() not in (clean_file_topic.lower(), doc_title.lower()):
            breadcrumb_parts.append(sec_title)
        breadcrumb = " | ".join(breadcrumb_parts)

        if len(bullet_lines) >= 2:
            for b_line in bullet_lines:
                chunk_counter += 1
                cid = f"{doc.doc_id}#chunk_{chunk_counter}"
                ctx_text = f"[{breadcrumb}] {b_line}"
                chunks.append(MemoryChunk(
                    chunk_id=cid,
                    doc_id=doc.doc_id,
                    section_title=sec_title,
                    raw_text=b_line,
                    contextualized_text=ctx_text,
                    parent_context=normalized,
                    updated_at=doc.updated_at,
                ))
        else:
            chunk_counter += 1
            cid = f"{doc.doc_id}#chunk_{chunk_counter}"
            ctx_text = f"[{breadcrumb}] {sec_text}"
            chunks.append(MemoryChunk(
                chunk_id=cid,
                doc_id=doc.doc_id,
                section_title=sec_title,
                raw_text=sec_text,
                contextualized_text=ctx_text,
                parent_context=normalized,
                updated_at=doc.updated_at,
            ))

    return chunks


class MemorySearchEngine:
    """Unified memory search engine powered by BAAI/bge-small-en-v1.5 and persistent caching."""

    def __init__(
        self,
        memories_dir: str = "memories",
        index_file: str = BGE_EMBEDDINGS_FILE,
    ):
        self.memories_dir = memories_dir
        self.index_file = index_file
        self.store = create_bge_small_embedding_store(index_file)
        self.documents: List[MemoryDocument] = []
        self._chunks: List[MemoryChunk] = []

    def load_documents_from_disk(self) -> List[MemoryDocument]:
        """Load all memory markdown and text files from memories_dir."""
        docs: List[MemoryDocument] = []
        if not os.path.exists(self.memories_dir):
            return docs

        for fname in sorted(os.listdir(self.memories_dir)):
            if not fname.endswith((".md", ".txt")):
                continue
            fpath = os.path.join(self.memories_dir, fname)
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                stat = os.stat(fpath)
                docs.append(MemoryDocument(
                    doc_id=fpath,
                    content=content,
                    updated_at=stat.st_mtime,
                ))
            except Exception:
                pass
        self.documents = docs
        return docs

    def index_all(self, force_save: bool = False) -> Dict[str, Any]:
        """Index all documents into contextual chunks with BGE neural embeddings."""
        self.load_documents_from_disk()
        self.store.load_from_disk()

        self._chunks = []
        for doc in self.documents:
            for ch in chunk_document_contextually(doc):
                ch.embedding = self.store.embed(
                    ch.contextualized_text,
                    is_query=False,
                    entry_key=ch.chunk_id,
                )
                self._chunks.append(ch)

        if force_save or self.store.cache_misses > 0 or not os.path.exists(self.index_file):
            self.store.save_to_disk()

        return {
            "documents_indexed": len(self.documents),
            "chunks_indexed": len(self._chunks),
            "vectors_cached": len(self.store.vectors_by_hash),
            "cache_hits": self.store.cache_hits,
            "cache_misses": self.store.cache_misses,
            "file_load_ms": self.store.last_load_time_ms,
        }

    def warmup_on_startup(self) -> Dict[str, Any]:
        """Fast startup warmup: loads pre-computed embeddings and builds in-memory chunk index."""
        t0 = time.perf_counter()
        stats = self.index_all()
        stats["total_startup_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        return stats

    def search(self, query: str, top_k: int = 3) -> List[SearchResult]:
        """Search memory documents using BAAI/bge-small-en-v1.5 contextual vector search."""
        if not self._chunks:
            self.index_all()

        q_vec = _bge_small_embedding(query, is_query=True)

        scored: List[SearchResult] = []
        for ch in self._chunks:
            sim = cosine_similarity(q_vec, ch.embedding)
            scored.append(SearchResult(
                doc_id=ch.doc_id,
                chunk_id=ch.chunk_id,
                score=round(sim, 4),
                snippet=ch.raw_text,
                parent_context=ch.parent_context,
            ))

        scored.sort(key=lambda r: r.score, reverse=True)

        # Deduplicate by document picking the highest-scoring chunk
        seen_docs: Set[str] = set()
        deduped: List[SearchResult] = []
        for r in scored:
            if r.doc_id not in seen_docs:
                seen_docs.add(r.doc_id)
                deduped.append(r)
                if len(deduped) >= top_k:
                    break

        return deduped
