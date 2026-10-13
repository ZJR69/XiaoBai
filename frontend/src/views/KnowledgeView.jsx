import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api.js'

const KIND_LABEL = { url: '网址', file: '文件', folder: '文件夹' }
// 线性小图标（杂志风：1.5px 描边，不用 emoji）
const svgProps = { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round' }
const KIND_ICON = {
  url: <svg {...svgProps}><path d="M10.5 13.5a4 4 0 0 0 5.7 0l2.6-2.6a4 4 0 1 0-5.7-5.7l-1.4 1.4"/><path d="M13.5 10.5a4 4 0 0 0-5.7 0l-2.6 2.6a4 4 0 1 0 5.7 5.7l1.4-1.4"/></svg>,
  file: <svg {...svgProps}><path d="M6 3.5h8L19 8.5v12H6v-17Z"/><path d="M13.5 3.5v5.5H19"/></svg>,
  folder: <svg {...svgProps}><path d="M3.5 6.5h6l2 2.5h9v9.5h-17V6.5Z"/></svg>,
  group: <svg {...svgProps}><path d="M4 5.5h16M4 12h16M4 18.5h10"/></svg>,
}
const EXPAND_KEY = 'res_expanded'

// ── ＋ 新建菜单（位置先行：挂在本行上，点哪建哪；根级第一项叫「话题」，话题行里叫「子话题」）──
function NewMenu({ onPick, root }) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef(null)

  // 点菜单外任意处收回
  useEffect(() => {
    if (!open) return
    const onDoc = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const kinds = [
    { kind: 'group', label: root ? '话题' : '子话题' },
    { kind: 'url', label: '网址' },
    { kind: 'file', label: '文件' },
    { kind: 'folder', label: '文件夹' },
  ]
  return (
    <div className="new-menu-wrap" ref={wrapRef}>
      <button className={root ? 'small-btn primary' : 'small-btn new-btn'} onClick={() => setOpen((o) => !o)} title="在这里新建">
        {root ? '＋ 新建' : '＋'}
      </button>
      {open && (
        <div className="new-menu">
          {kinds.map((k) => (
            <button key={k.kind} onClick={() => { setOpen(false); onPick(k.kind) }}>{k.label}</button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── 就地创建行：只问创建必需的东西（名字/地址），挂靠项目、备注留给「编辑」──
// 名称留空给默认名（url 取域名，file/folder 取路径末段）——飞书式「无标题文档」
function InlineNew({ kind, indent, onCancel, onCreate }) {
  const [title, setTitle] = useState('')
  const [target, setTarget] = useState('')
  const [error, setError] = useState('')
  const [picking, setPicking] = useState(false)
  const nameRef = useRef(null)
  useEffect(() => { nameRef.current?.focus() }, [])

  const browse = async () => {
    setPicking(true)
    setError('')
    try {
      const res = await api.pickLinkPath(kind)
      if (res.path) setTarget(res.path)
    } catch (err) {
      setError(`选择失败：${err.message}`)
    } finally {
      setPicking(false)
    }
  }

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    const addr = target.trim()
    let name = title.trim()
    if (kind !== 'group' && !addr) { setError('地址必填'); return }
    if (!name) {
      if (kind === 'url') { try { name = new URL(addr).hostname } catch { name = addr } }
      else if (kind === 'file' || kind === 'folder') { name = addr.split(/[\\/]/).filter(Boolean).pop() || '' }
    }
    if (!name) { setError('名称必填'); return }
    const err = await onCreate({ title: name, kind, target: kind === 'group' ? '' : addr })
    if (err) setError(err)
  }

  return (
    <form className="inline-new" style={{ paddingLeft: indent }} onSubmit={submit} onKeyDown={(e) => e.key === 'Escape' && onCancel()}>
      <span className="tree-icon">{kind === 'group' ? KIND_ICON.group : KIND_ICON[kind]}</span>
      <input ref={nameRef} className="inline-new-name" value={title} onChange={(e) => setTitle(e.target.value)}
        placeholder={kind === 'group' ? '新话题名…（回车即建）' : '名称（可留空自动取名）'} />
      {kind !== 'group' && (
        <>
          <input className="inline-new-target" value={target} onChange={(e) => setTarget(e.target.value)}
            placeholder={kind === 'url' ? 'https://…（回车登记）' : '路径（点「浏览…」选，或粘贴）'} />
          {kind !== 'url' && (
            <button type="button" className="small-btn" onClick={browse} disabled={picking}>
              {picking ? '选择中…' : '浏览…'}
            </button>
          )}
        </>
      )}
      <button type="submit" className="small-btn primary">创建</button>
      <button type="button" className="small-btn" onClick={onCancel}>取消</button>
      {error && <span className="action-error inline-err">{error}</span>}
    </form>
  )
}

// ── 行内编辑面板（属性是事后的：移动话题 / 挂靠项目 / 备注都在这里改）──
function EditPanel({ l, projects, groupList, onSave, onCancel }) {
  const isGroup = l.kind === 'group'
  const [title, setTitle] = useState(l.title)
  const [target, setTarget] = useState(l.target || '')
  const [parentId, setParentId] = useState(l.parent_id || '')
  const [projectId, setProjectId] = useState(l.project_id || '')
  const [note, setNote] = useState(l.note || '')
  const [error, setError] = useState('')
  const [picking, setPicking] = useState(false)

  const browse = async () => {
    setPicking(true)
    setError('')
    try {
      const res = await api.pickLinkPath(l.kind)
      if (res.path) setTarget(res.path)
    } catch (err) {
      setError(`选择失败：${err.message}`)
    } finally {
      setPicking(false)
    }
  }

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    const payload = {
      title: title.trim(),
      parent_id: parentId ? Number(parentId) : null,
      project_id: projectId || null,
      note: note.trim() || null,
    }
    if (!isGroup) payload.target = target.trim()
    if (!payload.title) { setError('名称必填'); return }
    if (!isGroup && !payload.target) { setError('地址必填'); return }
    const err = await onSave(payload)
    if (err) setError(err)
  }

  return (
    <form className="edit-panel" onSubmit={submit} onKeyDown={(e) => e.key === 'Escape' && onCancel()}>
      <div className="res-form-row">
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="名称" />
        {!isGroup && (
          <>
            <input value={target} onChange={(e) => setTarget(e.target.value)}
              placeholder={l.kind === 'url' ? 'https://…' : '路径'} />
            {l.kind !== 'url' && (
              <button type="button" className="small-btn" onClick={browse} disabled={picking}>
                {picking ? '选择中…' : '浏览…'}
              </button>
            )}
          </>
        )}
      </div>
      <div className="res-form-row">
        <select value={parentId} onChange={(e) => setParentId(e.target.value)} title="移动到哪个话题下">
          <option value="">根级</option>
          {groupList.filter((g) => g.id !== l.id).map((g) => (
            <option key={g.id} value={g.id}>话题：{g.label}</option>
          ))}
        </select>
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)} title="挂靠到哪个进行中的事">
          <option value="">不挂靠项目</option>
          {projects.map((p) => <option key={p.id} value={p.id}>挂靠：{p.name}</option>)}
        </select>
      </div>
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="备注（可选）" />
      {error && <div className="action-error">{error}</div>}
      <div className="proposal-actions">
        <button className="small-btn primary" type="submit">保存</button>
        <button className="small-btn" type="button" onClick={onCancel}>取消</button>
      </div>
    </form>
  )
}

// ── 树的一行：话题行（toggle + hover ＋菜单 + 编辑/删除），叶子行（点击直达 + 编辑/删除）──
// 拖拽（对齐资源管理器/飞书语义）：话题行三段判定——上1/3插前面、中间进入话题、下1/3插后面；
// 叶子两段（前/后）；落点指示由父组件统一管理
function TreeRow({ l, depth, childCount, isOpen, flash, editing, dragId, dropHint,
  onToggle, onNew, onEdit, onDelete, onOpen, onDragStart, onDragEnd, onRowDragOver, onRowDrop }) {
  const isGroup = l.kind === 'group'
  const cls = ['tree-row']
  if (flash) cls.push('flash')
  if (dropHint) cls.push(`drop-${dropHint}`)
  return (
    <div className={cls.join(' ')} style={{ paddingLeft: depth * 18 }}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = 'move'
        e.dataTransfer.setData('text/plain', String(l.id))
        onDragStart(l.id)
      }}
      onDragEnd={onDragEnd}
      onDragOver={(e) => {
        if (dragId == null || dragId === l.id) {
          // 自己拖自己/外部拖入：拦掉冒泡（不落到根级落区），也不允许落在自己上
          e.stopPropagation()
          return
        }
        e.preventDefault()
        e.stopPropagation()
        e.dataTransfer.dropEffect = 'move'
        const r = e.currentTarget.getBoundingClientRect()
        const y = (e.clientY - r.top) / r.height
        const mode = isGroup
          ? (y < 0.3 ? 'before' : y > 0.7 ? 'after' : 'into')
          : (y < 0.5 ? 'before' : 'after')
        onRowDragOver(l.id, mode)
      }}
      onDrop={(e) => { e.preventDefault(); e.stopPropagation(); onRowDrop(l.id) }}
    >
      {isGroup ? (
        <>
          <button className="tree-toggle" onClick={onToggle} title={isOpen ? '收起' : '展开'}>{isOpen ? '▾' : '▸'}</button>
          <span className="tree-icon">{KIND_ICON.group}</span>
          <button className="tree-title group" onClick={onToggle}>{l.title}</button>
          {childCount > 0 && <span className="hint">（{childCount}）</span>}
        </>
      ) : (
        <>
          <span className="tree-icon" title={KIND_LABEL[l.kind] || l.kind}>{KIND_ICON[l.kind] || KIND_ICON.url}</span>
          <button className="tree-title" onClick={onOpen}
            title={`${l.target}\n点击${l.kind === 'url' ? '打开网址' : '在资源管理器中定位'}`}>
            {l.title}
          </button>
          {l.project_name && <span className="kind-chip project">{l.project_name}</span>}
          {l.note && <span className="res-note" title={l.note}>{l.note}</span>}
        </>
      )}
      <span className="res-ops">
        {isGroup && <NewMenu onPick={onNew} />}
        <button className="small-btn" onClick={onEdit}>{editing ? '收起' : '编辑'}</button>
        <button className="small-btn" onClick={onDelete}>删除</button>
      </span>
    </div>
  )
}

// 资源索引（2026-09-26 树化；交互对齐成熟产品新建逻辑——位置先行、就地创建、属性事后）：
// 话题行 hover 出「＋」，点哪建哪；创建行只问名字/地址；移动/挂靠/备注收进「编辑」面板。
// 登记主路径仍是对话操作卡（进根级，页面「编辑」移动到话题下）。
export default function KnowledgeView({ refreshKey }) {
  const [links, setLinks] = useState([])
  const [projects, setProjects] = useState([])
  const [creating, setCreating] = useState(null) // { parentId, kind }：就地创建的位置与类型
  const [editingId, setEditingId] = useState(null)
  const [lastAdded, setLastAdded] = useState(null) // 刚建完的行高亮一下
  const [dragId, setDragId] = useState(null) // 正在拖的条目 id
  const [drop, setDrop] = useState(null) // 落点：{ id, mode }（行上）或 { root: true }（树空白=根级末尾）
  const [expanded, setExpanded] = useState(() => {
    try { return new Set(JSON.parse(localStorage.getItem(EXPAND_KEY) || '[]')) } catch { return new Set() }
  })

  const load = () =>
    api.listLinks().then((r) => {
      const ls = r.links || []
      setLinks(ls)
      // 首次进入（无记录）默认全展开，之后跟随用户操作
      if (localStorage.getItem(EXPAND_KEY) === null) {
        setExpanded(new Set(ls.filter((l) => l.kind === 'group').map((l) => l.id)))
      }
    }).catch(() => {})
  useEffect(() => { load() }, [refreshKey])
  useEffect(() => { api.listProjects().then(setProjects).catch(() => {}) }, [])

  // 建树（同层顺序由后端 ORDER BY position 保证，拖拽排序的结果）
  const childMap = useMemo(() => {
    const m = new Map()
    for (const l of links) {
      const k = l.parent_id || 0
      if (!m.has(k)) m.set(k, [])
      m.get(k).push(l)
    }
    return m
  }, [links])

  // 编辑面板「移动」用的扁平话题列表（全角空格缩进显示层级；防环由后端兜底报错）
  const groupList = useMemo(() => {
    const out = []
    const walk = (pid, depth) => {
      for (const l of childMap.get(pid) || []) {
        if (l.kind !== 'group') continue
        out.push({ id: l.id, label: '　'.repeat(depth) + l.title })
        walk(l.id, depth + 1)
      }
    }
    walk(0, 0)
    return out
  }, [childMap])

  const persistExpanded = (s) => localStorage.setItem(EXPAND_KEY, JSON.stringify([...s]))
  const toggle = (id) => setExpanded((s) => {
    const n = new Set(s)
    if (n.has(id)) n.delete(id); else n.add(id)
    persistExpanded(n)
    return n
  })

  const open = async (l) => {
    if (l.kind === 'url') { window.open(l.target, '_blank', 'noopener'); return }
    try { await api.openLink(l.id) } catch (err) { alert(`打开失败：${err.message}`) }
  }

  // ── 创建（位置先行：startCreate 记住在哪建；InlineNew 只问名字/地址）──
  const startCreate = (parentId, kind) => {
    setEditingId(null)
    setCreating({ parentId, kind })
    if (parentId != null) {
      // 在收起的话题上点＋：先展开让人看见创建行落在哪
      setExpanded((s) => { const n = new Set(s); n.add(parentId); persistExpanded(n); return n })
    }
  }
  const doCreate = async (payload) => {
    try {
      const res = await api.createLink({ ...payload, parent_id: creating.parentId })
      setLastAdded(res.id)
      setCreating(null)
      await load()
      return null
    } catch (err) {
      return err.message
    }
  }

  const saveEdit = async (payload) => {
    try {
      await api.updateLink(editingId, payload)
      setEditingId(null)
      await load()
      return null
    } catch (err) {
      return err.message
    }
  }

  const remove = async (l) => {
    const what = l.kind === 'group' ? `话题「${l.title}」` : `资源「${l.title}」`
    if (!window.confirm(`删掉${what}？`)) return
    // 非空话题会被后端拒绝（先移空再删），错误文案直接弹出
    await api.deleteLink(l.id).catch((err) => alert(`删除失败：${err.message}`))
    load()
  }

  // ── 拖拽（换父 + 同级排序，/move 一次完成；防环由后端 _check_parent 兜底）──
  const handleDragStart = (id) => {
    setCreating(null)
    setEditingId(null)
    setDragId(id)
  }
  const handleDragEnd = () => { setDragId(null); setDrop(null) }
  // 条件性 setState（dragover 每帧触发，值没变就不重渲）
  const handleRowDragOver = (id, mode) =>
    setDrop((d) => (d && !d.root && d.id === id && d.mode === mode ? d : { id, mode }))

  const handleRowDrop = async (rowId) => {
    const row = links.find((l) => l.id === rowId)
    const mode = drop && !drop.root && drop.id === rowId ? drop.mode : 'into'
    const id = dragId
    setDrop(null)
    setDragId(null)
    if (!row || id == null || id === rowId) return
    let parentId, pos
    if (mode === 'into') {
      parentId = row.id
      pos = (childMap.get(row.id) || []).filter((x) => x.id !== id).length
    } else {
      parentId = row.parent_id || null
      const sibs = (childMap.get(parentId || 0) || []).filter((x) => x.id !== id)
      const idx = sibs.findIndex((x) => x.id === rowId)
      pos = Math.max(0, idx + (mode === 'after' ? 1 : 0))
    }
    try {
      await api.moveLink(id, { parent_id: parentId, position: pos })
      // 移入的话题若是收起状态，展开让人看见结果
      if (mode === 'into') {
        setExpanded((s) => { const n = new Set(s); n.add(row.id); persistExpanded(n); return n })
      }
      await load()
    } catch (err) {
      alert(`移动失败：${err.message}`)
    }
  }

  const handleRootDragOver = (e) => {
    if (dragId == null) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    setDrop((d) => (d && d.root ? d : { root: true }))
  }
  const handleRootDrop = async (e) => {
    e.preventDefault()
    const id = dragId
    setDrop(null)
    setDragId(null)
    if (id == null) return
    const pos = (childMap.get(0) || []).filter((x) => x.id !== id).length
    try {
      await api.moveLink(id, { parent_id: null, position: pos })
      await load()
    } catch (err) {
      alert(`移动失败：${err.message}`)
    }
  }

  const renderLevel = (pid, depth) => {
    const rows = []
    for (const l of childMap.get(pid) || []) {
      const isOpen = expanded.has(l.id)
      rows.push(
        <TreeRow key={l.id} l={l} depth={depth} childCount={(childMap.get(l.id) || []).length}
          isOpen={isOpen} flash={lastAdded === l.id} editing={editingId === l.id}
          dragId={dragId} dropHint={drop && !drop.root && drop.id === l.id ? drop.mode : null}
          onToggle={() => toggle(l.id)} onNew={(k) => startCreate(l.id, k)}
          onEdit={() => { setCreating(null); setEditingId((cur) => (cur === l.id ? null : l.id)) }}
          onDelete={() => remove(l)} onOpen={() => open(l)}
          onDragStart={handleDragStart} onDragEnd={handleDragEnd}
          onRowDragOver={handleRowDragOver} onRowDrop={handleRowDrop} />
      )
      if (editingId === l.id) {
        rows.push(
          <EditPanel key={`edit-${l.id}`} l={l} projects={projects} groupList={groupList}
            onSave={saveEdit} onCancel={() => setEditingId(null)} />
        )
      }
      if (l.kind === 'group' && isOpen) {
        if (creating && creating.parentId === l.id) {
          rows.push(<InlineNew key="new" kind={creating.kind} indent={(depth + 1) * 18}
            onCancel={() => setCreating(null)} onCreate={doCreate} />)
        }
        rows.push(...renderLevel(l.id, depth + 1))
      }
    }
    return rows
  }

  return (
    <div className="view" onDragOver={handleRootDragOver} onDrop={handleRootDrop}>
      <div className="res-head">
        <div>
          <h2>资源索引</h2>
          <p className="hint">
            按话题分层：话题可嵌套，hover 任意话题行点「＋」往里建；网址点击直达，文件/文件夹在资源管理器定位（选中不打开）。
          </p>
        </div>
        <NewMenu root onPick={(k) => startCreate(null, k)} />
      </div>

      {links.length === 0 && !creating && (
        <p className="empty">还没有任何条目。右上角「＋ 新建」先建话题（或直接建资源），或在对话里对小白说「帮我记下这个网址/文件」。</p>
      )}

      <div className={drop && drop.root ? 'res-tree drop-root' : 'res-tree'}>
        {creating && creating.parentId == null && (
          <InlineNew kind={creating.kind} indent={0}
            onCancel={() => setCreating(null)} onCreate={doCreate} />
        )}
        {renderLevel(0, 0)}
      </div>
    </div>
  )
}
