import { useEffect, useState } from 'react'
import { api } from '../api.js'

const LOG_KINDS = [
  { key: 'background', label: '背景' },
  { key: 'decision', label: '决策' },
  { key: 'status', label: '状态' },
  { key: 'next_step', label: '下一步' },
  { key: 'note', label: '备注' },
]

const CATEGORIES = [
  { key: 'course', label: '课程' },
  { key: 'research', label: '科研' },
  { key: 'collaboration', label: '合作' },
  { key: 'family', label: '家庭' },
  { key: 'personal', label: '个人' },
  { key: 'club', label: '社团' },
  { key: 'other', label: '其他' },
]

export default function ProjectsView({ refreshKey, refresh }) {
  const [projects, setProjects] = useState([])
  const [name, setName] = useState('')
  const [category, setCategory] = useState('personal')
  const [pack, setPack] = useState(null) // context pack 弹层
  const [logFor, setLogFor] = useState(null) // 正在管理日志的项目
  const [logs, setLogs] = useState([]) // 该项目的全部进展日志
  const [logKind, setLogKind] = useState('next_step')
  const [logText, setLogText] = useState('')
  const [editingLog, setEditingLog] = useState(null) // 编辑中的日志 id
  const [editLogText, setEditLogText] = useState('')
  const [showClosed, setShowClosed] = useState(false) // 已完成折叠区

  const load = async () => setProjects(await api.listProjects(true))
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey])

  const active = projects.filter((p) => p.status !== 'closed')
  const closed = projects.filter((p) => p.status === 'closed')

  const add = async (e) => {
    e.preventDefault()
    const n = name.trim()
    if (!n) return
    await api.createProject({ name: n, category })
    setName('')
    await load()
    refresh?.()
  }

  const closeProject = async (p) => {
    if (!window.confirm(`把「${p.name}」标记为完成？它会从列表收起（数据保留，可恢复）。`)) return
    await api.updateProject(p.id, { status: 'closed' })
    await load()
    refresh?.()
  }

  const reopenProject = async (p) => {
    await api.updateProject(p.id, { status: 'active' })
    await load()
    refresh?.()
  }

  const openLogs = async (p) => {
    setLogFor(p)
    setEditingLog(null)
    setLogText('')
    setLogs(await api.listLogs(p.id))
  }

  const reloadLogs = async () => {
    if (logFor) setLogs(await api.listLogs(logFor.id))
    await load() // 卡片上的「下一步/最近动态」同步刷新
  }

  const submitLog = async (e) => {
    e.preventDefault()
    const t = logText.trim()
    if (!t || !logFor) return
    await api.addLog(logFor.id, logKind, t)
    setLogText('')
    await reloadLogs()
  }

  const saveLogEdit = async (logId) => {
    const t = editLogText.trim()
    if (!t) return
    await api.updateLog(logFor.id, logId, { content: t })
    setEditingLog(null)
    await reloadLogs()
  }

  const removeLog = async (logId) => {
    if (!window.confirm('删除这条日志？（背景包里也会同步消失）')) return
    await api.deleteLog(logFor.id, logId)
    if (editingLog === logId) setEditingLog(null)
    await reloadLogs()
  }

  const makePack = async (p) => {
    const res = await api.contextPack(p.id)
    setPack(res)
  }

  return (
    <div className="view">
      <h2>进行中的事</h2>
      <p className="hint">申报、洽谈、课程、家庭事务都算。对话里建任务时可以挂到这里（输入「#」引用具体某件事）。</p>
      <form className="inline-form" onSubmit={add}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="新事项（如：与A机构的合作洽谈）…" />
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          {CATEGORIES.map((c) => (
            <option key={c.key} value={c.key}>{c.label}</option>
          ))}
        </select>
        <button type="submit">创建</button>
      </form>

      {active.length === 0 && <p className="empty">还没有进行中的事。和我说「最近在和某某谈什么」，回顾后就会出现在这里。</p>}

      <div className="project-grid">
        {active.map((p) => (
          <div key={p.id} className="project-card">
            <div className="project-head">
              <span className="project-name">{p.name}</span>
            </div>
            {p.description && <p className="project-desc">{p.description}</p>}
            {p.next_step && (
              <div className="project-next">→ {p.next_step.content}</div>
            )}
            {p.last_activity && (
              <div className="project-meta">
                最近动态：{p.last_activity.kind} · {p.last_activity.created_at.slice(5, 10)}
              </div>
            )}
            <div className="project-tasks">
              {p.tasks.filter((t) => t.status !== 'done').length > 0 ? (
                p.tasks.filter((t) => t.status !== 'done').map((t) => (
                  <div key={t.id} className="project-task">· {t.title}{t.due_at ? `（${t.due_at.slice(5, 10)}）` : ''}</div>
                ))
              ) : (
                <div className="project-meta">无待办任务</div>
              )}
            </div>
            <div className="project-actions">
              <button className="small-btn" onClick={() => openLogs(p)}>进展</button>
              <button className="small-btn" onClick={() => makePack(p)}>背景包</button>
              <button className="small-btn" onClick={() => closeProject(p)}>完成</button>
            </div>
          </div>
        ))}
      </div>

      {closed.length > 0 && (
        <div className="closed-fold">
          <button className="closed-toggle" onClick={() => setShowClosed((s) => !s)}>
            {showClosed ? '▾' : '▸'} 已完成的事（{closed.length}）
          </button>
          {showClosed && (
            <div className="closed-list">
              {closed.map((p) => (
                <div key={p.id} className="closed-item">
                  <span className="closed-name">{p.name}</span>
                  <span className="closed-date">{(p.closed_at || '').slice(0, 10)}</span>
                  <button className="small-btn" onClick={() => reopenProject(p)}>恢复</button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {logFor && (
        <div className="modal-mask" onClick={() => setLogFor(null)}>
          <div className="modal wide" onClick={(e) => e.stopPropagation()}>
            <h3>进展 · {logFor.name}</h3>
            <div className="log-list">
              {logs.length === 0 && <p className="empty">还没有日志。</p>}
              {logs.map((lg) => (
                <div key={lg.id} className="log-item">
                  <span className="kind-chip">{LOG_KINDS.find((k) => k.key === lg.kind)?.label || lg.kind}</span>
                  {editingLog === lg.id ? (
                    <>
                      <textarea
                        className="log-edit"
                        value={editLogText}
                        onChange={(e) => setEditLogText(e.target.value)}
                        rows={2}
                        autoFocus
                      />
                      <span className="log-ops">
                        <button className="small-btn primary" onClick={() => saveLogEdit(lg.id)}>存</button>
                        <button className="small-btn" onClick={() => setEditingLog(null)}>取消</button>
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="log-content">{lg.content}</span>
                      <span className="log-date">{(lg.created_at || '').slice(0, 10)}</span>
                      <span className="log-ops">
                        <button className="icon-btn" onClick={() => { setEditingLog(lg.id); setEditLogText(lg.content) }} title="编辑">✎</button>
                        <button className="icon-btn" onClick={() => removeLog(lg.id)} title="删除">×</button>
                      </span>
                    </>
                  )}
                </div>
              ))}
            </div>
            <form onSubmit={submitLog}>
              <div className="kind-row">
                {LOG_KINDS.map((k) => (
                  <button
                    key={k.key}
                    type="button"
                    className={logKind === k.key ? 'chip selected' : 'chip'}
                    onClick={() => setLogKind(k.key)}
                  >
                    {k.label}
                  </button>
                ))}
              </div>
              <textarea
                value={logText}
                onChange={(e) => setLogText(e.target.value)}
                placeholder="这件事的背景/决策/当前状态/下一步…"
                rows={3}
              />
              <div className="modal-actions">
                <button type="button" className="small-btn" onClick={() => setLogFor(null)}>关闭</button>
                <button type="submit" className="small-btn primary">添加</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {pack && (
        <div className="modal-mask" onClick={() => setPack(null)}>
          <div className="modal wide" onClick={(e) => e.stopPropagation()}>
            <h3>{pack.project} · 背景同步包</h3>
            <pre className="pack">{pack.pack}</pre>
            <div className="modal-actions">
              <button className="small-btn" onClick={() => navigator.clipboard?.writeText(pack.pack)}>复制</button>
              <button className="small-btn primary" onClick={() => setPack(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
