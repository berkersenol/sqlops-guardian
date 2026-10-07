# SQLOps Guardian

**SQL anti-pattern detection and optimization tool with a 4-layer analysis pipeline.**

[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18+-61DAFB.svg)](https://react.dev/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://docs.docker.com/compose/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-99%20passing-brightgreen.svg)]()

---

## What It Does

SQLOps Guardian analyzes SQL queries through a 4-layer pipeline and returns actionable optimization recommendations:

1. **Deterministic Linter** — 10 regex-based rules catch common anti-patterns instantly
2. **RAG (Retrieval-Augmented Generation)** — searches a ChromaDB vector store for similar past cases and known fixes
3. **LLM Analysis** — sends the query to Groq for deeper optimization insights, suggested indexes, and rewritten queries
4. **Operations** — logs every analysis to SQLite, tracks metrics, and accepts user feedback to improve RAG over time

If the LLM is unavailable (rate limits, network issues, API key missing), the system gracefully degrades — linter and RAG results are always returned.

---

## Architecture

```
                    ┌─────────────────────────────────┐
                    │         React Frontend          │
                    │    (Analyzer + Dashboard)       │
                    └──────────────┬──────────────────┘
                                   │ nginx reverse proxy
                    ┌──────────────▼──────────────────┐
                    │        FastAPI Backend          │
                    │                                 │
                    │  ┌──────────┐  ┌─────────────┐  │
                    │  │  Linter  │  │  RAG Engine │  │
                    │  │ (10 rules│  │  (ChromaDB) │  │
                    │  └────┬─────┘  └──────┬──────┘  │
                    │       │               │         │
                    │  ┌────▼───────────────▼──────┐  │
                    │  │       Pipeline            │  │
                    │  │  Linter → RAG → LLM → Log │  │
                    │  └────────────┬──────────────┘  │
                    │               │                 │
                    │  ┌────────────▼──────────────┐  │
                    │  │    LLM Analyzer (Groq)    │  │
                    │  └────────────┬──────────────┘  │
                    │               │                 │
                    │  ┌────────────▼──────────────┐  │
                    │  │  Case Store (SQLite)      │  │
                    │  │  Logging, Metrics,        │  │
                    │  │  Feedback                 │  │
                    │  └────────────────────────── ┘  │
                    └────────────────── ──────────────┘
                                   │
                    ┌──────────────▼──────────────────┐
                    │     Docker Volume               │
                    │  ├── sqlops_guardian.db         │
                    │  └── chroma_db/                 │
                    └─────────────────────────────────┘
```

---

## Screenshots

### Analyzer — Full Pipeline Output
<!-- TODO: Add screenshot of analyzer with LLM results -->
> Paste a SQL query → get lint findings, similar cases from RAG, and deep LLM analysis with suggested indexes and rewritten queries.

### Dashboard — Metrics & Pattern Distribution
<!-- TODO: Add screenshot of dashboard -->
> Track total analyses, pattern distribution, acceptance rate, and recent analysis history.

---

## Quick Start

### Option 1: Docker (recommended)

```bash
git clone https://github.com/YOUR_USERNAME/sqlops-guardian.git
cd sqlops-guardian

# Set up environment
cp .env.example .env
# Edit .env → add your Groq_API_KEY

# Build and run
docker-compose up --build
```

- **Frontend:** http://localhost:3000
- **Backend API:** http://localhost:8000
- **API Docs:** http://localhost:8000/docs

### Option 2: Manual (development)

```bash
# Backend
cd backend
uv sync
uv run python main.py
# API running on http://localhost:8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
# Dev server on http://localhost:3000
```

---

## API Reference

| Method | Endpoint    | Description                                       |
|--------|-------------|---------------------------------------------------|
| POST   | `/analyze`  | Submit a SQL query → returns full analysis report |
| POST   | `/feedback` | Submit accept/reject feedback on an analysis      |
| GET    | `/metrics`  | Aggregated stats: total analyses, pattern counts  |
| GET    | `/recent`   | Recent analyses (supports `?limit=N`)             |
| GET    | `/health`   | Backend health check with DB and RAG status       |

### Example: Analyze a Query

```bash
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT * FROM orders WHERE EXTRACT(YEAR FROM created_at) = 2025;"}'
```

**Response includes:**
- `lint_findings` — deterministic rule violations with severity and fix suggestions
- `similar_cases` — matching cases from the RAG knowledge base with similarity scores
- `llm_analysis` — deep analysis with risk assessment, suggested indexes, and rewritten query
- `severity_summary` — count of findings by severity level
- `analysis_id` — unique ID for feedback and tracking

---

## Detection Rules

| Rule                     | Severity   | What It Catches                                         |
|--------------------------|------------|-------------------------------------------------------- |
| `DELETE_WITHOUT_WHERE`   | CRITICAL   | DELETE statements missing a WHERE clause                |
| `UPDATE_WITHOUT_WHERE`   | CRITICAL   | UPDATE statements missing a WHERE clause                |
| `DROP_TABLE`             | CRITICAL   | DROP TABLE statements (destructive operations)          |
| `FUNCTION_ON_COLUMN`     | HIGH       | Functions wrapping columns in WHERE (breaks SARGability)|
| `LEFT_JOIN_WHERE_TRAP`   | HIGH       | WHERE conditions that nullify LEFT JOIN behavior        |
| `SELECT_STAR`            | MEDIUM     | SELECT * instead of specific columns                    |
| `LEADING_WILDCARD_LIKE`  | MEDIUM     | LIKE '%pattern' preventing index usage                  | 
| `NOT_IN_SUBQUERY`        | MEDIUM     | NOT IN with subqueries (NULL-unsafe, slow)              |
| `OR_ACROSS_COLUMNS`      | MEDIUM     | OR conditions across different columns                  |
| `MISSING_LIMIT`          | LOW        | SELECT without LIMIT on unbounded queries               |

---

## Tech Stack

**Backend:** Python 3.12 · FastAPI · Pydantic · sqlglot · ChromaDB · Groq API · SQLite · uv

**Frontend:** React 18 · Vite · Tailwind CSS · Recharts · react-markdown

**Infrastructure:** Docker · Docker Compose · Nginx · GitHub Actions

---

## Project Structure

```
sqlops-guardian/
├── backend/
│   ├── app/
│   │   ├── api.py              # FastAPI routes + CORS
│   │   ├── config.py           # Environment config via .env
│   │   ├── linter.py           # 10 deterministic SQL rules (sqlglot syntax tree)
│   │   ├── linter_regex.py     # Regex rules, fallback when parsing fails
│   │   ├── rag.py              # ChromaDB vector search
│   │   ├── llm_analyzer.py     # Groq integration
│   │   ├── pipeline.py         # Orchestrator: Linter → RAG → LLM → Log
│   │   ├── case_store.py       # SQLite operations layer
│   │   ├── models.py           # Pydantic models
│   │   └── seed_cases.py       # Seed data loader
│   ├── tests/                  # 99 pytest tests
│   ├── evals/                  # Linter evaluation harness + golden set
│   ├── cases/                  # Seed case data
│   ├── samples/                # Example SQL files
│   └── main.py                 # Entry point
├── frontend/
│   ├── src/
│   │   ├── api/client.js       # API client
│   │   ├── components/         # React components
│   │   └── pages/              # Analyzer + Dashboard
│   └── vite.config.js
├── docker-compose.yml
├── Dockerfile.backend
├── Dockerfile.frontend
├── nginx.conf
├── .env.example
└── CLAUDE.md
```

---

## Configuration

All configuration is managed through environment variables (`.env` file):

```env
# Required
Groq_API_KEY=your-Groq-api-key

# Optional (defaults shown)
LLM_MODEL=groq/compound
LLM_MAX_TOKENS=4096
CHROMA_PERSIST_DIR=./data/chroma_db
SQLITE_DB_PATH=./data/sqlops_guardian.db
LOG_LEVEL=INFO
```

---

## Running Tests

```bash
cd backend
uv run pytest tests/ -v
```

99 tests covering the pipeline, RAG integration, API endpoints, LLM analyzer, and case store.
The Groq client is mocked throughout, so the suite needs no API key and makes no network calls.
Each test gets its own temporary SQLite file and ChromaDB directory, so runs never touch real data.

---

## Evaluation

The deterministic linter is evaluated separately from the LLM. The linter is the
only layer with a single correct answer — same query, same findings, every time —
so it can be scored by exact comparison against labeled data. It is also the layer
that still works when Groq is unreachable, so its score is a direct measurement of
the system's worst-case behaviour.

### The golden set

`backend/evals/golden_linter.json` holds 32 hand-labeled queries in four categories:

| Category | Cases | What it covers |
|---|---|---|
| `clean` | 9 | Queries that must produce **no** findings — paginated selects, `DELETE`/`UPDATE` with a `WHERE`, `COUNT(*)`, `NOT IN` over a literal list, `OR` on one column, and the correct `LEFT JOIN ... IS NULL` anti-join |
| `single` | 12 | One rule each, covering all 10 rules plus `DROP TABLE IF EXISTS` and `ILIKE` variants |
| `multi` | 3 | Queries that must trigger two or three rules at once |
| `tricky` | 8 | Cases where the naive textual reading and the correct answer disagree: SQL inside a string literal or a comment, a function wrapping a constant instead of a column, `ORDER BY` inside a window function, `AS` aliases, a table alias in `UPDATE`, multi-statement input, and quoted identifiers |

Scoring is per rule: **precision** ("when the linter speaks up, how often is it
right?") and **recall** ("of the real problems, how many did it catch?"), plus an
exact-match rate per category — a case counts only if the set of rules fired equals
the labeled set exactly.

### Why there are two sets

`golden_linter.json` (32 cases) is the **main set**. The sqlglot rules were developed
against it, one rule at a time, with the eval re-run after each. That makes it the
right thing to gate CI on, but it also means a perfect score on it is partly a
measure of fitting that particular set — the cases were in front of us while the code
was being written.

`golden_linter_holdout.json` (13 cases) is the **held-out set**, written independently
and never consulted while the rules were being built. It exists to answer a different
question: do the rules generalise to SQL they were not tuned on? It deliberately
probes constructs absent from the main set — a CTE with a star in the outer query,
`SELECT *` inside `EXISTS`, `TRIM` (a function the old regex list never knew about),
a block comment before a real `DROP`, `LIMIT ... OFFSET`, a trailing-wildcard `LIKE`,
and a `LEFT JOIN` whose filter correctly sits in the `ON` clause.

The split is what makes the headline number trustworthy. A high score on the set you
developed against can mean the rules are correct, or merely that they were shaped to
fit; only a set held back can tell those apart. It earned its keep immediately — see
below.

### Running it

```bash
cd backend

# main set (default)
uv run python -m evals.eval_linter

# held-out set
uv run python -m evals.eval_linter --golden evals/golden_linter_holdout.json

# fail (exit 1) if overall F1 drops below a threshold -- this is what CI gates on
uv run python -m evals.eval_linter --min-f1 0.95
```

`--golden` selects the set; the result filename is derived from it, so one set never
overwrites another's output.

Results land in `backend/evals/results/`, which is gitignored apart from committed
baselines so runs can be compared over time.

### Results: regex vs. sqlglot

The linter originally matched raw text with regular expressions. It now parses
each query into a sqlglot syntax tree, falling back to the regex rules only when
parsing fails. Both runs are committed, so the comparison is reproducible:
`linter_baseline_regex.json` and `linter_sqlglot.json` in `backend/evals/results/`.

| | regex | sqlglot |
|---|---|---|
| Precision | 0.83 | **1.00** |
| Recall | 0.83 | **1.00** |
| F1 | 0.83 | **1.00** |
| False positives | 4 | **0** |
| False negatives | 4 | **0** |

Exact-match rate by category:

| Category | regex | sqlglot |
|---|---|---|
| `clean` | 9/9 | 9/9 |
| `single` | 12/12 | 12/12 |
| `multi` | 3/3 | 3/3 |
| `tricky` | **0/8** | **8/8** |

Per rule, precision / recall:

| Rule | regex | sqlglot |
|---|---|---|
| `DELETE_WITHOUT_WHERE` | 0.50 / 0.33 | 1.00 / 1.00 |
| `UPDATE_WITHOUT_WHERE` | 1.00 / 0.50 | 1.00 / 1.00 |
| `LEFT_JOIN_WHERE_TRAP` | 1.00 / 0.50 | 1.00 / 1.00 |
| `DROP_TABLE` | 0.67 / 1.00 | 1.00 / 1.00 |
| `FUNCTION_ON_COLUMN` | 0.75 / 1.00 | 1.00 / 1.00 |
| `MISSING_LIMIT` | 0.75 / 1.00 | 1.00 / 1.00 |
| `SELECT_STAR` | 1.00 / 1.00 | 1.00 / 1.00 |
| `LEADING_WILDCARD_LIKE` | 1.00 / 1.00 | 1.00 / 1.00 |
| `NOT_IN_SUBQUERY` | 1.00 / 1.00 | 1.00 / 1.00 |
| `OR_ACROSS_COLUMNS` | 1.00 / 1.00 | 1.00 / 1.00 |

Four rules were already perfect on this set and stayed perfect — the gain is
concentrated in the six that depended on reading text as structure.

### Held-out results

On the 13 held-out cases the tree-based linter scores **13/13, precision 1.00,
recall 1.00, F1 1.00** (`linter_holdout.json`).

It did not start there. The first held-out run scored 12/13 and exposed a real bug:
`EXISTS (SELECT * FROM users u WHERE u.id = o.user_id)` was reported as
`FUNCTION_ON_COLUMN`. The cause is that sqlglot models `EXISTS` as an `exp.Func`
subclass, and the rule searched the whole function subtree for a column — so the
subquery's correlated columns were attributed to `EXISTS` itself, as though it were
a function applied to them.

The fix was to collect columns **per query scope**, stopping at nested `SELECT` and
`Subquery` boundaries: a function only wraps a column if the column is one of its own
arguments. That is the general form of an earlier narrower fix (`AND`/`OR` are also
`exp.Func` subclasses and had to be excluded by type), and it resolves the `EXISTS`
case without a special case for `EXISTS`. The main set stayed at 32/32 throughout.

This is exactly the kind of defect a held-out set exists to find: every construct
involved was absent from the main 32, so no amount of re-running that set would have
surfaced it.

### Why the tree fixes the tricky cases

All eight tricky failures had one root cause: the old rules matched text, so they
could not tell code from a comment or a string, and had no notion of statement
boundaries. Parsing removes the ambiguity rather than patching around it:

- **Comments are dropped during parsing**, so `-- DROP TABLE users` yields no
  `Drop` node at all.
- **String contents become opaque literals**, so `'DELETE FROM users'` is data.
- **Each statement is checked on its own**, so a `WHERE` in statement two cannot
  make a bare `DELETE` in statement one look safe.
- **Alias syntax is normalised**, so `UPDATE orders o SET`, `UPDATE orders AS o
  SET`, and a quoted `"user accounts"` table are all recognised. The old regex
  required `UPDATE <word> SET` and silently analysed nothing otherwise.
- **A window function's `ORDER BY` lives in its own `Window` node**, so it never
  appears in the query's top-level `order` and no longer reads as a missing `LIMIT`.
- **Function arguments are inspectable**, so `DATE('2025-01-01')` (a function on a
  constant) is distinguishable from `UPPER(name)` (a function on a column).

Two refinements fell out of the rewrite:

- `FUNCTION_ON_COLUMN` no longer needs a hardcoded list of six function names. Any
  function applied to a column is caught, which is strictly broader than before.
- `LEFT_JOIN_WHERE_TRAP` identifies the joined table by its alias when it has one
  and by its table name when it does not, so `LEFT JOIN orders o`, `LEFT JOIN
  orders AS o`, and `LEFT JOIN orders` are all covered. `IS NULL` / `IS NOT NULL`
  remain exempt, since that is the deliberate anti-join idiom.

### Graceful degradation

If `sqlglot.parse()` raises a `ParseError`, the linter logs a warning and falls back
to the regex rules in `app/linter_regex.py` for that query, so a syntactically
invalid query still gets best-effort findings instead of none:

```
DROP TABLE users ((( GARBAGE      -> DROP_TABLE
DELETE FROM users GROUP ORDER (((  -> DELETE_WITHOUT_WHERE
UPDATE t SET x = 1 ((( ???         -> UPDATE_WITHOUT_WHERE
```

---

## Design Decisions

- **4-layer pipeline** — each layer adds value independently. If the LLM is down, you still get linter + RAG results. This graceful degradation pattern is critical for production reliability.
- **ChromaDB for RAG** — lightweight, embedded vector database that runs without external infrastructure. Cases build up as users submit feedback, making the system smarter over time.
- **SQLite for operations** — zero-config, file-based database that persists via Docker volumes. Perfect for logging, metrics, and feedback without adding database infrastructure.
- **Docker volumes for persistence** — analysis history and RAG knowledge base survive container rebuilds. Data lives in `/app/data/`, separated from application code.

---

## License

MIT — see [LICENSE](LICENSE) for details.
