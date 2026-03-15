# SQLOps Guardian — Restructure + Frontend + Docker Prompt (for Claude Code)

Paste this into Claude Code. Work through each phase in order. Verify each step works before moving on.

---

## Context

SQLOps Guardian is a SQL analysis tool with a 4-layer architecture (Linter → RAG → LLM → Operations). The backend is fully built with 111+ tests. Now I need to:

1. Restructure the project from a flat layout into a proper monorepo
2. Build a React frontend
3. Add Docker support
4. Prepare for GitHub

I'm on Windows with PowerShell. I use `uv` for Python and `npm` for JS.

---

## PHASE 0: Project Restructure

The project is currently flat — all .py files in root, tests in root, everything mixed together. Restructure into a clean monorepo layout.

### Target structure:

```
sqlops-guardian/
│
├── backend/
│   ├── app/
│   │   ├── __init__.py          ← makes it a Python package
│   │   ├── api.py
│   │   ├── cli.py
│   │   ├── config.py
│   │   ├── case_store.py
│   │   ├── linter.py
│   │   ├── llm_analyzer.py
│   │   ├── models.py
│   │   ├── pipeline.py
│   │   ├── rag.py
│   │   └── seed_cases.py
│   ├── cases/
│   │   └── seed_cases.json
│   ├── samples/
│   │   ├── clean_query.sql
│   │   ├── dangerous_delete.sql
│   │   └── slow_dashboard.sql
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py          ← shared fixtures (if needed)
│   │   ├── test_api.py
│   │   ├── test_case_store.py
│   │   ├── test_llm_analyzer.py
│   │   ├── test_pipeline.py
│   │   └── test_rag.py
│   ├── main.py                  ← entry point: uvicorn app.api:app
│   ├── pyproject.toml
│   └── uv.lock
│
├── frontend/                    ← built in Phase 2
│   └── ...
│
├── docker-compose.yml           ← built in Phase 3
├── Dockerfile.backend
├── Dockerfile.frontend
├── .env.example
├── .gitignore
├── CLAUDE.md
├── LICENSE                      ← MIT
└── README.md
```

### Step 0.1: Create directories and move files

Create the directory structure and move files into place:

```
backend/app/       ← move all .py source files here (api.py, linter.py, models.py, etc.)
backend/tests/     ← move all test_*.py files here
backend/cases/     ← move cases/seed_cases.json here
backend/samples/   ← move *.sql files here
```

Move `pyproject.toml` and `uv.lock` into `backend/`.

Keep in the project root: `.env.example`, `.gitignore`, `CLAUDE.md`, `README.md`

Delete from root after moving: the original .py files, test files, .sql files, cases/ folder.

### Step 0.2: Create `backend/app/__init__.py`

Empty file or with a version string:
```python
"""SQLOps Guardian — SQL Anti-Pattern Detection & Optimization."""
```

### Step 0.3: Create `backend/tests/__init__.py`

Empty file.

### Step 0.4: Fix all imports

This is the critical step. Every Python file that imports from a sibling module needs updating.

**Pattern:** All `from module import ...` or `import module` references between project files must become `from app.module import ...`

Examples of changes needed:
```python
# BEFORE (in pipeline.py)
from linter import lint_sql
from rag import search_similar, add_case
from llm_analyzer import analyze_with_llm
from case_store import log_analysis
from models import AnalysisReport

# AFTER (in backend/app/pipeline.py)
from app.linter import lint_sql
from app.rag import search_similar, add_case
from app.llm_analyzer import analyze_with_llm
from app.case_store import log_analysis
from app.models import AnalysisReport
```

Do this for EVERY .py file in `backend/app/`. Also update all test files in `backend/tests/` — they should import from `app.*`.

**Files that need import updates (check all of these):**
- `app/api.py` — imports from models, pipeline, case_store, rag, config
- `app/pipeline.py` — imports from linter, rag, llm_analyzer, case_store, models
- `app/llm_analyzer.py` — imports from models, config
- `app/case_store.py` — imports from models, config
- `app/rag.py` — imports from config
- `app/seed_cases.py` — imports from rag
- `app/cli.py` — imports from linter, pipeline, rag, case_store, seed_cases
- `app/config.py` — may import from models or use .env path (check path references)
- All test files — import from app.*

### Step 0.5: Fix path references

Some files reference relative paths (like `cases/seed_cases.json`, SQLite DB path, ChromaDB path). These need to work relative to `backend/` now.

**config.py** — if it loads `.env`, the path should look for `.env` in the project root (parent of backend/) or in backend/. Check how `python-dotenv` finds it and adjust. Recommended: look for `.env` in project root.

**seed_cases.py** — references `cases/seed_cases.json`. Update to work from `backend/` directory.

**case_store.py** — SQLite DB path. Should default to `backend/sqlops_guardian.db` or be configurable via .env.

**rag.py** — ChromaDB persist directory. Should default to `backend/chroma_db/` or be configurable via .env.

### Step 0.6: Create `backend/main.py`

```python
"""Entry point for SQLOps Guardian API."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.api:app", host="0.0.0.0", port=8000, reload=True)
```

### Step 0.7: Update `backend/pyproject.toml`

Make sure the package configuration points to the `app` package. Update any script entry points.

### Step 0.8: Verify everything works

From the `backend/` directory:
```powershell
# Run all tests
uv run pytest tests/ -v

# Start the API
uv run python main.py

# Test an endpoint
# (from another terminal)
curl -X POST http://localhost:8000/analyze -H "Content-Type: application/json" -d '{"query": "SELECT * FROM users"}'
```

**ALL 111+ TESTS MUST PASS before moving to Phase 1.** If imports are broken, fix them. Do not proceed with failing tests.

### Step 0.9: Update .gitignore

Make sure .gitignore covers:
```
# Environment
.env
.venv/
backend/.venv/

# Data (generated at runtime)
backend/chroma_db/
backend/test_chroma_db/
backend/sqlops_guardian.db
backend/*.db

# Python
__pycache__/
*.pyc
.pytest_cache/

# Node
frontend/node_modules/
frontend/dist/

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db
```

---

## PHASE 1: Backend CORS Setup

Before building the frontend, add CORS middleware to `backend/app/api.py`:

```python
from fastapi.middleware.cors import CORSMiddleware

# Add after app = FastAPI(...)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",    # Vite dev server
        "http://localhost:5173",    # Vite default port
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Verify the backend still starts and tests still pass after this change.

---

## PHASE 2: React Frontend

### Step 2.1: Scaffold

From the project root:
```powershell
npm create vite@latest frontend -- --template react
cd frontend
npm install
npm install -D tailwindcss @tailwindcss/vite
npm install recharts react-markdown
```

Configure `frontend/vite.config.js`:
```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3000,
    proxy: {
      '/analyze': 'http://localhost:8000',
      '/feedback': 'http://localhost:8000',
      '/metrics': 'http://localhost:8000',
      '/recent': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    }
  }
})
```

Add Tailwind to `src/index.css`:
```css
@import "tailwindcss";
```

Verify: `npm run dev` shows a page on localhost:3000.

### Step 2.2: API Client

Create `src/api/client.js` — wraps all backend calls. Every component imports from here.

```js
const BASE = '';  // empty string = same origin, Vite proxy handles routing in dev

export async function analyzeSQL(query) {
  const res = await fetch(`${BASE}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  });
  if (!res.ok) throw new Error(`Analysis failed: ${res.status}`);
  return res.json();
}

export async function submitFeedback(analysisId, accepted, comments = '') {
  const res = await fetch(`${BASE}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ analysis_id: analysisId, accepted, comments }),
  });
  if (!res.ok) throw new Error(`Feedback failed: ${res.status}`);
  return res.json();
}

export async function getMetrics() {
  const res = await fetch(`${BASE}/metrics`);
  if (!res.ok) throw new Error(`Metrics failed: ${res.status}`);
  return res.json();
}

export async function getRecent(limit = 20) {
  const res = await fetch(`${BASE}/recent?limit=${limit}`);
  if (!res.ok) throw new Error(`Recent failed: ${res.status}`);
  return res.json();
}

export async function getHealth() {
  const res = await fetch(`${BASE}/health`);
  if (!res.ok) throw new Error(`Health failed: ${res.status}`);
  return res.json();
}
```

### Step 2.3: App Shell

`src/App.jsx` — navigation with two tabs: Analyzer and Dashboard.

- Dark theme: `bg-gray-900` background, `text-gray-100` text
- Top nav bar with "SQLOps Guardian" title on left, tab buttons on right
- Health indicator: green dot = healthy, red dot = backend down. Call GET /health on mount.
- State-based tab switching: `const [activeTab, setActiveTab] = useState('analyzer')`
- Render `<Analyzer />` or `<Dashboard />` based on active tab

### Step 2.4: Analyzer Page (HERO FEATURE)

File: `src/pages/Analyzer.jsx`

This manages the state for a full analysis cycle. Layout:

```
┌──────────────────────────────────────────────────┐
│  SQL Editor (textarea + Analyze button)           │
│  [Sample Queries dropdown]                        │
├────────────────────────┬─────────────────────────┤
│  Lint Findings         │  Similar Cases           │
│  (sorted by severity)  │  (from RAG)              │
├────────────────────────┴─────────────────────────┤
│  LLM Deep Analysis (rendered markdown)            │
├──────────────────────────────────────────────────┤
│  Feedback: [✓ Accept] [✗ Reject] + comment box   │
└──────────────────────────────────────────────────┘
```

State: `query`, `report` (null until analysis), `loading`, `feedbackSent`

**AnalysisReport shape from POST /analyze:**
```json
{
  "query": "SELECT * FROM users WHERE LOWER(email) = 'test@example.com';",
  "lint_findings": [
    {
      "rule_name": "SELECT_STAR",
      "severity": "MEDIUM",
      "message": "Avoid SELECT * — list specific columns",
      "position": { "line": 1, "column": 0 },
      "suggestion": "Replace * with specific column names"
    }
  ],
  "similar_cases": [
    {
      "case_id": "sarg-lower-email",
      "query": "SELECT id, name FROM customers WHERE LOWER(email) = 'john@example.com';",
      "problems": ["FUNCTION_ON_COLUMN"],
      "fix": "Added a functional index on LOWER(email)...",
      "similarity": 0.92
    }
  ],
  "llm_analysis": "## Analysis\n\nThis query has two issues...",
  "severity_summary": { "CRITICAL": 0, "HIGH": 1, "MEDIUM": 1, "LOW": 0 },
  "analysis_id": "abc123-def456"
}
```

#### Components:

**SqlEditor.jsx**
- `<textarea>` with monospace font (`font-mono`), dark bg (`bg-gray-800`), ~12 rows
- Placeholder: "Paste your SQL query here..."
- "Analyze" button: blue (`bg-blue-600 hover:bg-blue-700`), shows "Analyzing..." + spinner during loading
- Sample queries dropdown: 3-4 pre-loaded SQL examples that demonstrate different anti-patterns:
  - `SELECT * FROM orders WHERE EXTRACT(YEAR FROM created_at) = 2025;` (SELECT_STAR + FUNCTION_ON_COLUMN)
  - `DELETE FROM users;` (DELETE_WITHOUT_WHERE)
  - `SELECT * FROM users WHERE name LIKE '%john%';` (LEADING_WILDCARD_LIKE + SELECT_STAR)
  - `SELECT id FROM orders WHERE status = 'pending' OR customer_id = 5;` (OR_ACROSS_COLUMNS)
- Clicking a sample fills the textarea with that query

**FindingsPanel.jsx**
- Props: `findings` (array)
- Sort by severity: CRITICAL > HIGH > MEDIUM > LOW
- Each finding = a card with: SeverityBadge, rule_name (bold), message, suggestion (lighter text)
- Empty state: green checkmark + "No issues found"

**SeverityBadge.jsx**
- Props: `severity` (string)
- Colored pill badge
- CRITICAL = `bg-red-600 text-white`, HIGH = `bg-orange-500 text-white`, MEDIUM = `bg-yellow-500 text-black`, LOW = `bg-blue-500 text-white`

**SimilarCases.jsx**
- Props: `cases` (array)
- Each case: similarity % as a badge, query (truncated, expandable), problems list, fix text
- Empty state: "No similar cases in knowledge base yet"

**LlmAnalysis.jsx**
- Props: `analysis` (string, markdown)
- Render with react-markdown inside a bordered container (`border border-gray-700 rounded-lg p-4`)
- If null/empty: show "LLM analysis not available — linter and RAG results shown above"
- This demonstrates graceful degradation visually

**FeedbackButtons.jsx**
- Props: `analysisId`, `onFeedbackSent`
- Accept button (green), Reject button (red outline)
- Comment textarea appears after clicking either button
- Submit calls `submitFeedback()` → shows success message, disables buttons

### Step 2.5: Dashboard Page

File: `src/pages/Dashboard.jsx`

Fetches from `/metrics` and `/recent` on mount. Layout:

```
┌──────────┬──────────┬──────────┬──────────┐
│  Total   │ Patterns │ Accept   │   Top    │
│ Analyses │  Found   │  Rate    │ Pattern  │
└──────────┴──────────┴──────────┴──────────┘
┌──────────────────────┬─────────────────────┐
│  Pattern             │  Recent Analyses    │
│  Distribution        │  (table)            │
│  (bar chart)         │                     │
└──────────────────────┴─────────────────────┘
```

**MetricsCards.jsx**
- Props: `metrics` (object from /metrics)
- 4 cards in a grid row
- Each card: large number + label
- Styled: `bg-gray-800 rounded-lg p-6`

**PatternChart.jsx**
- Props: `patterns` (object: { rule_name: count })
- Horizontal bar chart with Recharts `BarChart`
- Color each bar by its rule's severity
- Rule severity mapping: CRITICAL = DELETE_WITHOUT_WHERE, UPDATE_WITHOUT_WHERE, DROP_TABLE. HIGH = FUNCTION_ON_COLUMN, LEFT_JOIN_WHERE_TRAP. MEDIUM = SELECT_STAR, LEADING_WILDCARD_LIKE, NOT_IN_SUBQUERY, OR_ACROSS_COLUMNS. LOW = MISSING_LIMIT.

**RecentTable.jsx**
- Props: `analyses` (array from /recent)
- Columns: Time (relative: "2 min ago"), Query (truncated ~50 chars), Findings, Severity
- Styled as a dark table with row hover
- Auto-refresh every 30 seconds

### Step 2.6: Polish

- Smooth fade-in transitions when analysis results appear
- Responsive: stack panels vertically on mobile (use Tailwind `md:grid-cols-2 grid-cols-1`)
- Footer: "SQLOps Guardian — SQL Anti-Pattern Detection & Optimization"
- Make sure the loading state feels snappy — skeleton loaders or a spinner

---

## PHASE 3: Docker

### Step 3.1: `Dockerfile.backend` (project root)

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first (layer caching)
COPY backend/pyproject.toml backend/uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy source code
COPY backend/ .

# Expose port
EXPOSE 8000

# Run the API
CMD ["uv", "run", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Step 3.2: `Dockerfile.frontend` (project root)

```dockerfile
# Build stage
FROM node:20-alpine AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ .
RUN npm run build

# Production stage — serve with nginx
FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html

# Nginx config to proxy API calls to backend
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
```

### Step 3.3: `nginx.conf` (project root)

```nginx
server {
    listen 80;

    location / {
        root /usr/share/nginx/html;
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    location ~ ^/(analyze|feedback|metrics|recent|health) {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Step 3.4: `docker-compose.yml` (project root)

```yaml
services:
  backend:
    build:
      context: .
      dockerfile: Dockerfile.backend
    ports:
      - "8000:8000"
    env_file:
      - .env
    volumes:
      - chroma_data:/app/chroma_db
      - sqlite_data:/app

  frontend:
    build:
      context: .
      dockerfile: Dockerfile.frontend
    ports:
      - "3000:80"
    depends_on:
      - backend

volumes:
  chroma_data:
  sqlite_data:
```

### Step 3.5: Create `.env.example`

```env
# Required
GEMINI_API_KEY=your-gemini-api-key-here

# Optional (defaults shown)
# SQLITE_DB_PATH=sqlops_guardian.db
# CHROMA_DB_PATH=chroma_db
# LOG_LEVEL=INFO
```

### Step 3.6: Verify Docker works

```powershell
docker-compose build
docker-compose up
```

Frontend at http://localhost:3000, backend at http://localhost:8000.

---

## PHASE 4: GitHub Preparation

### Step 4.1: README.md

Write a comprehensive README with these sections. Keep it focused and professional:

**Title + badges:** Project name, Python version badge, license badge

**One-liner:** "SQL anti-pattern detection and optimization tool with 4-layer analysis (Linter → RAG → LLM → Operations)"

**Screenshot/Demo:** Placeholder for a screenshot of the Analyzer page (add after frontend is built)

**Architecture diagram:** Show the 4-layer pipeline as a simple text/mermaid diagram

**Quick Start:**
```bash
# Clone
git clone https://github.com/YOUR_USERNAME/sqlops-guardian.git
cd sqlops-guardian

# Option 1: Docker (recommended)
cp .env.example .env
# Add your GEMINI_API_KEY to .env
docker-compose up

# Option 2: Manual
cd backend
uv sync
uv run python main.py
# In another terminal
cd frontend
npm install
npm run dev
```

**API Reference:** Brief table of all 5 endpoints

**Detection Rules:** Table of all 10 rules with severity levels

**Tech Stack:** Listed cleanly

**Project Structure:** The directory tree

**License:** MIT

### Step 4.2: LICENSE file

Create MIT license file in project root.

### Step 4.3: Final .gitignore check

Make sure nothing sensitive or generated gets committed. Run `git status` and review before first commit.

---

## Design Guidelines (apply throughout)

- **Dark theme**: `bg-gray-900` main, `bg-gray-800` cards/panels, `bg-gray-700` inputs
- **Accent**: Blue (`blue-500`, `blue-600`) for primary actions
- **Severity colors are sacred**: CRITICAL=red, HIGH=orange, MEDIUM=yellow, LOW=blue — consistent everywhere
- **Monospace for SQL**: Always `font-mono` for SQL queries
- **Component isolation**: Each component receives data via props, parent manages state
- **Responsive**: Use Tailwind breakpoints, stack on mobile, grid on desktop

## Build Order Summary

```
Phase 0 → Restructure (move files, fix imports, ALL TESTS PASS)
Phase 1 → CORS setup (one-line change)
Phase 2 → Frontend (scaffold → API client → shell → analyzer → dashboard → polish)
Phase 3 → Docker (backend image → frontend image → compose → verify)
Phase 4 → GitHub (README → LICENSE → push)
```

**CRITICAL: Complete each phase fully and verify before starting the next. Especially Phase 0 — all 111+ tests must pass after restructuring.**
