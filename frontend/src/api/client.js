const BASE = ''

export async function analyzeSQL(query) {
  const res = await fetch(`${BASE}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  })
  if (!res.ok) throw new Error(`Analysis failed: ${res.status}`)
  return res.json()
}

export async function submitFeedback(analysisId, accepted, comments = '') {
  const res = await fetch(`${BASE}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ analysis_id: analysisId, accepted, comments }),
  })
  if (!res.ok) throw new Error(`Feedback failed: ${res.status}`)
  return res.json()
}

export async function getMetrics() {
  const res = await fetch(`${BASE}/metrics`)
  if (!res.ok) throw new Error(`Metrics failed: ${res.status}`)
  return res.json()
}

export async function getRecent(limit = 20) {
  const res = await fetch(`${BASE}/recent?limit=${limit}`)
  if (!res.ok) throw new Error(`Recent failed: ${res.status}`)
  return res.json()
}

export async function getHealth() {
  const res = await fetch(`${BASE}/health`)
  if (!res.ok) throw new Error(`Health failed: ${res.status}`)
  return res.json()
}
