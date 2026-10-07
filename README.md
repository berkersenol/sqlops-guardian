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

## MCP Server

The same analysis layers are exposed over the [Model Context Protocol](https://modelcontextprotocol.io),
so SQLOps Guardian can be used directly from any MCP host — Claude Desktop,
Cursor, or Claude Code — without the REST API or the frontend running.

`backend/mcp_server.py` is an adapter, not a reimplementation: each tool calls
the same functions the REST API uses.

### Tools

| Tool | Cost | Reaches the network | Writes |
|------|------|---------------------|--------|
| `lint_sql(query)` | Free, milliseconds, deterministic | No | No |
| `search_similar_cases(query, top_k=3)` | Free, local embeddings | No | No |
| `analyze_sql(query)` | Groq API tokens, seconds | Yes (Groq) | Appends to the SQLite analysis log |

`lint_sql` is the default choice for "is this query OK?" — it is the sqlglot
rule engine alone, so it needs no API key and returns identical findings for
identical input. `analyze_sql` adds the RAG and LLM layers on top, returning
suggested indexes, a rewritten query and an explanation.

`search_similar_cases` separates genuine matches from weak ones rather than
returning whatever happens to be nearest: `cases` holds results at or above
`RAG_MIN_SIMILARITY`, `weak_matches` holds the rest, and an empty `cases` list
is a real answer — the knowledge base holds no precedent. It also feeds the
linter's rule names into the search, which is what makes retrieval accurate
enough for that distinction to hold; see
[Retrieval evaluation](#retrieval-evaluation).

Each tool is declared with MCP **tool annotations** (`readOnlyHint`,
`destructiveHint`, `openWorldHint`) that hosts use to decide how much friction
to put in front of a call: the two read-only tools can be approved more
freely, while `analyze_sql` is marked as writing (it appends to the analysis
log), non-destructive (it only ever adds history), and open-world (the query
text is sent to Groq).

**No tool executes the SQL it is given.** Queries are parsed by sqlglot and
embedded as text; nothing connects to a database with them.

### Claude Desktop (Windows)

Edit `%APPDATA%\Claude\claude_desktop_config.json` — create it if it does not
exist — and add:

```json
{
  "mcpServers": {
    "sqlops-guardian": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\work_projects\\sqlops-guardian-main\\backend",
        "python",
        "mcp_server.py"
      ],
      "env": {
        "GROQ_API_KEY": "gsk_your_key_here"
      }
    }
  }
}
```

Notes:

- **Paths need doubled backslashes** (`C:\\work_projects\\...`). JSON treats a
  single `\` as an escape character, so a Windows path pasted in raw is
  invalid JSON and the server will silently fail to appear.
- `uv run --directory <path>` is what makes the project's virtualenv active
  regardless of where Claude Desktop launches the process from. If `uv` is not
  on the system PATH, use its full path (`C:\\Users\\you\\.local\\bin\\uv.exe`).
- `GROQ_API_KEY` is only needed for `analyze_sql`. Without it `lint_sql` and
  `search_similar_cases` work normally, and `analyze_sql` still returns its
  deterministic lint findings with a `degraded` note explaining that the LLM
  layer was skipped. The server also reads `backend/.env`, so the `env` block
  can be omitted if the key is already there.
- Restart Claude Desktop fully after editing the file. The tools appear under
  the tools icon in the message box.

The first call to `search_similar_cases` or `analyze_sql` creates and seeds the
local ChromaDB store, which downloads the embedding model (~80MB) once. That
happens on first use rather than at startup, so it cannot stall the connection
handshake. `lint_sql` needs neither store and is fast immediately.

### Testing with the MCP Inspector

The Inspector is the fastest way to confirm the server works without involving
a model — it shows the raw JSON-RPC traffic, lists the advertised tools and
their schemas, and lets you call them by hand. It needs Node.js, not a Python
install:

```powershell
# From the backend directory
cd C:\work_projects\sqlops-guardian-main\backend

# Launch the Inspector wrapping the server over stdio
npx @modelcontextprotocol/inspector uv run python mcp_server.py
```

It prints a `http://localhost:6274` URL with a pre-filled session token — open
it, press **Connect**, then **List Tools**. Useful things to try:

- `lint_sql` with `SELECT * FROM orders` → one `SELECT_STAR` finding,
  `overall_severity: MEDIUM`.
- `lint_sql` with an empty `query` → `isError: true` and a message naming the
  argument, rather than a dropped connection.
- `search_similar_cases` with `{"query": "SELECT * FROM orders WHERE YEAR(created_at) = 2024"}`
  → `sarg-extract-date` as a match at ~0.81 similarity.
- `search_similar_cases` with `{"query": "slow scan"}` → an empty `cases` list
  and three `weak_matches` at ~0.43, labelled rather than passed off as
  precedents.
- `analyze_sql` without `GROQ_API_KEY` set → lint findings plus a `degraded`
  entry explaining the LLM layer was skipped.

Server logs appear in the Inspector's stderr pane. That is deliberate:
**under stdio transport, stdout carries the JSON-RPC frames**, so anything
printed there corrupts the protocol stream. All logging in `mcp_server.py` is
configured to stderr.

To run the server directly without the Inspector (it will wait on stdin for
JSON-RPC frames, which is expected):

```powershell
cd C:\work_projects\sqlops-guardian-main\backend
uv run python mcp_server.py
```

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

**Backend:** Python 3.12 · FastAPI · Pydantic · sqlglot · ChromaDB · Groq API · SQLite · MCP Python SDK · uv

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
│   │   ├── seed_cases.py       # Seed data loader
│   │   └── serialization.py    # Domain models -> JSON (shared by API and MCP)
│   ├── mcp_server.py           # MCP server over stdio (3 tools)
│   ├── tests/                  # pytest suite
│   ├── evals/                  # Linter + retrieval eval harnesses and golden sets
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
RAG_TOP_K=3
RAG_MIN_SIMILARITY=0.5
LOG_LEVEL=INFO
```

`RAG_MIN_SIMILARITY` is the cut-off below which a retrieved case is flagged
low-confidence rather than presented as a match. The default is calibrated
against a golden set, not chosen by feel — raising it to 0.6 discards about a
third of genuine matches. See [Retrieval evaluation](#retrieval-evaluation)
before changing it.

---

## Running Tests

```bash
cd backend
uv run pytest tests/ -v
```

262 tests covering the pipeline, RAG integration, API endpoints, LLM analyzer, case store, MCP server, seed data, and the retrieval eval harness.
The Groq client is mocked throughout, so the suite needs no API key and makes no network calls.
Each test gets its own temporary SQLite file and ChromaDB directory, so runs never touch real data.

---

## Evaluation

Two layers are evaluated against labeled data: the deterministic **linter**, and
**retrieval** from the RAG knowledge base. Both are scored because both have a
checkable right answer and neither depends on the LLM — so together they measure
what the system does when Groq is unreachable. The LLM layer itself is not scored
here.

The linter has a single correct answer — same query, same findings, every time —
so it is scored by exact comparison. Retrieval is scored by whether the right
seed case comes back, and is also what calibrates the similarity threshold.

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

### Retrieval evaluation

A vector search always returns its *n* nearest neighbours, however far away
they are. Searching for `"slow scan"` returned three unrelated cases at ~0.43
similarity, shaped exactly like genuine matches — nothing in the response said
they were weak.

The fix is a minimum similarity (`RAG_MIN_SIMILARITY`, default **0.5**), below
which a result is flagged `low_confidence` instead of being presented as a
match. The threshold is calibrated against a golden set rather than guessed.

**The golden set** (`evals/golden_retrieval.json`) is 13 positives — a query
plus the seed `case_id` that should be retrieved — and 6 negatives, queries with
no legitimate precedent (`"slow scan"`, `VACUUM ANALYZE orders;`, a connection
pool question). The negatives are what make the exercise meaningful: with
positives only, every threshold below the lowest correct score scores perfectly,
and the data would always favour a threshold of 0.

**The first finding was about the search text, not the threshold.** Cases are
indexed as a description — `"Query on orders. Problems: FUNCTION_ON_COLUMN,
SELECT_STAR. Fix: ..."` — while the MCP tool was searching with raw SQL alone.
That asymmetry depressed every score. Passing the linter's rule names alongside
the query (free, local, deterministic — the linter already runs) aligns the
query with the indexed `Problems:` field:

| Search shape | hit rate@1 | hit rate@3 | worst correct score |
|---|---|---|---|
| Query only | 62% | 85% | 0.329 |
| Query + lint rule names | 77% | **100%** | **0.538** |

Without that change no threshold works at all: correct matches ran as low as
0.329 while incorrect ones reached 0.480, so any cut-off that removed the noise
also removed real matches.

**The threshold sweep**, on the aligned search shape. `wrong suppressed` is the
share of incorrect results falling below the cut-off; `negatives rejected` is
the share of no-precedent queries returning no match at all:

| Threshold | hit rate@3 | wrong suppressed | negatives rejected |
|---|---|---|---|
| 0.40 | 100% | 34% | 67% |
| 0.45 | 100% | 55% | 83% |
| **0.50** | **100%** | **68%** | **100%** |
| 0.55 | 92% | 86% | 100% |
| 0.60 | 69% | 95% | 100% |
| 0.65 | 54% | 100% | 100% |

**0.5 is the highest threshold that costs no hit rate**, and it is also the
first that rejects every negative query outright. It sits in a real gap: the
best score any no-precedent query achieves is **0.480**, and the worst correct
match scores **0.538**.

The originally proposed default of 0.6 would have been a bad choice — it keeps
only 69% of correct matches, discarding four genuine precedents to suppress
noise that 0.5 already handles.

Two caveats worth keeping in mind:

- **The margin is thin** (~0.03 either side). Re-run the eval after changing the
  seed cases, the embedding text in `rag._build_case_text`, or the embedding
  model. CI gates on hit rate@3 = 1.0, and
  `tests/test_eval_retrieval.py` asserts both edges of the gap.
- **Incorrect results above 0.5 do occur, but only beside a correct one.** A
  query about `UPPER(col)` also retrieves the `EXTRACT()` and `LOWER()` cases at
  0.53–0.59. Those are genuinely related — all three are `FUNCTION_ON_COLUMN`
  cases — and are "incorrect" only because the golden set labels a single
  expected case per query. That is a limit of single-ground-truth labelling, not
  a leak in the threshold, and a test pins the distinction: no query that
  retrieves *no* correct case may produce a result above the threshold.

**Where weak results surface.** They are flagged, not dropped, and each layer
makes its own choice:

- `rag.search_similar` flags every result and drops nothing, so callers decide
  and the eval can read the raw distribution.
- `pipeline.analyze` keeps only genuine matches, because `similar_cases` goes
  into the LLM prompt as "similar past cases" — handing the model an unrelated
  precedent invites it to reason from it.
- The MCP `search_similar_cases` tool returns both, separated: `cases` for real
  matches and `weak_matches` for the rest, plus the `min_similarity` applied.
  Showing retrieval quality is that tool's job.

**Running it:**

```bash
cd backend
uv run python -m app.seed_cases                      # the eval needs a seeded store

uv run python -m evals.eval_retrieval                # report + JSON to evals/results/
uv run python -m evals.eval_retrieval --query-only   # without the lint rule names
uv run python -m evals.eval_retrieval --top-k 5
uv run python -m evals.eval_retrieval --min-hit-rate 1.0   # exit 1 below that (CI)
```

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
