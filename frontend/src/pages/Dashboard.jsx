import { useState, useEffect, useCallback } from 'react'
import { getMetrics, getRecent } from '../api/client'
import MetricsCards from '../components/MetricsCards'
import PatternChart from '../components/PatternChart'
import RecentTable from '../components/RecentTable'

export default function Dashboard() {
  const [metrics, setMetrics] = useState(null)
  const [analyses, setAnalyses] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchAll = useCallback(async () => {
    try {
      const [m, r] = await Promise.all([getMetrics(), getRecent(20)])
      setMetrics(m)
      setAnalyses(r)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto">
        <p className="text-sm text-gray-500 py-8 text-center">Loading dashboard…</p>
      </div>
    )
  }

  return (
    <div className="max-w-6xl mx-auto flex flex-col gap-6 animate-fade-in">
      {error && (
        <div className="bg-red-900/40 border border-red-700 rounded-lg px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Metrics */}
      <MetricsCards metrics={metrics} />

      {/* Chart + Recent side by side */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-gray-800 rounded-lg p-5 border border-gray-700">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">
            Anti-Pattern Distribution
          </h2>
          <PatternChart patterns={metrics?.rule_counts} />
        </div>

        <div className="bg-gray-800 rounded-lg p-5 border border-gray-700">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">
            Recent Analyses
          </h2>
          <RecentTable analyses={analyses} onRefresh={fetchAll} />
        </div>
      </div>
    </div>
  )
}
