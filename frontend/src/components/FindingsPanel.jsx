import SeverityBadge from './SeverityBadge'

const SEVERITY_ORDER = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 }

export default function FindingsPanel({ findings }) {
  if (!findings || findings.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-10 gap-2 text-green-400">
        <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
        <span className="text-sm font-medium">No issues found</span>
      </div>
    )
  }

  const sorted = [...findings].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9)
  )

  return (
    <div className="flex flex-col gap-3">
      {sorted.map((f, i) => (
        <div key={i} className="bg-gray-800 rounded-lg p-4 border border-gray-700">
          <div className="flex items-center gap-2 mb-1">
            <SeverityBadge severity={f.severity} />
            <span className="font-mono text-sm font-bold text-gray-100">{f.rule_name}</span>
          </div>
          <p className="text-sm text-gray-300 mb-1">{f.description}</p>
          {f.suggestion && (
            <p className="text-xs text-gray-500">
              <span className="text-gray-400 font-medium">Fix: </span>{f.suggestion}
            </p>
          )}
        </div>
      ))}
    </div>
  )
}
