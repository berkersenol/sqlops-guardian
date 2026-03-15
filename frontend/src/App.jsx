import { useState, useEffect } from 'react'
import { getHealth } from './api/client'
import Analyzer from './pages/Analyzer'
import Dashboard from './pages/Dashboard'

export default function App() {
  const [activeTab, setActiveTab] = useState('analyzer')
  const [health, setHealth] = useState(null) // null = loading, true = healthy, false = down

  useEffect(() => {
    getHealth()
      .then((data) => setHealth(data.status === 'healthy'))
      .catch(() => setHealth(false))
  }, [])

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100 flex flex-col">
      {/* Nav bar */}
      <header className="bg-gray-800 border-b border-gray-700 px-6 py-3 flex items-center justify-between">
        <span className="text-lg font-semibold tracking-tight">SQLOps Guardian</span>

        <div className="flex items-center gap-4">
          {/* Tab buttons */}
          <nav className="flex gap-1">
            <button
              onClick={() => setActiveTab('analyzer')}
              className={`px-4 py-1.5 rounded text-sm font-medium transition-colors ${
                activeTab === 'analyzer'
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-400 hover:text-gray-100 hover:bg-gray-700'
              }`}
            >
              Analyzer
            </button>
            <button
              onClick={() => setActiveTab('dashboard')}
              className={`px-4 py-1.5 rounded text-sm font-medium transition-colors ${
                activeTab === 'dashboard'
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-400 hover:text-gray-100 hover:bg-gray-700'
              }`}
            >
              Dashboard
            </button>
          </nav>

          {/* Health indicator */}
          <div className="flex items-center gap-1.5 text-xs text-gray-400">
            <span
              className={`w-2 h-2 rounded-full ${
                health === null
                  ? 'bg-gray-500'
                  : health
                  ? 'bg-green-500'
                  : 'bg-red-500'
              }`}
            />
            {health === null ? 'connecting…' : health ? 'backend online' : 'backend offline'}
          </div>
        </div>
      </header>

      {/* Page content */}
      <main className="flex-1 p-6">
        {activeTab === 'analyzer' ? (
          <Analyzer />
        ) : (
          <Dashboard />
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-gray-800 px-6 py-3 text-center text-xs text-gray-600">
        SQLOps Guardian — SQL Anti-Pattern Detection &amp; Optimization
      </footer>
    </div>
  )
}
