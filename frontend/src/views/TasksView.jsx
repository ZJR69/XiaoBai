import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'

function localToday() {
  // 本地日期（不用 toISOString——那是 UTC，东八区凌晨会差一天）
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const dateStr = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

// —— 保存筛选：本地求值器（2026-10-10；数据量小，后端零查询引擎）——
// YYYY-MM-DD[THH:MM] → 本地 Date（手动拼避免 UTC 偏移坑）；无法解析返回 null
function parseLocalDate(v) {
  if (!v) return null
  const m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/)
  if (!m) return null
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4] || 0), Number(m[5] || 0))
}

// 相对日期单点：now / now±Nd / now±Nw / now/d（/d=取整当天 0 点）；非相对表达式走 parseLocalDate
function parseFilterDate(v) {
  if (typeof v !== 'string') return null
  const m = v.match(/^now(?:([+-])(\d+)([dw]))?(\/d)?$/)
  if (!m) return parseLocalDate(v)
  const d = new Date()
  if (m[2]) d.setDate(d.getDate() + (m[1] === '-' ? -1 : 1) * Number(m[2]) * (m[3] === 'w' ? 7 : 1))
  if (m[4]) d.setHours(0, 0, 0, 0)
  return d
}

// evalFilter(task, filterJson)：all 全满足（&&）或 any 任一满足（||，取并集）。
// null 参与比较一律 false（没日期的任务不进日期筛选）；定义损坏时不过滤（不误伤）。
export function evalFilter(task, filterJson) {
  let spec
  try { spec = JSON.parse(filterJson) } catch { return true }
  if (!spec || typeof spec !== 'object') return true
  const DATE_FIELDS = ['due_at', 'start_at', 'created_at']
  const hit = (c) => {
    if (!c || typeof c !== 'object') return false
    const { field: f, op, value: val } = c
    if (DATE_FIELDS.includes(f)) {
      const a = parseLocalDate(task[f])
      if (!a) return false
      if (op === 'in' && Array.isArray(val)) return val.some((x) => {
        const b = parseFilterDate(x); return b && a.getTime() === b.getTime()
      })
      const b = parseFilterDate(val)
      if (!b) return false
      const av = a.getTime(), bv = b.getTime()
      if (op === '=') return av === bv
      if (op === '!=') return av !== bv
      if (op === '>') return av > bv
      if (op === '>=') return av >= bv
      if (op === '<') return av < bv
      if (op === '<=') return av <= bv
      return false
    }
    const actual = f === 'done' ? task.status === 'done' : task[f]
    if (actual === null || actual === undefined) return false
    if (op === 'in') return Array.isArray(val) && val.includes(actual)
    if (op === '=') return actual === val
    if (op === '!=') return actual !== val
    if (op === '>') return actual > val
    if (op === '>=') return actual >= val
    if (op === '<') return actual < val
    if (op === '<=') return actual <= val
    return false
  }
  const allOk = Array.isArray(spec.all) ? spec.all.every(hit) : true
  const anyOk = Array.isArray(spec.any) && spec.any.length > 0 ? spec.any.some(hit) : false
  return allOk || anyOk
}

function groupByDate(tasks) {
  const today = localToday()
  const weekEnd = new Date(Date.now() + 7 * 86400000)
  const weekEndStr = dateStr(weekEnd)
  const groups = { overdue: [], today: [], week: [], later: [], none: [] }
  for (const t of tasks) {
    if (t.status === 'done' || t.status === 'cancelled') continue
    const due = (t.due_at || '').slice(0, 10)
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
  none: { label: '池子（无日期）', cls: 'sec-none' },
}

// RRULE → 人读文案（best-effort，覆盖常见花样；复杂规则如实回退「重复」）
function rruleText(rrule) {
  if (!rrule) return ''
  const s = rrule.toUpperCase()
  const interval = Number(s.match(/INTERVAL=(\d+)/)?.[1] || 1)
  const byday = s.match(/BYDAY=([A-Z,]+)/)?.[1] || ''
  const DOWS = { MO: '一', TU: '二', WE: '三', TH: '四', FR: '五', SA: '六', SU: '日' }
  let base = '重复'
  if (s.includes('FREQ=DAILY')) base = interval > 1 ? `每隔${interval}天` : '每天'
  else if (s.includes('FREQ=WEEKLY')) {
    const dows = byday.split(',').filter(Boolean).map((d) => DOWS[d] || '').join('/')
    base = `${interval > 1 ? `每隔${interval}周` : '每周'}${dows ? `周${dows}` : ''}`
  } else if (s.includes('FREQ=MONTHLY')) base = interval > 1 ? `每隔${interval}月` : '每月'
  else if (s.includes('FREQ=YEARLY')) base = '每年'
  if (s.includes('COUNT=')) base += `·共${s.match(/COUNT=(\d+)/)[1]}次`
  return base
}

// 任务行（2026-09-26 重构，对标 Todoist/TickTick）：勾选框=完成唯一入口；点标题=原地改；
// 📅 快捷日期弹出；✎ 属性事后改（优先级/挂靠，选中即存）；× 删除。不再整行铺表单。
function TaskRow({ t, onToggle, onEdit, onDelete, projectOptions }) {
  const [editing, setEditing] = useState(false) // 标题原地编辑中
  const [title, setTitle] = useState(t.title)
  const [pop, setPop] = useState(null) // 'date' | 'detail' | null
  const wrapRef = useRef(null)

  // 点行外任意处收起弹出层
  useEffect(() => {
    if (!pop) return
    const onDoc = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setPop(null)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [pop])

  const saveTitle = () => {
    const v = title.trim()
    if (v && v !== t.title) onEdit(t.id, { title: v })
    setEditing(false)
  }

  const setDueOffset = (days) => {
    const d = new Date()
    d.setDate(d.getDate() + days)
    onEdit(t.id, { due_at: dateStr(d) })
    setPop(null)
  }

  const project = projectOptions.find((p) => p.id === t.project_id)

  return (
    <div className="task-wrap" ref={wrapRef}>
      <div className="task">
        {editing ? (
          <input
            className="edit-title" autoFocus value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') saveTitle()
              else if (e.key === 'Escape') { setTitle(t.title); setEditing(false) }
            }}
            onBlur={saveTitle}
          />
        ) : (
          <>
            <input type="checkbox" checked={false} onChange={() => onToggle(t)} title="完成" />
            <span className="task-title" onClick={() => setEditing(true)} title="点击修改">{t.title}</span>
            {t.rrule && <span className="task-due" title={`重复：${rruleText(t.rrule)}（${t.repeat_mode === 'done' ? '从完成起算' : '按截止节奏'}）`}>↻</span>}
            {project && <span className="task-proj">{project.name}</span>}
            <span className="task-pri">P{t.priority}</span>
            {t.due_at && <span className="task-due">{t.due_at.slice(5, 10)}</span>}
            {(t.start_at || t.end_at) && (
              <span className="task-due" title={`计划窗口（${t.start_at?.slice(0, 10) || '?'} ~ ${t.end_at?.slice(0, 10) || '?'}），与死线是两回事`}>
                {t.start_at ? t.start_at.slice(5, 10) : '?'}~{t.end_at ? t.end_at.slice(5, 10) : '?'}
              </span>
            )}
          </>
        )}
        <span className="task-ops">
          <button className="task-op" onClick={() => setPop(pop === 'date' ? null : 'date')} title="设日期">
            <svg viewBox="0 0 24 24"><rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M3.5 9.5h17M8 3v3.6M16 3v3.6"/></svg>
          </button>
          <button className="task-op" onClick={() => setPop(pop === 'detail' ? null : 'detail')} title="优先级 / 挂靠">
            <svg viewBox="0 0 24 24"><path d="M15.5 4.5l4 4L8 20H4v-4L15.5 4.5Z"/><path d="M13.5 6.5l4 4"/></svg>
          </button>
          <button className="task-del" onClick={() => onDelete(t.id)} title="删除">×</button>
        </span>
      </div>
      {pop === 'date' && (
        <div className="task-pop">
          <button className="small-btn" onClick={() => setDueOffset(0)}>今天</button>
          <button className="small-btn" onClick={() => setDueOffset(1)}>明天</button>
          <input
            type="date" value={t.due_at?.slice(0, 10) || ''} title="自选日期"
            onChange={(e) => { if (e.target.value) { onEdit(t.id, { due_at: e.target.value }); setPop(null) } }}
          />
          {t.due_at && <button className="small-btn" onClick={() => { onEdit(t.id, { due_at: null }); setPop(null) }}>清除</button>}
        </div>
      )}
      {pop === 'detail' && (
        <div className="task-detail">
          <label>
            优先级
            <select value={t.priority} onChange={(e) => onEdit(t.id, { priority: Number(e.target.value) })} title="1 高 - 5 低">
              {[1, 2, 3, 4, 5].map((p) => <option key={p} value={p}>P{p}</option>)}
            </select>
          </label>
          <label>
            挂靠
            <select value={t.project_id ?? ''} onChange={(e) => onEdit(t.id, { project_id: e.target.value === '' ? null : Number(e.target.value) })}>
              <option value="">不挂靠</option>
              {projectOptions.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          {t.rrule && (
            <label title="重复规则由对话管理（对小白说「改成每两周」「取消重复」）">
              重复
              <span className="task-due">{rruleText(t.rrule)}·{t.repeat_mode === 'done' ? '从完成起算' : '按截止'}</span>
            </label>
          )}
          <label title="计划窗口：打算什么时候开始做（可选；清空=移除）">
            开始
            <input type="date" value={t.start_at?.slice(0, 10) || ''} onChange={(e) => onEdit(t.id, { start_at: e.target.value || null })} />
          </label>
          <label title="计划窗口：打算什么时候做完（可选；清空=移除；不能早于开始）">
            结束
            <input type="date" value={t.end_at?.slice(0, 10) || ''} onChange={(e) => onEdit(t.id, { end_at: e.target.value || null })} />
          </label>
        </div>
      )}
    </div>
  )
}

// 任务池（小待办的家）：tasks 表唯一数据源，此页是完整管理视图；时间轴当天列是它的轻量投影
// activeFilter（可选）：保存筛选行 {title, filter_json}，命中 evalFilter 的任务才渲染
export default function TasksView({ refreshKey, refresh, activeFilter }) {
  const [tasks, setTasks] = useState([])
  const [projects, setProjects] = useState([])
  const [title, setTitle] = useState('')
  const [showDone, setShowDone] = useState(false)

  const load = async () => {
    const [ts, ps] = await Promise.all([api.listTasks(), api.listProjects()])
    setTasks(ts)
    setProjects(ps.filter((p) => p.status !== 'closed'))
  }
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey])

  // 筛选生效时先过一遍（求值在前端本地）
  const visible = activeFilter ? tasks.filter((t) => evalFilter(t, activeFilter.filter_json)) : tasks
  const groups = groupByDate(visible)
  const done = visible
    .filter((t) => t.status === 'done')
    .sort((a, b) => (b.completed_at || '').localeCompare(a.completed_at || ''))

  const add = async (e) => {
    e.preventDefault()
    const t = title.trim()
    if (!t) return
    await api.createTask({ title: t, due_at: null, project_id: null })
    setTitle('')
    await load()
    refresh?.()
  }

  const toggle = async (t) => {
    await api.updateTask(t.id, { status: t.status === 'done' ? 'backlog' : 'done' })
    await load()
    refresh?.()
  }

  const edit = async (id, patch) => {
    try {
      await api.updateTask(id, patch)
    } catch (err) {
      alert(err.message || '保存失败') // 后端 400 如实上浮（如「结束日期不能早于开始日期」）
    }
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
    <div className="wb-section">
      <div className="wb-panel-head">任务池</div>
      <div className="wb-panel">
      <form className="inline-form" onSubmit={add}>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="洗头、看完那个视频…（Enter 收下）" />
          <button type="submit" title="收下（Enter）">＋</button>
        </form>

      {Object.entries(groups).map(([key, list]) =>
        list.length > 0 ? (
          <div key={key} className={`timeline-section ${SECTION_STYLE[key].cls}`}>
            <div className="section-head">
              {SECTION_STYLE[key].label}
              <span className="section-count">{list.length}</span>
            </div>
            {list.map((t) => (
              <TaskRow
                key={t.id} t={t}
                onToggle={toggle} onEdit={edit} onDelete={remove}
                projectOptions={projects}
              />
            ))}
          </div>
        ) : null,
      )}
      {visible.filter((t) => t.status !== 'done').length === 0 && (
        <p className="empty">{activeFilter ? `筛选「${activeFilter.title}」下没有任务` : '池子空了。随手记一件想做的小事？'}</p>
      )}

      {/* 已完成：保留可见（填充 checkbox + 划线 + 浅色），默认折叠防刷屏 */}
      {done.length > 0 && (
        <div className="closed-fold">
          <button className="closed-toggle" onClick={() => setShowDone((s) => !s)}>
            {showDone ? '▾' : '▸'} 已完成（{done.length}）
          </button>
          {showDone && (
            <div className="closed-list">
              {done.map((t) => (
                <div key={t.id} className="task done">
                  <label className="task-row">
                    <input type="checkbox" checked onChange={() => toggle(t)} />
                    <span className="task-title">{t.title}</span>
                    <span className="closed-date">{t.completed_at?.slice(5, 10)}</span>
                  </label>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      </div>
    </div>
  )
}
