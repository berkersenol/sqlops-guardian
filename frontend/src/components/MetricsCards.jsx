function Card({ label, value, sub, compact }) {
  return (
    <div className="bg-gray-800 rounded-lg p-6 border border-gray-700 flex flex-col gap-1 min-w-0">
      <span className={`font-bold text-gray-100 truncate ${compact ? 'text-base' : 'text-3xl'}`}>
        {value ?? '—'}
      </span>
      <span className="text-sm text-gray-400">{label}</span>
      {sub && <span className="text-xs text-gray-600 mt-1 truncate">{sub}</span>}
    </div>
  )
}

export default function MetricsCards({ metrics }) {
  if (!metrics) return null

  const totalPatterns = Object.values(metrics.rule_counts ?? {}).reduce((a, b) => a + b, 0)
  const acceptRate =
    metrics.acceptance_rate != null
      ? `${Math.round(metrics.acceptance_rate * 100)}%`
      : 'No feedback yet'

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      <Card label="Total Analyses" value={metrics.total_analyses} />
      <Card
        label="Patterns Found"
        value={totalPatterns}
        sub={`${Object.keys(metrics.rule_counts ?? {}).length} distinct rules`}
      />
      <Card
        label="Acceptance Rate"
        value={metrics.acceptance_rate != null ? acceptRate : null}
        sub={
          metrics.feedback_total > 0
            ? `${metrics.feedback_total} feedback submissions`
            : 'No feedback yet'
        }
      />
      <Card
        label="Top Pattern"
        value={metrics.most_common_rule ?? 'None yet'}
        sub={metrics.most_common_severity ? `Most common severity: ${metrics.most_common_severity}` : undefined}
        compact={!!metrics.most_common_rule}
      />
    </div>
  )
}
