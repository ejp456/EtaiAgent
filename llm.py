import os
import json
import math
import time
import hashlib
import httpx
from typing import List, Dict, Any, Optional, Callable

BGE_EMBEDDINGS_FILE = os.path.join("memories", "embeddings_bge_small.json")
FASTEMBED_CACHE_DIR = os.path.join(".models", "fastembed")

_bge_model_instance: Any = None


def get_bge_small_model() -> Any:
    """Lazy-load the local ONNX BAAI/bge-small-en-v1.5 model from `.models/fastembed`."""
    global _bge_model_instance
    if _bge_model_instance is None:
        from fastembed import TextEmbedding
        local_only = os.path.exists(FASTEMBED_CACHE_DIR) and len(os.listdir(FASTEMBED_CACHE_DIR)) > 0
        _bge_model_instance = TextEmbedding(
            model_name="BAAI/bge-small-en-v1.5",
            cache_dir=FASTEMBED_CACHE_DIR,
            local_files_only=local_only,
        )
    return _bge_model_instance


def _bge_small_embedding(text: str, is_query: bool = False) -> List[float]:
    """Generate a 384-d neural embedding using BAAI/bge-small-en-v1.5 (ONNX Runtime)."""
    clean_text = text.strip()
    if not clean_text:
        return [0.0] * 384
    model = get_bge_small_model()
    if is_query:
        # query_embed automatically prepends BGE's asymmetric retrieval instruction:
        # "Represent this sentence for searching relevant passages: "
        vec = next(model.query_embed(clean_text))
    else:
        vec = next(model.passage_embed([clean_text]))
    return [float(x) for x in vec.tolist()]


class LLMClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        embed_model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 60.0,
        num_ctx: Optional[int] = None,
    ):
        raw_url = (
            base_url
            or os.environ.get("OLLAMA_BASE_URL")
            or "http://192.168.86.39:11434"
        ).rstrip("/")
        self.base_url = raw_url
        self.model = model or os.environ.get("OLLAMA_MODEL") or "qwen3:8b"
        self.embed_model = embed_model or os.environ.get("OLLAMA_EMBED_MODEL") or "nomic-embed-text"
        self.api_key = (
            api_key
            or os.environ.get("OLLAMA_API_KEY")
            or "AAAAC3NzaC1lZDI1NTE5AAAAIPcwwcSwd5DB30KzmJ3b/He0PBL7NYxrequCY9jQuNqy"
        )
        self.timeout = timeout
        self.num_ctx = num_ctx or int(os.environ.get("OLLAMA_NUM_CTX", "32768"))

    @property
    def endpoint_url(self) -> str:
        """Construct the native Ollama /api/chat endpoint URL (or custom /chat/completions if specified)"""
        if self.base_url.endswith("/chat/completions") or self.base_url.endswith("/api/chat"):
            return self.base_url
        elif self.base_url.endswith("/v1"):
            return f"{self.base_url[:-3]}/api/chat"
        return f"{self.base_url}/api/chat"

    @property
    def embeddings_url(self) -> str:
        """Construct the OpenAI-compatible embeddings endpoint URL"""
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/embeddings"
        return f"{self.base_url}/v1/embeddings"

    def complete(self, messages: List[Any], tools: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Send messages to LLM and return the assistant message dict"""
        serialized_messages = []
        for msg in messages:
            if hasattr(msg, "to_dict"):
                serialized_messages.append(msg.to_dict())
            elif isinstance(msg, dict):
                serialized_messages.append(msg)
            else:
                serialized_messages.append({
                    "role": getattr(msg, "role", "user"),
                    "content": getattr(msg, "content", ""),
                })

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": serialized_messages,
            "stream": False,
            "options": {
                "num_ctx": self.num_ctx,
            },
        }
        if tools:
            payload["tools"] = tools

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = httpx.post(
                self.endpoint_url,
                headers=headers,
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()
            print("response: {}".format(data))
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            print(f"\n\033[91m[LLM CONNECTION ERROR]\033[0m Could not reach '{self.endpoint_url}' ({type(e).__name__}).")
            print("Please make sure the Ollama server is running and reachable.")
            raise

        # Support both native Ollama /api/chat ('message') and OpenAI-compatible /v1/chat/completions ('choices')
        if "message" in data and isinstance(data["message"], dict):
            return data["message"]
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {})
        return {"role": "assistant", "content": ""}

    def embed(self, text: str, is_query: bool = False) -> List[float]:
        """Generate vector embedding using BAAI/bge-small-en-v1.5 (ONNX Runtime)."""
        return _bge_small_embedding(text, is_query=is_query)


class PersistentEmbeddingStore:
    """Manages a file-backed embedding store (e.g., `memories/embeddings_local.json` or `memories/embeddings_bge_small.json`)
    that pre-computes, persists, and loads document & chunk embeddings at server startup time.
    """

    def __init__(
        self,
        model_id: str,
        file_path: str,
        raw_embed_fn: Callable[[str, bool], List[float]],
    ):
        self.model_id = model_id
        self.file_path = file_path
        self.raw_embed_fn = raw_embed_fn
        # Map of sha256(mode + ":" + text) -> List[float]
        self.vectors_by_hash: Dict[str, List[float]] = {}
        # Map of file/chunk key -> metadata + embedding
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.last_load_time_ms: float = 0.0
        self.last_build_time_ms: float = 0.0
        self.cache_hits: int = 0
        self.cache_misses: int = 0

    @staticmethod
    def _hash_key(text: str, is_query: bool = False) -> str:
        prefix = "q:" if is_query else "d:"
        return hashlib.sha256((prefix + text).encode("utf-8")).hexdigest()

    def load_from_disk(self) -> float:
        """Load pre-computed embeddings from the JSON file at server startup. Returns load time in ms."""
        t0 = time.perf_counter()
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.vectors_by_hash = data.get("vectors_by_hash", {})
                self.entries = data.get("entries", {})
            except Exception:
                self.vectors_by_hash = {}
                self.entries = {}
        self.last_load_time_ms = (time.perf_counter() - t0) * 1000.0
        return self.last_load_time_ms

    def save_to_disk(self) -> None:
        """Persist all pre-computed document and chunk embeddings to `self.file_path`."""
        os.makedirs(os.path.dirname(self.file_path) or ".", exist_ok=True)
        payload = {
            "model_id": self.model_id,
            "updated_at": time.time(),
            "vector_count": len(self.vectors_by_hash),
            "entry_count": len(self.entries),
            "entries": self.entries,
            "vectors_by_hash": self.vectors_by_hash,
        }
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def embed(self, text: str, is_query: bool = False, entry_key: Optional[str] = None) -> List[float]:
        """Return cached embedding if present in the loaded store; otherwise compute and store it."""
        h = self._hash_key(text, is_query=is_query)
        if h in self.vectors_by_hash:
            self.cache_hits += 1
            vec = self.vectors_by_hash[h]
        else:
            self.cache_misses += 1
            vec = self.raw_embed_fn(text, is_query)
            # Only cache passage/document embeddings permanently so the file stays compact and deterministic
            if not is_query:
                self.vectors_by_hash[h] = vec

        if entry_key and not is_query:
            self.entries[entry_key] = {
                "key": entry_key,
                "hash": h,
                "preview": text[:140].strip(),
                "dimension": len(vec),
            }
        return vec


def create_bge_small_embedding_store(file_path: str = BGE_EMBEDDINGS_FILE) -> PersistentEmbeddingStore:
    """Create the `BAAI/bge-small-en-v1.5` ONNX embedding store backed by `memories/embeddings_bge_small.json`."""
    return PersistentEmbeddingStore(
        model_id="BAAI/bge-small-en-v1.5",
        file_path=file_path,
        raw_embed_fn=lambda text, is_query=False: _bge_small_embedding(text, is_query=is_query),
    )
