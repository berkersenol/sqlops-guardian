"""
SQLOps Guardian - RAG Layer
ChromaDB vector search for similar SQL anti-pattern cases.
"""

from collections.abc import Mapping

import chromadb
from chromadb.api import ClientAPI
from .config import config


_client: ClientAPI | None = None
_collection: chromadb.Collection | None = None


def init_collection() -> chromadb.Collection:
    """Connect to ChromaDB and get or create the collection."""
    global _client, _collection
    _client = chromadb.PersistentClient(path=config.CHROMA_PERSIST_DIR)
    _collection = _client.get_or_create_collection(
        name=config.CHROMA_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    return _collection


def _get_collection() -> chromadb.Collection:
    """Return existing collection or initialize one."""
    if _collection is None:
        return init_collection()
    return _collection


def _meta_str(meta: Mapping[str, object], key: str) -> str:
    """Read a metadata value as a string. Chroma metadata values are not str-only."""
    value = meta.get(key)
    return value if isinstance(value, str) else ""


def _meta_list(meta: Mapping[str, object], key: str) -> list[str]:
    """Read a comma-joined metadata value back into a list."""
    value = _meta_str(meta, key)
    return value.split(",") if value else []


def _build_case_text(query: str, problems: list[str], fix: str, tables: list[str]) -> str:
    """Build a descriptive text for embedding — NOT raw SQL."""
    table_str = ", ".join(tables) if tables else "unknown table"
    problem_str = ", ".join(problems) if problems else "no specific problems"
    return (
        f"Query on {table_str}. "
        f"Problems: {problem_str}. "
        f"Fix: {fix}"
    )


def add_case(
    case_id: str,
    query: str,
    problems: list[str],
    fix: str,
    tables: list[str],
    tenant: str = "",
) -> None:
    """Store a case in ChromaDB with descriptive embedding text."""
    col = _get_collection()
    text = _build_case_text(query, problems, fix, tables)
    metadata = {
        "query": query,
        "fix": fix,
        "tables": ",".join(tables),
        "problems": ",".join(problems),
    }
    if tenant:
        metadata["tenant"] = tenant

    col.upsert(
        ids=[case_id],
        documents=[text],
        metadatas=[metadata],
    )


def search_similar(
    query: str,
    problems: list[str] | None = None,
    n_results: int | None = None,
    min_similarity: float | None = None,
) -> list[dict]:
    """Search for similar cases. Returns list of cases with similarity scores.

    A vector search always returns its n nearest neighbours, however far away
    they are, so an unrelated query still comes back with a full set of
    results. Each case therefore carries a `low_confidence` flag: True when
    its similarity is below `min_similarity` (default config.RAG_MIN_SIMILARITY,
    calibrated in evals/eval_retrieval.py).

    Results are flagged rather than dropped, so a caller can still show a weak
    match clearly labelled as weak, and so the eval harness can see the raw
    distribution. Pass min_similarity=0.0 to flag nothing.
    """
    col = _get_collection()
    n = n_results or config.RAG_TOP_K
    threshold = (
        config.RAG_MIN_SIMILARITY if min_similarity is None else min_similarity
    )

    # Build search text from the query and its problems
    search_text = f"Query: {query}"
    if problems:
        search_text += f" Problems: {', '.join(problems)}"

    count = col.count()
    results = col.query(
        query_texts=[search_text],
        n_results=min(n, count) if count > 0 else 1,
    )

    if not results["ids"] or not results["ids"][0]:
        return []

    # Chroma types these as Optional and only fills the fields named in `include`.
    metadatas = results["metadatas"][0] if results["metadatas"] else []
    documents = results["documents"][0] if results["documents"] else []
    distances = results["distances"][0] if results["distances"] else []

    cases = []
    for i, case_id in enumerate(results["ids"][0]):
        meta = metadatas[i] if i < len(metadatas) else {}
        distance = distances[i] if i < len(distances) else None
        similarity = round(1 - distance, 4) if distance is not None else None
        cases.append({
            "case_id": case_id,
            "document": documents[i] if i < len(documents) else "",
            "query": _meta_str(meta, "query"),
            "fix": _meta_str(meta, "fix"),
            "tables": _meta_list(meta, "tables"),
            "problems": _meta_list(meta, "problems"),
            "distance": distance,
            "similarity": similarity,
            # An unknown similarity is treated as low confidence: better to
            # under-claim than to present an unscored neighbour as a match.
            "low_confidence": similarity is None or similarity < threshold,
        })

    return cases


def get_case_count() -> int:
    """Return the number of cases stored in ChromaDB."""
    return _get_collection().count()
