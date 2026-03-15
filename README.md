# SQLOps Guardian

SQL analysis tool that catches anti-patterns and suggests optimizations.

## Quick Start

```bash
# Install dependencies
uv sync

# Set up environment
cp .env.example .env
# Edit .env with your GEMINI_API_KEY

# Run the API server
uv run uvicorn api:app --reload
```

The API will be available at `http://127.0.0.1:8000`.

## API Endpoints

### POST /analyze

Analyze a SQL query for anti-patterns.

```bash
curl -X POST http://127.0.0.1:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT * FROM users"}'
```

### POST /feedback

Submit feedback on a previous analysis. Accepted cases are stored in RAG for future reference.

```bash
curl -X POST http://127.0.0.1:8000/feedback \
  -H "Content-Type: application/json" \
  -d '{"analysis_id": 1, "accepted": true, "comments": "good fix"}'
```

### GET /metrics

Get summary statistics about all analyses.

```bash
curl http://127.0.0.1:8000/metrics
```

### GET /recent

Get recent analyses (default limit: 20).

```bash
curl http://127.0.0.1:8000/recent
curl http://127.0.0.1:8000/recent?limit=5
```

### GET /health

Check service health.

```bash
curl http://127.0.0.1:8000/health
```

## Running Tests

```bash
uv run python test_api.py
uv run python test_pipeline.py
```
