import { useEffect, useState } from 'react'
import { api } from '../api.js'

function groupByDate(tasks) {
  const today = new Date().toISOString().slice(0, 10)
  const weekEnd = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10)
  const groups = { overdue: [], today: [], week: [], later: [], none: [] }
  for (const t of tasks) {
    if (t.status === 'done' || t.status === 'cancelled') continue
    if (!t.due_at) groups.none.push(t)
    else if (t.due_at < today) groups.overdue.push(t)
    else if (t.due_at === today) groups.today.push(t)
    else if (t.due_at <= weekEnd) groups.week.push(t)
    else groups.later.push(t)
  }
  return groups
}

const SECTION_STYLE = {
  overdue: { label: '逾期', cls: 'sec-overdue' },
  today: { label: '今天', cls: 'sec-today' },
  week: { label: '本周', cls: 'sec-week' },
  later: { label: '更远', cls: 'sec-later' },
  none: { label: '无日期', cls: 'sec-none' },
}

export default function TimelineView({ refreshKey, refresh }) {
  const [tasks, setTasks] = useState([])
  const [title, setTitle] = useState('')
  const [due, setDue] = useState('')

  const load = async () => setTasks(await api.listTasks())
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey])

  const groups = groupByDate(tasks)

  const add = async (e) => {
    e.preventDefault()
    const t = title.trim()
    if (!t) return
    await api.createTask({ title: t, due_at: due || null })
    setTitle('')
    setDue('')
    await load()
    refresh?.()
  }

  const toggle = async (t) => {
    await api.updateTask(t.id, { status: t.status === 'done' ? 'backlog' : 'done' })
    await load()
    refresh?.()
  }

  return (
    <div className="view">
      <h2>时间轴</h2>
      <form className="inline-form" onSubmit={add}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="新任务…" />
        <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
        <button type="submit">添加</button>
      </form>

      {Object.entries(groups).map(([key, list]) =>
        list.length > 0 ? (
          <div key={key} className={`timeline-section ${SECTION_STYLE[key].cls}`}>
            <div className="section-head">
              {SECTION_STYLE[key].label}
              <span className="section-count">{list.length}</span>
            </div>
            {list.map((t) => (
              <div key={t.id} className="task">
                <label className="task-row">
                  <input type="checkbox" checked={false} onChange={() => toggle(t)} />
                  <span className="task-title">{t.title}</span>
                  <span className="task-pri">P{t.priority}</span>
                  <span className="task-due">{t.due_at?.slice(5)}</span>
                </label>
              </div>
            ))}
          </div>
        ) : null,
      )}

      <h2 className="mt">已完成</h2>
      {tasks.filter((t) => t.status === 'done').map((t) => (
        <div key={t.id} className="task done">
          <label className="task-row">
            <input type="checkbox" checked onChange={() => toggle(t)} />
            <span className="task-title">{t.title}</span>
          </label>
        </div>
      ))}
    </div>
  )
}
