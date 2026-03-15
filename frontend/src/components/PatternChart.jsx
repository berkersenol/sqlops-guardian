import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
  ResponsiveContainer,
} from 'recharts'

// Rule → severity mapping (matches backend linter)
const RULE_SEVERITY = {
  DELETE_WITHOUT_WHERE: 'CRITICAL',
  UPDATE_WITHOUT_WHERE: 'CRITICAL',
  DROP_TABLE: 'CRITICAL',
  FUNCTION_ON_COLUMN: 'HIGH',
  LEFT_JOIN_WHERE_TRAP: 'HIGH',
  SELECT_STAR: 'MEDIUM',
  LEADING_WILDCARD_LIKE: 'MEDIUM',
  NOT_IN_SUBQUERY: 'MEDIUM',
  OR_ACROSS_COLUMNS: 'MEDIUM',
  MISSING_LIMIT: 'LOW',
}

const SEVERITY_COLOR = {
  CRITICAL: '#dc2626', // red-600
  HIGH: '#f97316',     // orange-500
  MEDIUM: '#eab308',   // yellow-500
  LOW: '#3b82f6',      // blue-500
}

function barColor(ruleName) {
  return SEVERITY_COLOR[RULE_SEVERITY[ruleName]] ?? '#6b7280'
}

const CustomTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null
  const { name, value } = payload[0].payload
  const sev = RULE_SEVERITY[name] ?? 'UNKNOWN'
  return (
    <div className="bg-gray-800 border border-gray-600 rounded px-3 py-2 text-xs shadow-lg">
      <p className="font-mono font-semibold text-gray-100">{name}</p>
      <p className="text-gray-400">
        Count: <span className="text-gray-100">{value}</span>
      </p>
      <p className="text-gray-400">
        Severity: <span style={{ color: SEVERITY_COLOR[sev] }}>{sev}</span>
      </p>
    </div>
  )
}

export default function PatternChart({ patterns }) {
  if (!patterns || Object.keys(patterns).length === 0) {
    return (
      <p className="text-sm text-gray-500 py-6 text-center">No patterns detected yet.</p>
    )
  }

  const data = Object.entries(patterns)
    .map(([name, count]) => ({ name, value: count }))
    .sort((a, b) => b.value - a.value)

  return (
    <ResponsiveContainer width="100%" height={Math.max(200, data.length * 40)}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 0, right: 16, bottom: 0, left: 8 }}
      >
        <XAxis
          type="number"
          allowDecimals={false}
          tick={{ fill: '#9ca3af', fontSize: 11 }}
          axisLine={{ stroke: '#374151' }}
          tickLine={false}
        />
        <YAxis
          type="category"
          dataKey="name"
          width={190}
          tick={{ fill: '#d1d5db', fontSize: 11, fontFamily: 'monospace' }}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
        <Bar dataKey="value" radius={[0, 4, 4, 0]}>
          {data.map((entry) => (
            <Cell key={entry.name} fill={barColor(entry.name)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
