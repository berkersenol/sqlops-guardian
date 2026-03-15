const STYLES = {
  CRITICAL: 'bg-red-600 text-white',
  HIGH: 'bg-orange-500 text-white',
  MEDIUM: 'bg-yellow-500 text-black',
  LOW: 'bg-blue-500 text-white',
}

export default function SeverityBadge({ severity }) {
  const cls = STYLES[severity] ?? 'bg-gray-600 text-white'
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold uppercase tracking-wide ${cls}`}>
      {severity}
    </span>
  )
}
