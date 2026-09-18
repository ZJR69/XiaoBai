import { useEffect, useState } from 'react'
import { api } from '../api.js'
import RawView from './RawView.jsx'

const STATUS_META = {
  clean: { label: '已归档', cls: 'st-clean' },
  modified: { label: '有修改', cls: 'st-modified' },
  untracked: { label: '未归档', cls: 'st-untracked' },
}

export default function KnowledgeView({ refreshKey, initialTab }) {
  const [tab, setTab] = useState(initialTab || 'wiki') // wiki | raw | lint
  const [pages, setPages] = useState([])
  const [reading, setReading] = useState(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [lint, setLint] = useState(null)
  const [linting, setLinting] = useState(false)

  const load = async () => setPages(await api.listWikiPages())
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey])

  // 体检 tab：进入即自动跑一次（结果只报告，修复需用户确认）
  useEffect(() => {
    if (tab === 'lint' && !lint && !linting) {
      setLinting(true)
      api.wikiLint()
        .then(setLint)
        .catch(console.error)
        .finally(() => setLinting(false))
    }
  }, [tab])

  const open = async (p) => {
    const res = await api.readWikiPage(p.path)
    setReading({ ...p, content: res.content })
    setEditing(false)
  }

  const archive = async (path) => {
    await api.archiveWikiPage(path)
    setReading(null)
    await load()
  }

  const startEdit = () => {
    setDraft(reading.content)
    setEditing(true)
  }

  const save = async () => {
    setSaving(true)
    try {
      await api.saveWikiPage(reading.path, draft)
      const res = await api.readWikiPage(reading.path)
      setReading({ ...reading, content: res.content, status: 'modified' })
      setEditing(false)
      await load()
    } finally {
      setSaving(false)
    }
  }

  const runLint = async () => {
    const res = await api.wikiLint()
    setLint(res)
  }

  const fixOrphans = async (paths) => {
    await api.fixIndex(paths)
    await runLint()
    await load()
  }

  return (
    <div className="view">
      <div className="sub-tabs">
        <button className={tab === 'wiki' ? 'sub-tab active' : 'sub-tab'} onClick={() => setTab('wiki')}>
          知识页
        </button>
        <button className={tab === 'raw' ? 'sub-tab active' : 'sub-tab'} onClick={() => setTab('raw')}>
          原料区
        </button>
        <button className={tab === 'lint' ? 'sub-tab active' : 'sub-tab'} onClick={() => setTab('lint')}
          title="检查知识库健康：core 精炼度/孤儿页/重复实体">
          体检
        </button>
      </div>

      {tab === 'raw' && <RawView refreshKey={refreshKey} onChanged={load} />}

      {tab === 'lint' && (
        <div>
          <p className="hint">
            检查知识库健康：core 精炼度 / 孤儿页 / 重复实体 / 死链重现。只报告，修复需你确认。
          </p>
          {linting && <p className="empty">体检中…</p>}
          {!linting && lint && (
            <div className="lint-panel">
              <div className="notify-head">
                体检报告（{lint.ok ? '一切正常' : `发现 ${lint.issues.length} 个问题`}，检查了 {lint.checked} 个页面）
                <button className="small-btn" onClick={runLint}>重新检查</button>
              </div>
              {lint.issues.map((iss, i) => (
                <div key={i} className="notify-item">
                  <div className="notify-detail" style={{ whiteSpace: 'pre-wrap' }}>{iss.detail}</div>
                  {iss.paths && iss.kind === 'orphan_pages' && (
                    <button className="small-btn" onClick={() => fixOrphans(iss.paths)}>
                      补进目录
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
          {!linting && !lint && <p className="empty">还没有体检结果。</p>}
        </div>
      )}

      {tab === 'wiki' && (
        <div>
          <p className="hint">
            状态标注：未归档（新内容）→ 你修改后变为 有修改 → 点「归档」整理入库（Git 式工作流）。
          </p>
          {pages.length === 0 && <p className="empty">知识库还是空的。收藏、文章、技术笔记消化后会出现在这里。</p>}

          <div className="wiki-list">
            {pages.map((p) => {
              const meta = STATUS_META[p.status]
              return (
                <div key={p.path} className="wiki-item" onClick={() => open(p)}>
                  <span className="wiki-title">{p.title}</span>
                  <span className="wiki-path">{p.path}</span>
                  <span className={`status-chip ${meta.cls}`}>{meta.label}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {reading && (
        <div className="modal-mask" onClick={() => setReading(null)}>
          <div className="modal wide" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h3>{reading.title}</h3>
              <span className={`status-chip ${STATUS_META[reading.status].cls}`}>
                {STATUS_META[reading.status].label}
              </span>
            </div>
            {editing ? (
              <textarea
                className="wiki-editor"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                spellCheck={false}
              />
            ) : (
              <pre className="wiki-content">{reading.content}</pre>
            )}
            <div className="modal-actions">
              {editing ? (
                <>
                  <button className="small-btn primary" onClick={save} disabled={saving}>
                    {saving ? '保存中…' : '保存'}
                  </button>
                  <button className="small-btn" onClick={() => setEditing(false)}>取消</button>
                </>
              ) : (
                <>
                  <button className="small-btn" onClick={startEdit}>编辑</button>
                  {reading.status !== 'clean' && (
                    <button className="small-btn primary" onClick={() => archive(reading.path)}>归档此页</button>
                  )}
                </>
              )}
              <button className="small-btn" onClick={() => setReading(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
