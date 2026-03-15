import { useState } from 'react'

function SimilarCase({ c }) {
  const [expanded, setExpanded] = useState(false)
  const pct = c.similarity != null ? Math.round(c.similarity * 100) : null

  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
      <div className="flex items-center justify-between mb-2">
        <span className="font-mono text-xs text-gray-400">{c.case_id}</span>
        {pct != null && (
          <span className="text-xs font-semibold bg-blue-900 text-blue-300 px-2 py-0.5 rounded">
            {pct}% match
          </span>
        )}
      </div>

      {c.query && (
        <div className="mb-2">
          <button
            onClick={() => setExpanded((e) => !e)}
            className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            {expanded ? 'Hide query ▲' : 'Show query ▼'}
          </button>
          {expanded && (
            <pre className="mt-1 text-xs font-mono bg-gray-900 rounded p-2 text-gray-300 whitespace-pre-wrap break-all">
              {c.query}
            </pre>
          )}
        </div>
      )}

      {c.problems && c.problems.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {c.problems.map((p) => (
            <span key={p} className="text-xs bg-gray-700 text-gray-300 px-2 py-0.5 rounded font-mono">
              {p}
            </span>
          ))}
        </div>
      )}

      {c.fix && (
        <p className="text-xs text-gray-400">
          <span className="text-gray-300 font-medium">Fix: </span>{c.fix}
        </p>
      )}
    </div>
  )
}

export default function SimilarCases({ cases }) {
  if (!cases || cases.length === 0) {
    return (
      <p className="text-sm text-gray-500 py-4">No similar cases in knowledge base yet.</p>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {cases.map((c, i) => (
        <SimilarCase key={c.case_id ?? i} c={c} />
      ))}
    </div>
  )
}
