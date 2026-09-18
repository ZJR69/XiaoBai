import { useEffect, useState } from 'react'
import { api } from '../api.js'

const STATUS_META = {
  untracked: { label: '未归档', cls: 'st-untracked' },
  digested: { label: '已消化', cls: 'st-clean' },
  modified: { label: '有修改', cls: 'st-modified' },
}

// 原料区：投放区文件 + 状态标注 + 批量消化（Karpathy 工作流的 raw 层）
export default function RawView({ refreshKey, onChanged }) {
  const [files, setFiles] = useState([])
  const [selected, setSelected] = useState(new Set())
  const [busy, setBusy] = useState(false)
  const [results, setResults] = useState(null)
  const [dropzone, setDropzone] = useState('')

  const load = async () => {
    const res = await api.scanDropzone()
    setFiles(res.files)
    setDropzone(res.dropzone)
  }
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey])

  const toggle = (path) =>
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(path)) n.delete(path)
      else n.add(path)
      return n
    })

  const digest = async () => {
    if (selected.size === 0 || busy) return
    if (!confirm(`消化 ${selected.size} 个文件？将调用大模型生成摘要页，可能需要几分钟。`)) return
    setBusy(true)
    setResults(null)
    try {
      const res = await api.digestFiles([...selected])
      setResults(res.results)
      setSelected(new Set())
      await load()
      onChanged?.()
    } catch (err) {
      alert(err.message)
    } finally {
      setBusy(false)
    }
  }

  const pending = files.filter((f) => f.status !== 'digested' || f.status === 'modified')

  return (
    <div className="view">
      <h2>原料区</h2>
      <p className="hint">
        把想让我处理的文件丢进投放文件夹（内部结构随意，不用整理）：
        <code className="dropzone-path">{dropzone}</code>
        我会标注每个文件的状态；消化 = 提炼成知识库摘要页（课程类资料只登记不读内容）。
      </p>

      {selected.size > 0 && (
        <div className="flash-actions">
          <button className="small-btn primary" onClick={digest} disabled={busy}>
            {busy ? '消化中…（可能要几分钟）' : `消化选中（${selected.size}）`}
          </button>
        </div>
      )}

      {busy && <div className="digesting">消化引擎运行中，LLM 正在阅读你的文件…</div>}

      {results && (
        <div className="digest-results">
          {results.map((r, i) => (
            <div key={i} className={r.ok ? 'digest-ok' : 'digest-fail'}>
              {r.ok ? '✓' : '✗'} {r.path}
              {r.source_page && ` → ${r.source_page}`}
              {r.note && `（${r.note}）`}
              {r.error && `（${r.error}）`}
            </div>
          ))}
        </div>
      )}

      {files.length === 0 && (
        <p className="empty">
          投放区是空的。丢几个文件进去（md/txt/docx/pdf），然后刷新这里的列表。
        </p>
      )}

      <div className="flash-list">
        {files.map((f) => {
          const meta = STATUS_META[f.status] || STATUS_META.untracked
          return (
            <div
              key={f.path}
              className={selected.has(f.path) ? 'flash-item selected' : 'flash-item'}
              onClick={() => toggle(f.path)}
            >
              <input
                type="checkbox"
                checked={selected.has(f.path)}
                onChange={() => toggle(f.path)}
                onClick={(e) => e.stopPropagation()}
              />
              <span className="flash-content">
                {f.name}
                <span className="flash-time"> {f.path !== f.name ? `（${f.path}）` : ''}</span>
                {!f.supported && <span className="flash-time"> [不可读格式，仅登记]</span>}
              </span>
              <span className="flash-time">{(f.size / 1024).toFixed(1)} KB</span>
              <span className={`status-chip ${meta.cls}`}>{meta.label}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
