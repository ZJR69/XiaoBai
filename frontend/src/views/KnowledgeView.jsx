import { useEffect, useState } from 'react'
import { api } from '../api.js'

const STATUS_META = {
  clean: { label: '已归档', cls: 'st-clean' },
  modified: { label: '有修改', cls: 'st-modified' },
  untracked: { label: '未归档', cls: 'st-untracked' },
}

export default function KnowledgeView({ refreshKey }) {
  const [pages, setPages] = useState([])
  const [reading, setReading] = useState(null)

  const load = async () => setPages(await api.listWikiPages())
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey])

  const open = async (p) => {
    const res = await api.readWikiPage(p.path)
    setReading({ ...p, content: res.content })
  }

  const archive = async (path) => {
    await api.archiveWikiPage(path)
    setReading(null)
    await load()
  }

  return (
    <div className="view">
      <h2>知识库</h2>
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

      {reading && (
        <div className="modal-mask" onClick={() => setReading(null)}>
          <div className="modal wide" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h3>{reading.title}</h3>
              <span className={`status-chip ${STATUS_META[reading.status].cls}`}>
                {STATUS_META[reading.status].label}
              </span>
            </div>
            <pre className="wiki-content">{reading.content}</pre>
            <div className="modal-actions">
              {reading.status !== 'clean' && (
                <button className="small-btn primary" onClick={() => archive(reading.path)}>归档此页</button>
              )}
              <button className="small-btn" onClick={() => setReading(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
