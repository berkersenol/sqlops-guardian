import { useEffect, useRef } from 'react'
import SeverityBadge from './SeverityBadge'

function timeAgo(isoString) {
  if (!isoString) return '—'
  const diff = Math.floor((Date.now() - new Date(isoString).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function findingsCount(lint_findings_json) {
  try {
    return JSON.parse(lint_findings_json).length
  } catch {
    return 0
  }
}

export default function RecentTable({ analyses, onRefresh }) {
  const intervalRef = useRef(null)

  useEffect(() => {
    intervalRef.current = setInterval(() => {
      onRefresh?.()
    }, 30_000)
    return () => clearInterval(intervalRef.current)
  }, [onRefresh])

  if (!analyses || analyses.length === 0) {
    return <p className="text-sm text-gray-500 py-4">No analyses yet.</p>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-500 uppercase tracking-wide border-b border-gray-700">
            <th className="pb-2 pr-4 font-medium">Time</th>
            <th className="pb-2 pr-4 font-medium">Query</th>
            <th className="pb-2 pr-4 font-medium text-center">Findings</th>
            <th className="pb-2 font-medium">Severity</th>
          </tr>
        </thead>
        <tbody>
          {analyses.map((row) => (
            <tr
              key={row.id}
              className="border-b border-gray-800 hover:bg-gray-700/40 transition-colors duration-150 cursor-default"
            >
              <td className="py-2.5 pr-4 text-xs text-gray-500 whitespace-nowrap">
                {timeAgo(row.timestamp)}
              </td>
              <td className="py-2.5 pr-4 font-mono text-xs text-gray-300 max-w-xs">
                <span
                  title={row.query}
                  className="block truncate"
                  style={{ maxWidth: '320px' }}
                >
                  {row.query}
                </span>
              </td>
              <td className="py-2.5 pr-4 text-center text-gray-400">
                {findingsCount(row.lint_findings)}
              </td>
              <td className="py-2.5">
                <SeverityBadge severity={row.overall_severity} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
