import { useEffect, useState } from 'react'
import { api } from '../api.js'

// 闪记：哑捕获池。勾选若干条 → 召唤小白对话式处理（看懂就提案，看不懂就问）
export default function FlashView({ refreshKey, refresh, startChatWith }) {
  const [items, setItems] = useState([])
  const [selected, setSelected] = useState(new Set())

  const load = async () => setItems(await api.listInbox())
  useEffect(() => {
    load().catch(console.error)
  }, [refreshKey])

  const toggle = (id) =>
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  const discardSelected = async () => {
    await Promise.all([...selected].map((id) => api.discardInbox(id)))
    setSelected(new Set())
    await load()
    refresh?.()
  }

  const processSelected = () => {
    const chosen = items.filter((it) => selected.has(it.id))
    const text = '处理这几条闪记：\n' + chosen.map((it, i) => `${i + 1}. ${it.content}`).join('\n')
    startChatWith(text)
  }

  return (
    <div className="view">
      <h2>闪记</h2>
      <p className="hint">
        Ctrl+Shift+X 随手记，不用分类，小白不会催你。想处理时勾几条，叫它过来一起过。
      </p>

      {selected.size > 0 && (
        <div className="flash-actions">
          <button className="small-btn primary" onClick={processSelected}>
            去对话处理（{selected.size}）
          </button>
          <button className="small-btn" onClick={discardSelected}>
            丢弃选中
          </button>
        </div>
      )}

      {items.length === 0 && <p className="empty">没有待处理的闪记。</p>}

      <div className="flash-list">
        {items.map((it) => (
          <div
            key={it.id}
            className={selected.has(it.id) ? 'flash-item selected' : 'flash-item'}
            onClick={() => toggle(it.id)}
          >
            <input
              type="checkbox"
              checked={selected.has(it.id)}
              onChange={() => toggle(it.id)}
              onClick={(e) => e.stopPropagation()}
            />
            <span className="flash-content">{it.content}</span>
            <span className="flash-time">{it.captured_at?.slice(5, 16).replace('T', ' ')}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
