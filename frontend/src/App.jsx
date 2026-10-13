import { useEffect, useRef, useState } from 'react'
import { api } from './api.js'
import ChatView from './views/ChatView.jsx'
import WorkbenchView from './views/WorkbenchView.jsx'
import ProjectsView from './views/ProjectsView.jsx'
import KnowledgeView from './views/KnowledgeView.jsx'
import LifeView from './views/LifeView.jsx'

const NAV = [
  { key: 'chat', label: '对话' },
  { key: 'workbench', label: '工作台' },
  { key: 'projects', label: '进行中的事' },
  { key: 'knowledge', label: '知识库' },
  { key: 'life', label: '生活角' },
]

// 通知中心：轮询调度器触发的通知（提醒/截止/日程预告），铃铛角标 + 点开列表
function NotifyBell({ onImportant }) {
  const [items, setItems] = useState([])
  const [open, setOpen] = useState(false)
  const seen = useRef(new Set())
  const wrapRef = useRef(null)

  // 点面板外任意处收回（再点铃铛切换开关）
  useEffect(() => {
    if (!open) return
    const onDoc = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

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
    <div className="notify-wrap" ref={wrapRef}>
      <button className="notify-bell" onClick={() => setOpen((o) => !o)} title="通知">
        <svg viewBox="0 0 24 24"><path d="M6 9.5a6 6 0 1 1 12 0c0 4 1.5 5.5 2 6H4c.5-.5 2-2 2-6Z"/><path d="M10 18.5a2 2 0 0 0 4 0"/></svg>
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
                  {n.kind === 'due' ? '截止' : n.kind === 'schedule' ? '日程' : n.kind === 'graveyard' ? '待清理' : n.kind === 'lint' ? '体检' : '提醒'}
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
          {result.wiki.length === 0 && result.tasks.length === 0 && result.projects.length === 0 &&
           result.links.length === 0 && result.events.length === 0 && result.reminders.length === 0 && (
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
          {result.links.length > 0 && <div className="search-group">资源</div>}
          {result.links.map((l) => (
            <div key={l.id} className="notify-item">
              <div className="notify-title">{l.title} <span className="hint">（{l.kind}）</span></div>
              {l.target && <div className="notify-detail">{l.target}</div>}
            </div>
          ))}
          {result.events.length > 0 && <div className="search-group">日程</div>}
          {result.events.map((e) => (
            <div key={e.id} className="notify-item">
              <div className="notify-title">{e.title} <span className="hint">（{e.date} {e.start_time || ''}{e.rrule ? '，重复系列' : ''}）</span></div>
            </div>
          ))}
          {result.reminders.length > 0 && <div className="search-group">提醒</div>}
          {result.reminders.map((r) => (
            <div key={r.id} className="notify-item">
              <div className="notify-title">{r.title} <span className="hint">（{r.trigger_type}{r.trigger_value ? ` ${r.trigger_value}` : ''}）</span></div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function App() {
  const [view, setView] = useState('chat')
  const [refreshKey, setRefreshKey] = useState(0)
  const [notice, setNotice] = useState(null)
  const refresh = () => setRefreshKey((k) => k + 1)

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
            </button>
          ))}
        </nav>
        <div className="sidebar-status">
          <SearchBox />
        </div>
      </aside>

      <main className="main">
        {view === 'chat' && (
          <ChatView
            refresh={refresh}
            notice={view === 'chat' ? notice : null}
            onNoticeConsumed={() => setNotice(null)}
            goView={setView}
          />
        )}
        {view === 'workbench' && <WorkbenchView refreshKey={refreshKey} refresh={refresh} />}
        {view === 'projects' && <ProjectsView refreshKey={refreshKey} refresh={refresh} />}
        {view === 'knowledge' && <KnowledgeView refreshKey={refreshKey} />}
        {view === 'life' && <LifeView refreshKey={refreshKey} refresh={refresh} />}
      </main>
    </div>
  )
}
