import './TabBar.css'

const TABS = [
  { id: 'overview', label: 'OVERVIEW' },
  { id: 'settings', label: 'SETTINGS' },
  { id: 'scenarios', label: 'SCENARIOS' },
  { id: 'features', label: 'FEATURES' },
  { id: 'skullwatcher', label: 'SKULLWATCHER' },
  { id: 'threads', label: 'THREADS' },
]

export default function TabBar({ activeTab, onTabChange }) {
  return (
    <div className="tab-bar">
      {TABS.map(tab => (
        <button
          key={tab.id}
          className={`tab-btn ${activeTab === tab.id ? 'active' : ''}`}
          onClick={() => onTabChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}
