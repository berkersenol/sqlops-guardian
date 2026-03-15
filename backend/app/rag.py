"""
SQLOps Guardian - RAG Layer
ChromaDB vector search for similar SQL anti-pattern cases.
"""

import chromadb
from .config import config


_client: chromadb.ClientAPI | None = None
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
) -> list[dict]:
    """Search for similar cases. Returns list of cases with similarity scores."""
    col = _get_collection()
    n = n_results or config.RAG_TOP_K

    # Build search text from the query and its problems
    search_text = f"Query: {query}"
    if problems:
        search_text += f" Problems: {', '.join(problems)}"

    results = col.query(
        query_texts=[search_text],
        n_results=min(n, col.count()) if col.count() > 0 else 1,
    )

    if not results["ids"] or not results["ids"][0]:
        return []

    cases = []
    for i, case_id in enumerate(results["ids"][0]):
        meta = results["metadatas"][0][i]
        distance = results["distances"][0][i] if results["distances"] else None
        cases.append({
            "case_id": case_id,
            "document": results["documents"][0][i],
            "query": meta.get("query", ""),
            "fix": meta.get("fix", ""),
            "tables": meta.get("tables", "").split(","),
            "problems": meta.get("problems", "").split(","),
            "distance": distance,
            "similarity": round(1 - distance, 4) if distance is not None else None,
        })

    return cases


def get_case_count() -> int:
    """Return the number of cases stored in ChromaDB."""
    return _get_collection().count()
