import { useState } from 'react'
import { analyzeSQL, getRecent } from '../api/client'
import SqlEditor from '../components/SqlEditor'
import FindingsPanel from '../components/FindingsPanel'
import SimilarCases from '../components/SimilarCases'
import LlmAnalysis from '../components/LlmAnalysis'
import FeedbackButtons from '../components/FeedbackButtons'
import SeverityBadge from '../components/SeverityBadge'

export default function Analyzer() {
  const [query, setQuery] = useState('')
  const [report, setReport] = useState(null)
  const [analysisId, setAnalysisId] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [feedbackSent, setFeedbackSent] = useState(false)

  async function handleAnalyze() {
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    setReport(null)
    setAnalysisId(null)
    setFeedbackSent(false)

    try {
      const data = await analyzeSQL(query)
      setReport(data)

      // Fetch the analysis_id from /recent since /analyze doesn't return it
      try {
        const recent = await getRecent(1)
        if (recent.length > 0) setAnalysisId(recent[0].id)
      } catch {
        // non-critical — feedback just won't work
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-6xl mx-auto flex flex-col gap-6">
      {/* Editor */}
      <div className="bg-gray-800 rounded-lg p-5 border border-gray-700">
        <SqlEditor
          query={query}
          onChange={setQuery}
          onAnalyze={handleAnalyze}
          loading={loading}
        />
      </div>

      {error && (
        <div className="bg-red-900/40 border border-red-700 rounded-lg px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Results */}
      {report && (
        <div className="flex flex-col gap-6 animate-fade-in">
          {/* Summary bar */}
          <div className="flex flex-wrap items-center gap-3 bg-gray-800 rounded-lg px-5 py-3 border border-gray-700">
            <SeverityBadge severity={report.overall_severity} />
            <span className="text-sm text-gray-300">{report.summary}</span>
            {report.response_time_ms != null && (
              <span className="ml-auto text-xs text-gray-600">{report.response_time_ms} ms</span>
            )}
          </div>

          {/* Two-column: Findings + Similar Cases */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div>
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">
                Lint Findings ({report.lint_findings.length})
              </h2>
              <FindingsPanel findings={report.lint_findings} />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">
                Similar Cases
              </h2>
              <SimilarCases cases={report.similar_cases} />
            </div>
          </div>

          {/* LLM Analysis */}
          <div>
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">
              LLM Deep Analysis
            </h2>
            <LlmAnalysis analysis={report.llm_analysis} />
          </div>

          {/* Feedback */}
          {analysisId && !feedbackSent && (
            <div className="bg-gray-800 rounded-lg px-5 py-4 border border-gray-700">
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">
                Feedback
              </h2>
              <FeedbackButtons
                analysisId={analysisId}
                onFeedbackSent={() => setFeedbackSent(true)}
              />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
