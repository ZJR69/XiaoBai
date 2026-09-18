import { useEffect, useState } from 'react'
import { api } from '../api.js'

const TRIGGER_TYPES = [
  { key: 'once', label: '一次' },
  { key: 'daily', label: '每天' },
  { key: 'weekly', label: '每周' },
  { key: 'interval', label: '间隔' },
]
const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

export default function LifeView({ refreshKey, refresh }) {
  const [reminders, setReminders] = useState([])
  const [title, setTitle] = useState('')
  const [type, setType] = useState('once')
  // 结构化触发值：once=日期+可选时间 / daily=时间 / weekly=星期+时间 / interval=小时数
  const [date, setDate] = useState('')
  const [time, setTime] = useState('09:00')
  const [weekday, setWeekday] = useState(0)
  const [hours, setHours] = useState('4')
  const [error, setError] = useState('')

  const load = async () => setReminders(await api.listReminders())
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey])

  // 按类型拼装触发值（后端会再校验归一化）
  const buildValue = () => {
    if (type === 'once') return date ? (time ? `${date}T${time}` : date) : null
    if (type === 'daily') return time
    if (type === 'weekly') return `${weekday}-${time}`
    return hours
  }

  const describe = (r) => {
    const v = r.trigger_value || ''
    if (r.trigger_type === 'once') return v.replace('T', ' ')
    if (r.trigger_type === 'daily') return `每天 ${v}`
    if (r.trigger_type === 'weekly') {
      const [w, t] = v.split('-', 2)
      return `每${WEEKDAYS[Number(w)] || '?'} ${t || ''}`
    }
    if (r.trigger_type === 'interval') return `每 ${v} 小时`
    return v
  }

  const add = async (e) => {
    e.preventDefault()
    const t = title.trim()
    if (!t) return
    setError('')
    try {
      await api.createReminder({ title: t, trigger_type: type, trigger_value: buildValue() })
      setTitle('')
      await load()
      refresh?.()
    } catch (err) {
      setError(err.message || '添加失败')
    }
  }

  return (
    <div className="view">
      <h2>生活角</h2>
      <p className="hint">快递、房间、习惯打卡——和正事分开，但一眼可见。到点自动提醒。</p>

      <form className="inline-form life-form" onSubmit={add}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="提醒事项（如：拿快递）…" />
        <select value={type} onChange={(e) => setType(e.target.value)}>
          {TRIGGER_TYPES.map((t) => (
            <option key={t.key} value={t.key}>{t.label}</option>
          ))}
        </select>
        {type === 'once' && (
          <>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
            <input type="time" value={time} onChange={(e) => setTime(e.target.value)} title="当天几点提醒（可选）" />
          </>
        )}
        {type === 'daily' && (
          <input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
        )}
        {type === 'weekly' && (
          <>
            <select value={weekday} onChange={(e) => setWeekday(Number(e.target.value))}>
              {WEEKDAYS.map((d, i) => (
                <option key={d} value={i}>{d}</option>
              ))}
            </select>
            <input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
          </>
        )}
        {type === 'interval' && (
          <input
            type="number" min="0.5" step="0.5" value={hours}
            onChange={(e) => setHours(e.target.value)} title="每隔几小时提醒一次"
            style={{ width: 90 }}
          />
        )}
        <button type="submit">添加</button>
      </form>
      {error && <p className="form-error">{error}</p>}

      {reminders.length === 0 && <p className="empty">没有生活提醒。想到什么杂事就记在这里。</p>}

      <div className="reminder-list">
        {reminders.map((r) => (
          <div key={r.id} className={r.active ? 'reminder' : 'reminder inactive'}>
            <label className="task-row">
              <input type="checkbox" checked={!r.active} onChange={() => api.toggleReminder(r.id).then(load)} />
              <span className="task-title">{r.title}</span>
              <span className="task-due">{describe(r)}</span>
            </label>
            <button className="task-del" onClick={() => api.deleteReminder(r.id).then(load)}>×</button>
          </div>
        ))}
      </div>
    </div>
  )
}
