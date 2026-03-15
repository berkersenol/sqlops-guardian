# SQLOps Guardian

## Project Overview
SQL analysis tool that catches anti-patterns and suggests optimizations.
4-layer architecture:
1. Deterministic Linter (built) - regex-based SQL anti-pattern detection
2. RAG with ChromaDB - search similar past cases
3. LLM Analysis with Gemini - deeper optimization suggestions
4. Operations - SQLite logging, feedback, metrics

## Tech Stack
- Python 3.11+
- uv for package management
- Pydantic for data modelsYes
- FastAPI for REST API
- ChromaDB for vector database
- Google Gemini API for LLM
- SQLite for logging and metrics
- python-dotenv for config

## Project Structure
- models.py: Pydantic data models (LintFinding, AnalysisReport, Severity)
- linter.py: 10 deterministic SQL detection rules
- cli.py: Command line interface + test suite
- config.py: Environment config loaded from .env
- rag.py: ChromaDB integration (to build)
- llm_analyzer.py: Gemini integration (to build)
- case_store.py: SQLite logging (to build)
- api.py: FastAPI endpoints (to build)

## Conventions
- Use Pydantic models for all data structures
- Every new feature needs a test in cli.py or tests/
- Config comes from .env via config.py, never hardcoded
- Each file has one responsibility
- LLM failure should never break the system - graceful degradation

## Current Status
Step A: Config + environment - IN PROGRESS
Step B: SQLite logging - TODO
Step C: RAG with ChromaDB - TODO
Step D: LLM analyzer - TODO
Step E: Wire pipeline together - TODO
Step F: FastAPI endpoints - TODO