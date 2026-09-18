import { useEffect, useState } from 'react'
import { api } from '../api.js'

const TRIGGER_TYPES = [
  { key: 'once', label: '一次' },
  { key: 'daily', label: '每天' },
  { key: 'weekly', label: '每周' },
  { key: 'interval', label: '间隔' },
]

export default function LifeView({ refreshKey, refresh }) {
  const [reminders, setReminders] = useState([])
  const [title, setTitle] = useState('')
  const [type, setType] = useState('once')
  const [value, setValue] = useState('')

  const load = async () => setReminders(await api.listReminders())
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey])

  const add = async (e) => {
    e.preventDefault()
    const t = title.trim()
    if (!t) return
    await api.createReminder({ title: t, trigger_type: type, trigger_value: value || null })
    setTitle('')
    setValue('')
    await load()
    refresh?.()
  }

  return (
    <div className="view">
      <h2>生活角</h2>
      <p className="hint">快递、房间、习惯打卡——和正事分开，但一眼可见。到点提醒在 M6 接入。</p>

      <form className="inline-form" onSubmit={add}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="提醒事项（如：拿快递）…" />
        <select value={type} onChange={(e) => setType(e.target.value)}>
          {TRIGGER_TYPES.map((t) => (
            <option key={t.key} value={t.key}>{t.label}</option>
          ))}
        </select>
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="时间/说明（可空）" />
        <button type="submit">添加</button>
      </form>

      {reminders.length === 0 && <p className="empty">没有生活提醒。想到什么杂事就记在这里。</p>}

      <div className="reminder-list">
        {reminders.map((r) => (
          <div key={r.id} className={r.active ? 'reminder' : 'reminder inactive'}>
            <label className="task-row">
              <input type="checkbox" checked={!r.active} onChange={() => api.toggleReminder(r.id).then(load)} />
              <span className="task-title">{r.title}</span>
              <span className="task-due">
                {r.trigger_type === 'once' ? r.trigger_value : TRIGGER_TYPES.find((t) => t.key === r.trigger_type)?.label}
              </span>
            </label>
            <button className="task-del" onClick={() => api.deleteReminder(r.id).then(load)}>×</button>
          </div>
        ))}
      </div>
    </div>
  )
}
