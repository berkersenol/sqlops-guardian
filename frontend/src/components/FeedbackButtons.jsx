import { useState } from 'react'
import { submitFeedback } from '../api/client'

export default function FeedbackButtons({ analysisId, onFeedbackSent }) {
  const [choice, setChoice] = useState(null)   // 'accept' | 'reject' | null
  const [comment, setComment] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  if (submitted) {
    return (
      <div className="text-sm text-green-400 font-medium">
        Feedback submitted — thank you!
      </div>
    )
  }

  async function handleSubmit() {
    if (!choice) return
    setLoading(true)
    setError(null)
    try {
      await submitFeedback(analysisId, choice === 'accept', comment)
      setSubmitted(true)
      onFeedbackSent?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2">
        <button
          onClick={() => setChoice('accept')}
          className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
            choice === 'accept'
              ? 'bg-green-600 text-white'
              : 'border border-green-600 text-green-400 hover:bg-green-600 hover:text-white'
          }`}
        >
          ✓ Accept
        </button>
        <button
          onClick={() => setChoice('reject')}
          className={`px-4 py-2 rounded text-sm font-medium transition-colors ${
            choice === 'reject'
              ? 'bg-red-600 text-white'
              : 'border border-red-600 text-red-400 hover:bg-red-600 hover:text-white'
          }`}
        >
          ✗ Reject
        </button>
      </div>

      {choice && (
        <div className="flex flex-col gap-2">
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Optional comment..."
            rows={2}
            className="bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 resize-none focus:outline-none focus:border-blue-500"
          />
          <div className="flex items-center gap-3">
            <button
              onClick={handleSubmit}
              disabled={loading}
              className="px-4 py-1.5 rounded text-sm font-medium bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 transition-colors"
            >
              {loading ? 'Submitting…' : 'Submit feedback'}
            </button>
            <button
              onClick={() => setChoice(null)}
              className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
            >
              Cancel
            </button>
          </div>
          {error && <p className="text-xs text-red-400">{error}</p>}
        </div>
      )}
    </div>
  )
}
