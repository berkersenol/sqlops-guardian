const SAMPLE_QUERIES = [
  {
    label: 'SELECT_STAR + FUNCTION_ON_COLUMN',
    sql: "SELECT * FROM orders WHERE EXTRACT(YEAR FROM created_at) = 2025;",
  },
  {
    label: 'DELETE_WITHOUT_WHERE',
    sql: "DELETE FROM users;",
  },
  {
    label: 'LEADING_WILDCARD_LIKE + SELECT_STAR',
    sql: "SELECT * FROM users WHERE name LIKE '%john%';",
  },
  {
    label: 'OR_ACROSS_COLUMNS',
    sql: "SELECT id FROM orders WHERE status = 'pending' OR customer_id = 5;",
  },
]

export default function SqlEditor({ query, onChange, onAnalyze, loading }) {
  function handleSample(e) {
    const idx = e.target.value
    if (idx === '') return
    onChange(SAMPLE_QUERIES[idx].sql)
    e.target.value = ''
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <label className="text-sm font-medium text-gray-300">SQL Query</label>
        <select
          onChange={handleSample}
          defaultValue=""
          className="text-xs bg-gray-700 border border-gray-600 text-gray-300 rounded px-2 py-1 focus:outline-none focus:border-blue-500"
        >
          <option value="" disabled>Load sample query…</option>
          {SAMPLE_QUERIES.map((s, i) => (
            <option key={i} value={i}>{s.label}</option>
          ))}
        </select>
      </div>

      <textarea
        value={query}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Paste your SQL query here..."
        rows={12}
        spellCheck={false}
        className="w-full font-mono text-sm bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-gray-100 placeholder-gray-600 resize-none focus:outline-none focus:border-blue-500"
      />

      <button
        onClick={onAnalyze}
        disabled={loading || !query.trim()}
        className="self-start flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-semibold bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white transition-colors"
      >
        {loading && (
          <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
        )}
        {loading ? 'Analyzing…' : 'Analyze'}
      </button>
    </div>
  )
}
