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

// ── 日程表（M4 FR-4.1）：课程只是日程的一类 ──

const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const ENTRY_TYPES = [
  { key: 'course', label: '课程' },
  { key: 'meeting', label: '会议' },
  { key: 'outing', label: '外出' },
  { key: 'personal', label: '个人' },
]
const entryLabel = (k) => ENTRY_TYPES.find((t) => t.key === k)?.label || k

function ScheduleSection({ refreshKey }) {
  const [slots, setSlots] = useState([])
  const [today, setToday] = useState([])
  const [form, setForm] = useState({
    title: '', entry_type: 'course', day_of_week: 0,
    start_time: '08:00', end_time: '09:40', location: '', week_pattern: 'all',
  })

  const load = async () => {
    const [all, t] = await Promise.all([api.listSchedule(), api.todaySchedule()])
    setSlots(all)
    setToday(t.slots)
  }
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey])

  const add = async (e) => {
    e.preventDefault()
    if (!form.title.trim()) return
    await api.createSlot({
      ...form,
      title: form.title.trim(),
      location: form.location.trim() || null,
    })
    setForm((f) => ({ ...f, title: '', location: '' }))
    await load()
  }

  const remove = async (id) => {
    await api.deleteSlot(id)
    await load()
  }

  return (
    <section className="schedule-section">
      <h3>今日日程</h3>
      {today.length === 0 ? (
        <p className="empty">今天没有日程安排（小白看到的就是空）。</p>
      ) : (
        <div className="today-schedule">
          {today.map((s) => (
            <div key={s.id} className="schedule-block">
              <span className="schedule-time">{s.start_time}–{s.end_time}</span>
              <span className="schedule-title">{s.title}</span>
              <span className="schedule-type">{entryLabel(s.entry_type)}</span>
              {s.location && <span className="schedule-loc">@{s.location}</span>}
            </div>
          ))}
        </div>
      )}

      <h3 className="mt">周课表管理</h3>
      <form className="schedule-form" onSubmit={add}>
        <input
          value={form.title}
          onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
          placeholder="日程名（如：算法设计 / 导师组会）…"
        />
        <select value={form.entry_type} onChange={(e) => setForm((f) => ({ ...f, entry_type: e.target.value }))}>
          {ENTRY_TYPES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
        </select>
        <select value={form.day_of_week} onChange={(e) => setForm((f) => ({ ...f, day_of_week: Number(e.target.value) }))}>
          {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
        </select>
        <input type="time" value={form.start_time} onChange={(e) => setForm((f) => ({ ...f, start_time: e.target.value }))} />
        <span className="hint">到</span>
        <input type="time" value={form.end_time} onChange={(e) => setForm((f) => ({ ...f, end_time: e.target.value }))} />
        <select value={form.week_pattern} onChange={(e) => setForm((f) => ({ ...f, week_pattern: e.target.value }))}>
          <option value="all">每周</option>
          <option value="odd">单周</option>
          <option value="even">双周</option>
        </select>
        <input
          value={form.location}
          onChange={(e) => setForm((f) => ({ ...f, location: e.target.value }))}
          placeholder="地点（可空）"
          className="loc-input"
        />
        <button type="submit">添加</button>
      </form>

      {slots.length === 0 ? (
        <p className="empty">还没有日程。课程、组会、外出、个人安排都加进来，小白的简报和提醒会基于它运转。</p>
      ) : (
        <div className="week-schedule">
          {WEEKDAYS.map((d, i) => {
            const daySlots = slots.filter((s) => s.day_of_week === i)
            if (daySlots.length === 0) return null
            return (
              <div key={d} className="day-col">
                <div className="day-head">{d}</div>
                {daySlots.map((s) => (
                  <div key={s.id} className="schedule-block">
                    <span className="schedule-time">{s.start_time}</span>
                    <span className="schedule-title">{s.title}</span>
                    {s.week_pattern !== 'all' && (
                      <span className="schedule-pattern">{s.week_pattern === 'odd' ? '单' : '双'}</span>
                    )}
                    <button className="icon-btn" onClick={() => remove(s.id)} title="删除">×</button>
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
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
      <ScheduleSection refreshKey={refreshKey} />

      <h2 className="mt">任务时间轴</h2>
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
