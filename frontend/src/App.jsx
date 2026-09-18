import { useEffect, useState } from 'react'
import { api } from './api.js'
import ChatView from './views/ChatView.jsx'
import FlashView from './views/FlashView.jsx'
import ProjectsView from './views/ProjectsView.jsx'
import TimelineView from './views/TimelineView.jsx'
import KnowledgeView from './views/KnowledgeView.jsx'
import LifeView from './views/LifeView.jsx'

const NAV = [
  { key: 'chat', label: '对话' },
  { key: 'flash', label: '闪记' },
  { key: 'projects', label: '项目' },
  { key: 'timeline', label: '时间轴' },
  { key: 'knowledge', label: '知识库' },
  { key: 'life', label: '生活角' },
]

// 闪记快捷弹窗：Ctrl+Shift+X 唤起，5 秒哑捕获（窗口级快捷键，M7 升系统级）
function FlashPopup({ onCaptured }) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')

  useEffect(() => {
    const onKey = (e) => {
      if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'x') {
        e.preventDefault()
        setOpen((o) => !o)
      }
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const submit = async () => {
    const t = text.trim()
    if (!t) return
    try {
      await api.capture(t)
    } catch (err) {
      alert(err.message)
      return
    }
    setText('')
    setOpen(false)
    onCaptured?.()
  }

  if (!open) return null
  return (
    <div className="popup-mask" onClick={() => setOpen(false)}>
      <div className="popup" onClick={(e) => e.stopPropagation()}>
        <input
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit()
          }}
          placeholder="闪记：想到什么记什么，不用分类…（Enter 保存，Esc 关闭）"
        />
      </div>
    </div>
  )
}

export default function App() {
  const [view, setView] = useState('chat')
  const [status, setStatus] = useState({ openTasks: 0, inbox: 0, reminders: 0 })
  const [refreshKey, setRefreshKey] = useState(0)
  const [chatSeed, setChatSeed] = useState(null)
  const refresh = () => setRefreshKey((k) => k + 1)

  useEffect(() => {
    api.statusBar().then(setStatus).catch(console.error)
  }, [refreshKey, view])

  // 召唤式处理：闪记视图勾选后带上下文切到对话流
  const startChatWith = (text) => {
    setChatSeed(text)
    setView('chat')
  }

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
              {n.key === 'flash' && status.inbox > 0 && (
                <span className="nav-badge">{status.inbox}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-status">
          <div>待办 {status.openTasks}</div>
          <div>闪记 {status.inbox}</div>
          <div>提醒 {status.reminders}</div>
        </div>
      </aside>

      <main className="main">
        {view === 'chat' && (
          <ChatView
            refresh={refresh}
            initialMessage={chatSeed}
            onSeedConsumed={() => setChatSeed(null)}
          />
        )}
        {view === 'flash' && (
          <FlashView refreshKey={refreshKey} refresh={refresh} startChatWith={startChatWith} />
        )}
        {view === 'projects' && <ProjectsView refreshKey={refreshKey} refresh={refresh} />}
        {view === 'timeline' && <TimelineView refreshKey={refreshKey} refresh={refresh} />}
        {view === 'knowledge' && <KnowledgeView refreshKey={refreshKey} />}
        {view === 'life' && <LifeView refreshKey={refreshKey} refresh={refresh} />}
      </main>

      <FlashPopup onCaptured={refresh} />
    </div>
  )
}
