import { useEffect, useState } from 'react'
import { api } from '../api.js'

function localToday() {
  // 本地日期（不用 toISOString——那是 UTC，东八区凌晨会差一天）
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// Date 对象 → 本地日期串（同上理由）
const localDateStr = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

function groupByDate(tasks) {
  const today = localToday()
  const weekEnd = new Date(Date.now() + 7 * 86400000)
  const weekEndStr = `${weekEnd.getFullYear()}-${String(weekEnd.getMonth() + 1).padStart(2, '0')}-${String(weekEnd.getDate()).padStart(2, '0')}`
  const groups = { overdue: [], today: [], week: [], later: [], none: [] }
  for (const t of tasks) {
    if (t.status === 'done' || t.status === 'cancelled') continue
    const due = (t.due_at || '').slice(0, 10) // 带时间的截止也按日期分组
    if (!due) groups.none.push(t)
    else if (due < today) groups.overdue.push(t)
    else if (due === today) groups.today.push(t)
    else if (due <= weekEndStr) groups.week.push(t)
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

// ── 日程表：Notion 风格周历（7 天 × 6:00-23:00，日程块按类型染色） ──

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']
const ENTRY_TYPES = [
  { key: 'course', label: '课程' },
  { key: 'meeting', label: '会议' },
  { key: 'outing', label: '外出' },
  { key: 'personal', label: '个人' },
]
const entryLabel = (k) => ENTRY_TYPES.find((t) => t.key === k)?.label || k

const HOUR_H = 56          // 每小时像素高
const DAY_START = 6        // 6:00 起
const DAY_END = 23         // 23:00 止
const DAY_H = (DAY_END - DAY_START) * HOUR_H

const minutesOf = (hm) => { const [h, m] = hm.split(':').map(Number); return h * 60 + m }
const hmOf = (min) => `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`

const EMPTY_FORM = {
  kind: 'event',           // event=一次性（日程的默认形态）/ slot=每周重复（课程、固定组会）
  title: '', entry_type: 'personal',
  date: '',                // kind=event 用（YYYY-MM-DD，打开表单时按点击列预填）
  day_of_week: 0, week_pattern: 'all', // kind=slot 用
  start_time: '08:00', end_time: '09:00', location: '',
}

function ScheduleSection({ refreshKey, refresh }) {
  const [week, setWeek] = useState(null)      // {start, days:[{date, day_of_week, slots}]}
  const [offset, setOffset] = useState(0)     // 相对本周偏移（周数）
  const [form, setForm] = useState(EMPTY_FORM)
  const [editing, setEditing] = useState(null) // null | {kind:'event'|'slot', id}
  const [error, setError] = useState('')

  // 周起点：本周一 + offset 周（本地日期计算，不用 toISOString）
  const weekStartDate = () => {
    const d = new Date()
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7) + offset * 7)
    return d
  }

  const load = async () => setWeek(await api.weekSchedule(localDateStr(weekStartDate())))
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey, offset])

  const closeModal = () => { setEditing(null); setForm(EMPTY_FORM); setError('') }

  const submit = async (e) => {
    e.preventDefault()
    if (!form.title.trim()) return
    setError('')
    try {
      const base = {
        title: form.title.trim(), entry_type: form.entry_type,
        start_time: form.start_time, end_time: form.end_time,
        location: form.location.trim() || null,
      }
      if (form.kind === 'event') {
        const payload = { ...base, date: form.date || localToday() }
        if (editing?.kind === 'event') await api.updateEvent(editing.id, payload)
        else await api.createEvent(payload)
      } else {
        const payload = { ...base, day_of_week: form.day_of_week, week_pattern: form.week_pattern }
        if (editing?.kind === 'slot') await api.updateSlot(editing.id, payload)
        else await api.createSlot(payload)
      }
      closeModal()
      await load()
      refresh?.()
    } catch (err) {
      setError(err.message || '保存失败')
    }
  }

  const remove = async () => {
    if (!editing) return
    if (!window.confirm('删除这条日程？')) return
    if (editing.kind === 'event') await api.deleteEvent(editing.id)
    else await api.deleteSlot(editing.id)
    closeModal()
    await load()
    refresh?.()
  }

  const startEdit = (s, dateStr) => {
    const isEvent = s.source === 'event'
    setEditing({ kind: isEvent ? 'event' : 'slot', id: s.id })
    setForm({
      ...EMPTY_FORM,
      kind: isEvent ? 'event' : 'slot',
      title: s.title, entry_type: s.entry_type,
      date: s.date || dateStr || localToday(),
      day_of_week: s.day_of_week ?? 0, week_pattern: s.week_pattern || 'all',
      start_time: s.start_time, end_time: s.end_time,
      location: s.location || '',
    })
  }

  // 点空白时段：按点击位置预填日期（该列的真实日期，一次性）和时间（半小时对齐，默认 1 小时）
  const onEmptyClick = (e, day) => {
    const y = e.nativeEvent.offsetY
    const raw = DAY_START * 60 + (y / HOUR_H) * 60
    const startMin = Math.max(Math.round(raw / 30) * 30, DAY_START * 60)
    const endMin = Math.min(startMin + 60, DAY_END * 60)
    setEditing(null)
    setForm({
      ...EMPTY_FORM, date: day.date, day_of_week: day.day_of_week,
      start_time: hmOf(startMin), end_time: hmOf(endMin),
    })
  }

  const todayStr = localToday()
  const label = week
    ? `${week.start.slice(5).replace('-', '.')} – ${week.days[6].date.slice(5).replace('-', '.')}`
    : ''

  return (
    <section className="schedule-section">
      <div className="week-toolbar">
        <h2>日程表</h2>
        <div className="week-nav">
          <button onClick={() => setOffset((o) => o - 1)} title="上一周">‹</button>
          <span className="week-label">{label}</span>
          <button onClick={() => setOffset((o) => o + 1)} title="下一周">›</button>
          {offset !== 0 && <button className="week-today" onClick={() => setOffset(0)}>回到本周</button>}
        </div>
      </div>

      {week && (
        <div className="week-cal">
          <div className="week-gutter">
            {Array.from({ length: DAY_END - DAY_START }, (_, i) => (
              <div className="gutter-cell" key={i} style={{ height: HOUR_H }}>
                {`${String(DAY_START + i).padStart(2, '0')}:00`}
              </div>
            ))}
          </div>
          {week.days.map((day) => (
            <div key={day.date} className={`week-day ${day.date === todayStr ? 'is-today' : ''}`}>
              <div className="day-head">
                周{WEEKDAYS[day.day_of_week]}
                <span className="day-date">{day.date.slice(5).replace('-', '.')}</span>
              </div>
              <div className="day-body" style={{ height: DAY_H }} onClick={(e) => onEmptyClick(e, day)}>
                {day.slots.map((s) => {
                  // 显示范围钳制（6:00-23:00 之外的极少数日程压缩到边缘）
                  const sMin = Math.max(minutesOf(s.start_time), DAY_START * 60)
                  const eMin = Math.min(Math.max(minutesOf(s.end_time), sMin + 20), DAY_END * 60)
                  const top = ((sMin - DAY_START * 60) / 60) * HOUR_H
                  const height = ((eMin - sMin) / 60) * HOUR_H - 2
                  const hover = `${s.title}\n${s.start_time}–${s.end_time}` +
                    `${s.location ? ` @${s.location}` : ''}\n${entryLabel(s.entry_type)}` +
                    `${s.source === 'event' ? '·一次性' : s.week_pattern !== 'all' ? (s.week_pattern === 'odd' ? '·单周' : '·双周') : ''}`
                  return (
                    <div
                      key={`${s.source || 'slot'}-${s.id}`}
                      className={`slot-block slot-${s.entry_type}${s.source === 'event' ? ' slot-once' : ''}`}
                      style={{ top, height }}
                      title={hover}
                      onClick={(e) => { e.stopPropagation(); startEdit(s, day.date) }}
                    >
                      <div className="slot-time">{s.start_time}</div>
                      <div className="slot-title">{s.title}</div>
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {(editing || form.title !== '' || form.start_time !== EMPTY_FORM.start_time) && (
        <div className="modal-mask" onClick={closeModal}>
          <div className="modal slot-modal" onClick={(e) => e.stopPropagation()}>
            <h3>{editing ? '编辑日程' : '添加日程'}</h3>
            <form className="slot-form" onSubmit={submit}>
              <input
                value={form.title}
                onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
                placeholder="日程名（如：牙医 / 组会 / 公司金融）…"
                autoFocus
              />
              <div className="form-row kind-toggle">
                <button
                  type="button"
                  className={form.kind === 'event' ? 'on' : ''}
                  onClick={() => setForm((f) => ({ ...f, kind: 'event' }))}
                >一次性</button>
                <button
                  type="button"
                  className={form.kind === 'slot' ? 'on' : ''}
                  onClick={() => setForm((f) => ({ ...f, kind: 'slot' }))}
                >每周重复</button>
                <select value={form.entry_type} onChange={(e) => setForm((f) => ({ ...f, entry_type: e.target.value }))}>
                  {ENTRY_TYPES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
                </select>
              </div>
              <div className="form-row">
                {form.kind === 'event' ? (
                  <input type="date" value={form.date} onChange={(e) => setForm((f) => ({ ...f, date: e.target.value }))} />
                ) : (
                  <>
                    <select value={form.day_of_week} onChange={(e) => setForm((f) => ({ ...f, day_of_week: Number(e.target.value) }))}>
                      {WEEKDAYS.map((d, i) => <option key={d} value={i}>{`周${d}`}</option>)}
                    </select>
                    <select value={form.week_pattern} onChange={(e) => setForm((f) => ({ ...f, week_pattern: e.target.value }))}>
                      <option value="all">每周</option>
                      <option value="odd">单周</option>
                      <option value="even">双周</option>
                    </select>
                  </>
                )}
              </div>
              <div className="form-row">
                <input type="time" value={form.start_time} onChange={(e) => setForm((f) => ({ ...f, start_time: e.target.value }))} />
                <span className="hint-inline">到</span>
                <input type="time" value={form.end_time} onChange={(e) => setForm((f) => ({ ...f, end_time: e.target.value }))} />
                <input
                  value={form.location}
                  onChange={(e) => setForm((f) => ({ ...f, location: e.target.value }))}
                  placeholder="地点（可空）"
                />
              </div>
              {error && <p className="form-error">{error}</p>}
              <div className="form-row slot-actions">
                <button className="small-btn primary" type="submit">{editing ? '保存' : '添加'}</button>
                <button className="small-btn" type="button" onClick={closeModal}>取消</button>
                {editing && <button className="small-btn danger" type="button" onClick={remove}>删除</button>}
              </div>
            </form>
          </div>
        </div>
      )}
    </section>
  )
}

// ── 任务行（勾完成 / 编辑 / 删除）──

function TaskRow({ t, onToggle, onEdit, onDelete, projectOptions }) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(t.title)
  const [due, setDue] = useState((t.due_at || '').slice(0, 10))
  const [priority, setPriority] = useState(t.priority)
  const [projectId, setProjectId] = useState(t.project_id ?? '')

  const save = async () => {
    await onEdit(t.id, {
      title: title.trim() || t.title, due_at: due || null, priority,
      project_id: projectId === '' ? null : Number(projectId),
    })
    setEditing(false)
  }

  if (editing) {
    return (
      <div className="task editing-row">
        <input className="edit-title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
        <select value={priority} onChange={(e) => setPriority(Number(e.target.value))} title="优先级 1 高 - 5 低">
          {[1, 2, 3, 4, 5].map((p) => <option key={p} value={p}>P{p}</option>)}
        </select>
        <select value={projectId} onChange={(e) => setProjectId(e.target.value === '' ? '' : Number(e.target.value))} title="挂靠的事">
          <option value="">不挂靠</option>
          {projectOptions.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <button className="small-btn primary" onClick={save}>存</button>
        <button className="small-btn" onClick={() => setEditing(false)}>取消</button>
      </div>
    )
  }

  return (
    <div className="task">
      <label className="task-row">
        <input type="checkbox" checked={false} onChange={() => onToggle(t)} />
        <span className="task-title">{t.title}</span>
        <span className="task-pri">P{t.priority}</span>
        <span className="task-due">{t.due_at?.slice(5, 10)}</span>
      </label>
      <span className="task-ops">
        <button className="icon-btn" onClick={() => setEditing(true)} title="编辑">✎</button>
        <button className="task-del" onClick={() => onDelete(t.id)} title="删除">×</button>
      </span>
    </div>
  )
}

export default function TimelineView({ refreshKey, refresh }) {
  const [tasks, setTasks] = useState([])
  const [projects, setProjects] = useState([]) // 进行中的事（挂靠下拉用）
  const [title, setTitle] = useState('')
  const [due, setDue] = useState('')
  const [projectId, setProjectId] = useState('')

  const load = async () => {
    const [ts, ps] = await Promise.all([api.listTasks(), api.listProjects()])
    setTasks(ts)
    setProjects(ps.filter((p) => p.status !== 'closed'))
  }
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey])

  const groups = groupByDate(tasks)

  const add = async (e) => {
    e.preventDefault()
    const t = title.trim()
    if (!t) return
    await api.createTask({
      title: t, due_at: due || null,
      project_id: projectId === '' ? null : Number(projectId),
    })
    setTitle('')
    setDue('')
    setProjectId('')
    await load()
    refresh?.()
  }

  const toggle = async (t) => {
    await api.updateTask(t.id, { status: t.status === 'done' ? 'backlog' : 'done' })
    await load()
    refresh?.()
  }

  const edit = async (id, patch) => {
    await api.updateTask(id, patch)
    await load()
    refresh?.()
  }

  const remove = async (id) => {
    if (!window.confirm('删除这个任务？（挂靠的进展日志会保留）')) return
    await api.deleteTask(id)
    await load()
    refresh?.()
  }

  return (
    <div className="view">
      <ScheduleSection refreshKey={refreshKey} refresh={refresh} />

      <h2 className="mt">任务时间轴</h2>
      <form className="inline-form" onSubmit={add}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="新任务…" />
        <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
        <select value={projectId} onChange={(e) => setProjectId(e.target.value === '' ? '' : Number(e.target.value))} title="挂靠的事">
          <option value="">不挂靠</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
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
              <TaskRow key={t.id} t={t} onToggle={toggle} onEdit={edit} onDelete={remove} projectOptions={projects} />
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
      {tasks.filter((t) => t.status === 'done').length === 0 && (
        <p className="empty">还没有完成的任务。</p>
      )}
    </div>
  )
}
