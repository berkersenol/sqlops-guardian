"""
SQLOps Guardian - Seed Cases
Populates ChromaDB with initial anti-pattern cases from cases/seed_cases.json.
Run: uv run python -m app.seed_cases
"""

import json
import logging
import os

from .rag import init_collection, add_case, get_case_count

logger = logging.getLogger(__name__)


def seed():
    """Load seed cases from JSON and insert into ChromaDB."""
    json_path = os.path.join(os.path.dirname(__file__), "..", "cases", "seed_cases.json")
    with open(json_path, "r") as f:
        cases = json.load(f)

    init_collection()

    for case in cases:
        add_case(
            case_id=case["case_id"],
            query=case["query"],
            problems=case["problems"],
            fix=case["fix"],
            tables=case["tables"],
        )

    count = get_case_count()
    # Logged, not printed: pipeline.init() calls this, and the MCP server
    # calls that on first use, where stdout carries the JSON-RPC frames.
    logger.info("Seeded %d cases into ChromaDB.", count)
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    seed()
