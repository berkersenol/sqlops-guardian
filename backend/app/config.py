import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Groq / LLM
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    # Must be a chat model the account can actually access: "groq/compound"
    # is an agentic system, not a plain chat model, and returns
    # 404 model_not_found. `client.models.list()` shows what is available.
    LLM_MODEL: str = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "1000"))

    # ChromaDB / RAG
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")
    CHROMA_COLLECTION_NAME: str = os.getenv("CHROMA_COLLECTION_NAME", "sql_cases")
    RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "3"))
    # Minimum cosine similarity for a retrieved case to count as a match.
    #
    # 0.5 is calibrated against evals/golden_retrieval.json, not guessed. It is
    # the highest threshold that still keeps hit rate@3 at 100% (every correct
    # case is retrieved and survives) while rejecting all six negative queries
    # outright. The gap it sits in: the best score any no-precedent query
    # achieves is 0.480, and the worst correct match scores 0.538.
    #
    # A margin of ~0.03 either side is thin, so re-run the eval after changing
    # the seed cases, the embedding text in rag._build_case_text, or the
    # embedding model.
    #
    # Note that wrong results scoring above 0.5 do occur, but only alongside a
    # correct one: pos-function-upper also retrieves the other two
    # FUNCTION_ON_COLUMN cases at 0.53-0.59. Those are genuinely related cases
    # that the golden set simply does not label as the expected one, which is a
    # limit of single-ground-truth labelling rather than a leak in the
    # threshold. See the README's "Retrieval evaluation" section.
    RAG_MIN_SIMILARITY: float = float(os.getenv("RAG_MIN_SIMILARITY", "0.5"))

    # SQLite
    SQLITE_DB_PATH: str = os.getenv("SQLITE_DB_PATH", "./data/sqlops_guardian.db")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

    # Severity behavior — hardcoded defaults, env override optional
    BLOCK_ON: list[str] = (
        os.getenv("BLOCK_ON", "DELETE_WITHOUT_WHERE,UPDATE_WITHOUT_WHERE,DROP_TABLE")
        .split(",")
    )
    WARN_ON: list[str] = (
        os.getenv("WARN_ON", "SELECT_STAR,MISSING_LIMIT,LEADING_WILDCARD_LIKE")
        .split(",")
    )


config = Config()
