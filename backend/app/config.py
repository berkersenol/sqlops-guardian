import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Groq / LLM
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "1000"))

    # ChromaDB / RAG
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")
    CHROMA_COLLECTION_NAME: str = os.getenv("CHROMA_COLLECTION_NAME", "sql_cases")
    RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "3"))

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
