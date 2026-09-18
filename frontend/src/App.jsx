import { useEffect, useRef, useState } from 'react'
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
  { key: 'projects', label: '进行中的事' },
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

// dropzone 变更监听提示：轮询后端事件（watchdog 检测到新文件时显示）
function WatchTip({ onGoRaw }) {
  const [since, setSince] = useState(null)
  const [count, setCount] = useState(0)
  const sinceRef = useRef(null)

  useEffect(() => {
    const poll = async () => {
      try {
        const res = await api.rawEvents(sinceRef.current ?? 0)
        if (sinceRef.current === null) {
          // 首次：建立基线，不提示历史事件
          sinceRef.current = res.now
        } else if (res.count > 0) {
          setCount(res.count)
          sinceRef.current = res.now
        }
      } catch {
        /* 后端未启动时静默 */
      }
    }
    poll()
    const timer = setInterval(poll, 15000)
    return () => clearInterval(timer)
  }, [])

  if (count === 0) return null
  const goRaw = () => {
    setCount(0) // 处理意图已表达，提示清零（下次新事件再提示）
    onGoRaw()
  }
  return (
    <div className="watch-tip" onClick={goRaw} title="点击去原料区处理">
      投放区有动静（{count} 个事件）
      <br />
      要消化吗？
    </div>
  )
}

// 通知中心：轮询调度器触发的通知（提醒/截止/日程预告），铃铛角标 + 点开列表
function NotifyBell({ onImportant }) {
  const [items, setItems] = useState([])
  const [open, setOpen] = useState(false)
  const seen = useRef(new Set())

  useEffect(() => {
    let timer
    const poll = async () => {
      try {
        const list = await api.listNotifications()
        setItems(list)
        // 重要通知（截止临近）：首次出现的推给 ChatView 即时插入（同轮多条全推）
        const fresh = list.filter((n) => n.important && !seen.current.has(n.id))
        fresh.forEach((n) => {
          seen.current.add(n.id)
          onImportant?.(n)
        })
        list.forEach((n) => seen.current.add(n.id))
      } catch {
        /* 后端未启动时静默 */
      }
    }
    poll()
    timer = setInterval(poll, 15000)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const openItems = items.filter((n) => !n.dismissed_at)
  const dismissAll = async () => {
    await api.dismissNotifications(openItems.map((n) => n.id))
    setItems(await api.listNotifications().catch(() => items))
  }

  return (
    <div className="notify-wrap">
      <button className="notify-bell" onClick={() => setOpen((o) => !o)} title="通知">
        🔔
        {openItems.length > 0 && <span className="nav-badge">{openItems.length}</span>}
      </button>
      {open && (
        <div className="notify-panel">
          <div className="notify-head">
            通知
            {openItems.length > 0 && (
              <button className="small-btn" onClick={dismissAll}>全部处理</button>
            )}
          </div>
          {openItems.length === 0 && <p className="empty">没有待处理的通知。</p>}
          {openItems.map((n) => (
            <div key={n.id} className="notify-item">
              <div className="notify-title">
                <span className={`kind-chip ${n.kind}`}>
                  {n.kind === 'due' ? '截止' : n.kind === 'schedule' ? '日程' : n.kind === 'graveyard' ? '待清理' : '提醒'}
                </span>
                {n.title}
              </div>
              {n.detail && <div className="notify-detail">{n.detail}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// 统一搜索（FR-2.5）：侧栏输入 → 全库搜（wiki/任务/事务）
function SearchBox() {
  const [q, setQ] = useState('')
  const [result, setResult] = useState(null)

  const doSearch = async (e) => {
    e.preventDefault()
    const query = q.trim()
    if (query.length < 2) return
    setResult(await api.search(query).catch(() => null))
  }

  return (
    <div className="search-wrap">
      <form onSubmit={doSearch}>
        <input
          className="search-input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索任务/知识/事务…"
        />
      </form>
      {result && (
        <div className="search-panel">
          <div className="notify-head">
            搜索「{result.q}」
            <button className="icon-btn" onClick={() => setResult(null)}>×</button>
          </div>
          {result.wiki.length === 0 && result.tasks.length === 0 && result.projects.length === 0 && (
            <p className="empty">没有匹配结果。</p>
          )}
          {result.wiki.length > 0 && <div className="search-group">知识库</div>}
          {result.wiki.map((w) => (
            <div key={w.path} className="notify-item">
              <div className="notify-title">{w.title}</div>
              {w.snippet && <div className="notify-detail">{w.snippet}</div>}
            </div>
          ))}
          {result.projects.length > 0 && <div className="search-group">进行中的事</div>}
          {result.projects.map((p) => (
            <div key={p.id} className="notify-item">
              <div className="notify-title">{p.name} <span className="hint">（{p.category}）</span></div>
            </div>
          ))}
          {result.tasks.length > 0 && <div className="search-group">任务</div>}
          {result.tasks.map((t) => (
            <div key={t.id} className="notify-item">
              <div className="notify-title">{t.title} <span className="hint">（{t.status}{t.due_at ? `，截止 ${t.due_at.slice(5, 10)}` : ''}）</span></div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function App() {
  const [view, setView] = useState('chat')
  const [status, setStatus] = useState({ openTasks: 0, inbox: 0, reminders: 0 })
  const [refreshKey, setRefreshKey] = useState(0)
  const [chatSeed, setChatSeed] = useState(null)
  const [notice, setNotice] = useState(null)
  const [knowledgeTab, setKnowledgeTab] = useState('wiki')
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
          <NotifyBell onImportant={(n) => setNotice({ ...n, ts: Date.now() })} />
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
          <SearchBox />
          <WatchTip onGoRaw={() => { setKnowledgeTab('raw'); setView('knowledge') }} />
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
            notice={view === 'chat' ? notice : null}
            onNoticeConsumed={() => setNotice(null)}
          />
        )}
        {view === 'flash' && (
          <FlashView refreshKey={refreshKey} refresh={refresh} startChatWith={startChatWith} />
        )}
        {view === 'projects' && <ProjectsView refreshKey={refreshKey} refresh={refresh} />}
        {view === 'timeline' && <TimelineView refreshKey={refreshKey} refresh={refresh} />}
        {view === 'knowledge' && <KnowledgeView refreshKey={refreshKey} initialTab={knowledgeTab} />}
        {view === 'life' && <LifeView refreshKey={refreshKey} refresh={refresh} />}
      </main>

      <FlashPopup onCaptured={refresh} />
    </div>
  )
}
