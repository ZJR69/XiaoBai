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
  { key: 'research', label: '科研' },
  { key: 'collaboration', label: '合作' },
  { key: 'family', label: '家庭' },
  { key: 'personal', label: '个人' },
  { key: 'club', label: '社团' },
  { key: 'other', label: '其他' },
]

const categoryLabel = (key) => CATEGORIES.find((c) => c.key === key)?.label || '其他'

export default function ProjectsView({ refreshKey, refresh }) {
  const [projects, setProjects] = useState([])
  const [name, setName] = useState('')
  const [category, setCategory] = useState('personal')
  const [pack, setPack] = useState(null) // context pack 弹层
  const [logFor, setLogFor] = useState(null) // 正在加日志的项目
  const [logKind, setLogKind] = useState('next_step')
  const [logText, setLogText] = useState('')

  const load = async () => setProjects(await api.listProjects())
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey])

  const add = async (e) => {
    e.preventDefault()
    const n = name.trim()
    if (!n) return
    await api.createProject({ name: n, category })
    setName('')
    await load()
    refresh?.()
  }

  const submitLog = async (e) => {
    e.preventDefault()
    const t = logText.trim()
    if (!t || !logFor) return
    await api.addLog(logFor.id, logKind, t)
    setLogFor(null)
    setLogText('')
    await load()
  }

  const makePack = async (p) => {
    const res = await api.contextPack(p.id)
    setPack(res)
  }

  return (
    <div className="view">
      <h2>进行中的事</h2>
      <p className="hint">不只是正经项目——申报、合作洽谈、家庭事务都算。对话里聊到的进展会自动挂靠到这里。</p>
      <form className="inline-form" onSubmit={add}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="新事项（如：与A机构的合作洽谈）…" />
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          {CATEGORIES.map((c) => (
            <option key={c.key} value={c.key}>{c.label}</option>
          ))}
        </select>
        <button type="submit">创建</button>
      </form>

      {projects.length === 0 && <p className="empty">还没有进行中的事。和我说「最近在和某某谈什么」，回顾后就会出现在这里。</p>}

      <div className="project-grid">
        {projects.map((p) => (
          <div key={p.id} className="project-card">
            <div className="project-head">
              <span className="project-name">{p.name}</span>
              <span className="project-cat">{categoryLabel(p.category)}</span>
              <span className="project-status">{p.status}</span>
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
              <button className="small-btn" onClick={() => setLogFor(p)}>记进展</button>
              <button className="small-btn" onClick={() => makePack(p)}>背景包</button>
            </div>
          </div>
        ))}
      </div>

      {logFor && (
        <div className="modal-mask" onClick={() => setLogFor(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>记录进展 · {logFor.name}</h3>
            <div className="kind-row">
              {LOG_KINDS.map((k) => (
                <button
                  key={k.key}
                  className={logKind === k.key ? 'chip selected' : 'chip'}
                  onClick={() => setLogKind(k.key)}
                >
                  {k.label}
                </button>
              ))}
            </div>
            <form onSubmit={submitLog}>
              <textarea
                value={logText}
                onChange={(e) => setLogText(e.target.value)}
                placeholder="这件事的背景/决策/当前状态/下一步…"
                rows={4}
                autoFocus
              />
              <div className="modal-actions">
                <button type="button" className="small-btn" onClick={() => setLogFor(null)}>取消</button>
                <button type="submit" className="small-btn primary">保存</button>
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
