import ReactMarkdown from 'react-markdown'

function LlmField({ label, value }) {
  if (!value) return null
  return (
    <div className="flex gap-2 text-xs">
      <span className="text-gray-500 shrink-0">{label}:</span>
      <span className="text-gray-300">{value}</span>
    </div>
  )
}

export default function LlmAnalysis({ analysis }) {
  if (!analysis) {
    return (
      <div className="border border-gray-700 rounded-lg p-4 text-sm text-gray-500 italic">
        LLM analysis not available — linter and RAG results shown above.
      </div>
    )
  }

  const explanation = typeof analysis === 'string' ? analysis : analysis.explanation
  const indexes = analysis.suggested_indexes ?? []
  const rewritten = analysis.rewritten_query

  return (
    <div className="border border-gray-700 rounded-lg p-4 flex flex-col gap-4">
      {/* Meta row */}
      <div className="flex flex-wrap gap-4">
        <LlmField label="Risk" value={analysis.risk_level} />
        <LlmField label="Confidence" value={analysis.confidence} />
        <LlmField label="Estimated improvement" value={analysis.estimated_improvement} />
      </div>

      {/* Explanation */}
      {explanation && (
        <div className="prose prose-invert prose-sm max-w-none text-gray-300">
          <ReactMarkdown>{explanation}</ReactMarkdown>
        </div>
      )}

      {/* Suggested indexes */}
      {indexes.length > 0 && (
        <div>
          <p className="text-xs text-gray-400 font-medium mb-1">Suggested indexes:</p>
          <ul className="flex flex-col gap-1">
            {indexes.map((idx, i) => (
              <li key={i} className="font-mono text-xs bg-gray-900 rounded px-2 py-1 text-gray-300">
                {typeof idx === 'string' ? idx : JSON.stringify(idx)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Rewritten query */}
      {rewritten && (
        <div>
          <p className="text-xs text-gray-400 font-medium mb-1">Rewritten query:</p>
          <pre className="font-mono text-xs bg-gray-900 rounded px-3 py-2 text-green-300 whitespace-pre-wrap">
            {rewritten}
          </pre>
        </div>
      )}
    </div>
  )
}
