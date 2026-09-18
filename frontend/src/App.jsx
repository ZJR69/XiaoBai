import { useEffect, useRef, useState } from 'react'
import { api } from './api.js'

const TYPES = [
  { key: 'task', label: '任务', color: '#3a7d5c' },
  { key: 'knowledge', label: '知识', color: '#8a6d3b' },
  { key: 'life', label: '生活', color: '#4a7fa5' },
  { key: 'idea', label: '灵感', color: '#9c5c8f' },
]

function CaptureBar({ onCaptured }) {
  const [content, setContent] = useState('')
  const inputRef = useRef(null)

  // 全局快捷键：Ctrl+Shift+X 聚焦速记框（M7 可升级为系统级快捷键）
  useEffect(() => {
    const onKey = (e) => {
      if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'x') {
        e.preventDefault()
        inputRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const submit = async (e) => {
    e.preventDefault()
    const text = content.trim()
    if (!text) return
    try {
      await api.capture(text)
      setContent('')
      onCaptured?.()
    } catch (err) {
      alert(err.message)
    }
  }

  return (
    <form className="capture-bar" onSubmit={submit}>
      <input
        ref={inputRef}
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder="想到什么，先记下来…（Ctrl+Shift+X 随时唤起）"
      />
      <button type="submit">记下</button>
    </form>
  )
}

function InboxView({ refreshKey, onChanged }) {
  const [items, setItems] = useState([])

  const load = async () => setItems(await api.listInbox())
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey])

  const confirm = async (id, type) => {
    await api.confirmInbox(id, type)
    await load()
    onChanged?.()
  }
  const discard = async (id) => {
    await api.discardInbox(id)
    await load()
  }

  if (items.length === 0) return <p className="empty">收件箱清空了，很干净。</p>

  return (
    <div className="inbox">
      {items.map((it) => (
        <div key={it.id} className="inbox-item">
          <div className="inbox-content">{it.content}</div>
          <div className="inbox-time">{it.captured_at?.slice(5, 16).replace('T', ' ')}</div>
          <div className="inbox-actions">
            {TYPES.map((t) => (
              <button
                key={t.key}
                className="chip"
                style={{ borderColor: t.color, color: t.color }}
                onClick={() => confirm(it.id, t.key)}
              >
                {t.label}
              </button>
            ))}
            <button className="chip discard" onClick={() => discard(it.id)}>丢弃</button>
          </div>
        </div>
      ))}
    </div>
  )
}

function TasksView({ refreshKey }) {
  const [tasks, setTasks] = useState([])
  const [title, setTitle] = useState('')
  const [priority, setPriority] = useState(3)
  const [due, setDue] = useState('')

  const load = async () => setTasks(await api.listTasks())
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey])

  const add = async (e) => {
    e.preventDefault()
    const t = title.trim()
    if (!t) return
    await api.createTask({ title: t, priority, due_at: due || null })
    setTitle('')
    setPriority(3)
    setDue('')
    await load()
  }

  const toggle = async (task) => {
    await api.updateTask(task.id, { status: task.status === 'done' ? 'backlog' : 'done' })
    await load()
  }

  const remove = async (id) => {
    await api.deleteTask(id)
    await load()
  }

  return (
    <div>
      <form className="task-form" onSubmit={add}>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="新任务…"
        />
        <select value={priority} onChange={(e) => setPriority(Number(e.target.value))}>
          {[1, 2, 3, 4, 5].map((p) => (
            <option key={p} value={p}>P{p}</option>
          ))}
        </select>
        <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
        <button type="submit">添加</button>
      </form>
      <ul className="task-list">
        {tasks.map((t) => {
          const done = t.status === 'done'
          return (
            <li key={t.id} className={done ? 'task done' : 'task'}>
              <label className="task-row">
                <input type="checkbox" checked={done} onChange={() => toggle(t)} />
                <span className="task-title">{t.title}</span>
                <span className="task-pri">P{t.priority}</span>
                {t.due_at && <span className="task-due">{t.due_at.slice(5)}</span>}
              </label>
              <button className="task-del" onClick={() => remove(t.id)}>×</button>
            </li>
          )
        })}
      </ul>
      {tasks.length === 0 && <p className="empty">还没有任务，从收件箱确认一条，或直接添加。</p>}
    </div>
  )
}

export default function App() {
  const [tab, setTab] = useState('inbox')
  const [refreshKey, setRefreshKey] = useState(0)
  const refresh = () => setRefreshKey((k) => k + 1)

  return (
    <div className="app">
      <header>
        <h1>小白</h1>
        <span className="sub">先记下来，再分类。</span>
      </header>

      <CaptureBar onCaptured={refresh} />

      <nav className="tabs">
        <button className={tab === 'inbox' ? 'active' : ''} onClick={() => setTab('inbox')}>
          收件箱
        </button>
        <button className={tab === 'tasks' ? 'active' : ''} onClick={() => setTab('tasks')}>
          任务池
        </button>
      </nav>

      <main>
        {tab === 'inbox' ? (
          <InboxView refreshKey={refreshKey} onChanged={refresh} />
        ) : (
          <TasksView refreshKey={refreshKey} />
        )}
      </main>
    </div>
  )
}
