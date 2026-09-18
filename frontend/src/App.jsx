import { useEffect, useState } from 'react'
import { api } from './api.js'
import ChatView from './views/ChatView.jsx'
import ProjectsView from './views/ProjectsView.jsx'
import TimelineView from './views/TimelineView.jsx'
import KnowledgeView from './views/KnowledgeView.jsx'
import LifeView from './views/LifeView.jsx'

const NAV = [
  { key: 'chat', label: '对话' },
  { key: 'projects', label: '项目' },
  { key: 'timeline', label: '时间轴' },
  { key: 'knowledge', label: '知识库' },
  { key: 'life', label: '生活角' },
]

export default function App() {
  const [view, setView] = useState('chat')
  const [status, setStatus] = useState({ openTasks: 0, inbox: 0, reminders: 0 })
  const [refreshKey, setRefreshKey] = useState(0)
  const refresh = () => setRefreshKey((k) => k + 1)

  useEffect(() => {
    api.statusBar().then(setStatus).catch(console.error)
  }, [refreshKey, view])

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="logo">
          <h1>小白</h1>
        </div>
        <nav className="nav">
          {NAV.map((n) => (
            <button
              key={n.key}
              className={view === n.key ? 'nav-item active' : 'nav-item'}
              onClick={() => setView(n.key)}
            >
              {n.label}
              {n.key === 'inbox' && status.inbox > 0 && (
                <span className="nav-badge">{status.inbox}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-status">
          <div>待办 {status.openTasks}</div>
          <div>收件 {status.inbox}</div>
          <div>提醒 {status.reminders}</div>
        </div>
      </aside>

      <main className="main">
        {view === 'chat' && <ChatView refresh={refresh} />}
        {view === 'projects' && <ProjectsView refreshKey={refreshKey} refresh={refresh} />}
        {view === 'timeline' && <TimelineView refreshKey={refreshKey} refresh={refresh} />}
        {view === 'knowledge' && <KnowledgeView refreshKey={refreshKey} />}
        {view === 'life' && <LifeView refreshKey={refreshKey} refresh={refresh} />}
      </main>
    </div>
  )
}
