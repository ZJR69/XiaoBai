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

const kindLabel = (k) => LOG_KINDS.find((x) => x.key === k)?.label || k

// 距今多久（只呈现不判断——小时周期还是月周期，用户一眼自知；预警判断交给小白）
const daysAgo = (iso) => {
  if (!iso) return ''
  const d = new Date(iso.slice(0, 10))
  const today = new Date()
  const n = Math.round((today - d) / 86400000)
  if (n <= 0) return '今天'
  if (n === 1) return '昨天'
  return `${n}天前`
}

// 行内最新动态：[类型chip] 摘要 · 距今（没有则空）
const LastActivity = ({ a }) => {
  if (!a) return <span className="proj-last muted">还没动态</span>
  return (
    <span className="proj-last">
      <span className={`kind-chip k-${a.kind}`}>{kindLabel(a.kind)}</span>
      <span className="proj-last-text">{a.content}</span>
      <span className="proj-last-when">{daysAgo(a.created_at)}</span>
    </span>
  )
}

export default function ProjectsView({ refreshKey, refresh }) {
  const [projects, setProjects] = useState([]) // 树形：一级带 children
  const [expanded, setExpanded] = useState(() => {
    try { return JSON.parse(localStorage.getItem('proj_expanded') || '{}') } catch { return {} }
  })
  const [name, setName] = useState('')
  const [category, setCategory] = useState('personal')
  const [inlineFor, setInlineFor] = useState(null) // 就地新建子事项的父级 id
  const [inlineName, setInlineName] = useState('')
  const [editFor, setEditFor] = useState(null) // 编辑中的事项（属性面板）
  const [pack, setPack] = useState(null)
  const [logFor, setLogFor] = useState(null) // 日志管理弹层
  const [logs, setLogs] = useState([])
  const [logKind, setLogKind] = useState('status')
  const [logText, setLogText] = useState('')
  const [editingLog, setEditingLog] = useState(null)
  const [editLogText, setEditLogText] = useState('')
  const [showClosed, setShowClosed] = useState(false)

  const load = async () => setProjects(await api.listProjects(true))
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey])

  const toggle = (id) => {
    setExpanded((e) => {
      const next = { ...e, [id]: !e[id] }
      localStorage.setItem('proj_expanded', JSON.stringify(next))
      return next
    })
  }

  const active = projects.filter((p) => p.status !== 'closed')
  // 已完成 = 一级 closed + 各一级下 closed 的二级（后端树形只把一级放顶层，二级须从 children 收集）
  const closed = [
    ...projects.filter((p) => p.status === 'closed'),
    ...projects.flatMap((p) => (p.children || []).filter((c) => c.status === 'closed')),
  ]
  const childTotal = active.reduce((n, p) => n + (p.children || []).filter((c) => c.status !== 'closed').length, 0)

  const add = async (e) => {
    e.preventDefault()
    const n = name.trim()
    if (!n) return
    await api.createProject({ name: n, category })
    setName('')
    await load()
    refresh?.()
  }

  const addChild = async (parentId) => {
    const n = inlineName.trim()
    if (!n) { setInlineFor(null); return }
    await api.createProject({ name: n, category: 'course', parent_id: inlineFor })
    setInlineName('')
    setInlineFor(null)
    setExpanded((e) => { const nx = { ...e, [parentId]: true }; localStorage.setItem('proj_expanded', JSON.stringify(nx)); return nx })
    await load()
    refresh?.()
  }

  const closeProject = async (p) => {
    if (!window.confirm(`把「${p.name}」标记为完成？它会从列表收起（数据保留，可恢复）。`)) return
    await api.updateProject(p.id, { status: 'closed' })
    // 级联：一级完成 → 子事项一并收起（整块事务结束）
    for (const c of p.children || []) {
      if (c.status !== 'closed') await api.updateProject(c.id, { status: 'closed' })
    }
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
    await load()
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

  const makePack = async (p) => setPack(await api.contextPack(p.id))

  return (
    <div className="view">
      <h2>进行中的事</h2>
      <div className="proj-overview">
        <span>{active.length} 块事务 · {childTotal} 个子事项{closed.length > 0 ? ` · 已完成 ${closed.length}` : ''}</span>
        <span className="proj-overview-hint">和小白聊进展会自动记到对应事项下</span>
      </div>
      <div className="wb-panel">
      <form className="inline-form" onSubmit={add}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="新事务（如：与A机构的合作洽谈）…" />
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          {CATEGORIES.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
        <button type="submit">创建</button>
      </form>

      {active.length === 0 && <p className="empty">还没有进行中的事。和小白聊「最近在做什么」，它记下的进展会出现在这里。</p>}

      {CATEGORIES.map((cat) => {
        const tops = active.filter((p) => p.category === cat.key)
        if (tops.length === 0) return null
        return (
          <div key={cat.key} className="proj-block">
            <div className="proj-block-title">{cat.label}</div>
            {tops.map((p) => {
              const open = expanded[p.id] ?? true
              const kids = p.children || []
              return (
                <div key={p.id} className="proj-wrap">
                  <div className="proj-row top">
                    {kids.length > 0 && (
                      <button className="proj-toggle" onClick={() => toggle(p.id)} title={open ? '收起' : '展开'}>
                        {open ? '▾' : '▸'}
                      </button>
                    )}
                    <span className="proj-name">{p.name}</span>
                    {kids.length > 0 && <span className="proj-chip" title="子事项数">{kids.length} 项</span>}
                    {p.open_tasks > 0 && <span className="proj-chip tasks" title="未完成任务数">⚑{p.open_tasks}</span>}
                    <LastActivity a={p.last_activity} />
                    <span className="proj-ops">
                      <button className="task-op" onClick={() => setInlineFor(inlineFor === p.id ? null : p.id)} title="加子事项（如某门课）">＋</button>
                      <button className="task-op" onClick={() => openLogs(p)} title="进展日志">进展</button>
                      <button className="task-op" onClick={() => makePack(p)} title="背景同步包（素材）">背景包</button>
                      <button className="task-op" onClick={() => setEditFor(p)} title="编辑属性"><svg viewBox="0 0 24 24"><path d="M15.5 4.5l4 4L8 20H4v-4L15.5 4.5Z"/><path d="M13.5 6.5l4 4"/></svg></button>
                      <button className="task-op" onClick={() => closeProject(p)} title="标记完成">✓</button>
                    </span>
                  </div>
                  {open && kids.filter((c) => c.status !== 'closed').map((c) => (
                    <div key={c.id} className="proj-row child">
                      <span className="proj-name">{c.name}</span>
                      {c.tasks.filter((t) => t.status !== 'done').length > 0 && (
                        <span className="proj-chip tasks">⚑{c.tasks.filter((t) => t.status !== 'done').length}</span>
                      )}
                      <LastActivity a={c.last_activity} />
                      <span className="proj-ops">
                        <button className="task-op" onClick={() => openLogs(c)} title="进展日志">进展</button>
                        <button className="task-op" onClick={() => makePack(c)} title="背景同步包（素材）">背景包</button>
                        <button className="task-op" onClick={() => setEditFor(c)} title="编辑属性"><svg viewBox="0 0 24 24"><path d="M15.5 4.5l4 4L8 20H4v-4L15.5 4.5Z"/><path d="M13.5 6.5l4 4"/></svg></button>
                        <button className="task-op" onClick={() => closeProject(c)} title="标记完成">✓</button>
                      </span>
                    </div>
                  ))}
                  {open && inlineFor === p.id && (
                    <div className="proj-row child newing">
                      <input
                        autoFocus value={inlineName}
                        onChange={(e) => setInlineName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') { e.preventDefault(); addChild(p.id) }
                          else if (e.key === 'Escape') { setInlineFor(null); setInlineName('') }
                        }}
                        onBlur={() => addChild(p.id)}
                        placeholder={`在「${p.name}」下加子事项，回车创建…`}
                      />
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )
      })}

      {closed.length > 0 && (
        <div className="closed-fold">
          <button className="closed-toggle" onClick={() => setShowClosed((s) => !s)}>
            {showClosed ? '▾' : '▸'} 已完成的事（{closed.length}）
          </button>
          {showClosed && (
            <div className="closed-list">
              {closed.map((p) => (
                <div key={p.id} className="closed-item">
                  <span className="closed-name">{p.parent_id != null ? '　' : ''}{p.name}</span>
                  <span className="closed-date">{(p.closed_at || '').slice(0, 10)}</span>
                  <button className="small-btn" onClick={() => reopenProject(p)}>恢复</button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {editFor && <EditPanel p={editFor} tops={active} onDone={async () => { setEditFor(null); await load(); refresh?.() }} />}

      {logFor && (
        <div className="modal-mask" onClick={() => setLogFor(null)}>
          <div className="modal wide" onClick={(e) => e.stopPropagation()}>
            <h3>进展 · {logFor.name}</h3>
            <div className="log-list">
              {logs.length === 0 && <p className="empty">还没有日志。和小白聊这件事，说出的进展会自动记进来。</p>}
              {logs.map((lg) => (
                <div key={lg.id} className={`log-item${lg.done_at ? ' done' : ''}`}>
                  <span className={`kind-chip k-${lg.kind}`}>{kindLabel(lg.kind)}</span>
                  {lg.source === 'agent' && <span className="log-agent" title="小白从对话中自动记录">小白记</span>}
                  {lg.done_at && <span className="log-done-tag" title={`随任务完成于 ${(lg.done_at || '').slice(0, 10)} 落地，留痕保留`}>已落地</span>}
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
                        <button className="icon-btn" onClick={() => { setEditingLog(lg.id); setEditLogText(lg.content) }} title="编辑"><svg viewBox="0 0 24 24" style={{width:14,height:14,stroke:'currentColor',strokeWidth:1.5,fill:'none',strokeLinecap:'round',strokeLinejoin:'round'}}><path d="M15.5 4.5l4 4L8 20H4v-4L15.5 4.5Z"/><path d="M13.5 6.5l4 4"/></svg></button>
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
    </div>
  )
}

// 属性编辑面板（属性事后改：名称/描述/类别；二级还可改挂靠或升为一级）
function EditPanel({ p, tops, onDone }) {
  const [name, setName] = useState(p.name)
  const [description, setDescription] = useState(p.description || '')
  const [category, setCategory] = useState(p.category)
  const [parentId, setParentId] = useState(p.parent_id ?? '')

  const save = async () => {
    const patch = { name: name.trim() || p.name, description, category }
    if (p.parent_id != null || parentId !== '') {
      patch.parent_id = parentId === '' ? null : Number(parentId)
    }
    await api.updateProject(p.id, patch)
    onDone()
  }

  return (
    <div className="modal-mask" onClick={onDone}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>编辑 · {p.name}</h3>
        <div className="proj-edit-form">
          <label>名称<input value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label>描述<textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} /></label>
          <label>类别
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              {CATEGORIES.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
            </select>
          </label>
          {p.parent_id != null && (
            <label>挂靠
              <select value={parentId} onChange={(e) => setParentId(e.target.value)}>
                <option value="">（升为一级事务）</option>
                {tops.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </label>
          )}
        </div>
        <div className="modal-actions">
          <button className="small-btn" onClick={onDone}>取消</button>
          <button className="small-btn primary" onClick={save}>保存</button>
        </div>
      </div>
    </div>
  )
}
