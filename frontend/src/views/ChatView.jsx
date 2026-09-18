import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'

export default function ChatView({ refresh, initialMessage, onSeedConsumed }) {
  const [messages, setMessages] = useState([]) // {role, content}
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [proposals, setProposals] = useState(null) // 回顾生成的提案清单
  const [reviewing, setReviewing] = useState(false)
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, proposals])

  // 刷新后恢复今日对话（chat_messages 不做只写不读的假持久化）
  // 例外：从闪记带种子消息进来时不加载，避免覆盖正在进行的处理流
  useEffect(() => {
    if (initialMessage) return
    api.chatHistory()
      .then((res) => {
        if (res.messages.length > 0) setMessages(res.messages)
      })
      .catch(console.error)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const sendText = async (raw) => {
    const text = raw.trim()
    if (!text || busy) return
    const history = messages.slice(-20)
    setMessages((m) => [...m, { role: 'user', content: text }])
    setBusy(true)
    try {
      const { reply } = await api.chat(text, history)
      setMessages((m) => [...m, { role: 'assistant', content: reply }])
    } catch (err) {
      setMessages((m) => [...m, { role: 'assistant', content: `（出错了：${err.message}）` }])
    } finally {
      setBusy(false)
    }
  }

  // 召唤式处理：从闪记视图带来的首批消息自动发送
  const seedSent = useRef(false)
  useEffect(() => {
    if (initialMessage && !seedSent.current) {
      seedSent.current = true
      onSeedConsumed?.()
      sendText(initialMessage)
    }
  }, [initialMessage])

  const send = (e) => {
    e.preventDefault()
    const text = input
    setInput('')
    sendText(text)
  }

  // 回顾对话 → 批量提案（防污染：对话中绝不打断，按需回顾）
  const review = async () => {
    if (messages.length === 0 || reviewing) return
    setReviewing(true)
    try {
      const res = await api.reviewConversation(messages)
      setProposals(res)
    } catch (err) {
      setProposals({ proposals: [], note: `回顾失败：${err.message}` })
    } finally {
      setReviewing(false)
    }
  }

  const decide = async (idx, action) => {
    setProposals((p) => {
      const next = { ...p, proposals: p.proposals.map((x, i) => (i === idx ? { ...x, _decision: action } : x)) }
      return next
    })
  }

  const applyAll = async () => {
    const approved = proposals.proposals.filter((p) => p._decision === 'approve')
    if (approved.length === 0) {
      setProposals(null)
      return
    }
    const res = await api.applyProposals(approved)
    // 如实汇报：成功与失败分开说，不谎报全入库
    const ok = res.applied.filter((a) => a.ok !== false)
    const failed = res.applied.filter((a) => a.ok === false)
    const parts = []
    if (ok.length) {
      parts.push(`已入库 ${ok.length} 条：\n` + ok.map((a) => `· ${a.title}${a.note ? `（${a.note}）` : ''}`).join('\n'))
    }
    if (failed.length) {
      parts.push(`未执行 ${failed.length} 条（请手动处理）：\n` + failed.map((a) => `· ${a.title}（${a.error}）`).join('\n'))
    }
    setMessages((m) => [
      ...m,
      { role: 'assistant', content: parts.join('\n\n') || '没有可执行的提案。' },
    ])
    setProposals(null)
    refresh?.()
  }

  return (
    <div className="chat-view">
      <div className="chat-scroll">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p>和小白说话。它记得你的项目和任务，直接说事就行。</p>
            <p className="hint">例：今天该干什么？/ 把 XX 的截止推到周五 / 我最近有点乱，帮我理一理</p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === 'user' ? 'bubble user' : 'bubble assistant'}>
            <div className="bubble-name">{m.role === 'user' ? '我' : '小白'}</div>
            <div className="bubble-content">{m.content}</div>
          </div>
        ))}
        {busy && <div className="bubble assistant"><div className="bubble-name">小白</div><div className="bubble-content typing">…</div></div>}

        {proposals && (
          <div className="proposal-panel">
            <div className="proposal-head">
              记忆同步 · 提案清单
              <button className="icon-btn" onClick={() => setProposals(null)}>×</button>
            </div>
            {proposals.note && <p className="proposal-note">{proposals.note}</p>}
            {proposals.proposals?.length === 0 && !proposals.note && (
              <p className="proposal-note">这段对话没有需要入库的共识。</p>
            )}
            {proposals.proposals?.map((p, i) => (
              <div key={i} className={`proposal-item ${p._decision || ''}`}>
                <div className="proposal-title">
                  <span className="kind-chip">{p.kind}</span> {p.title}
                </div>
                <div className="proposal-detail">{p.detail}</div>
                <div className="proposal-evidence">来源：{p.evidence}</div>
                {!p._decision ? (
                  <div className="proposal-actions">
                    <button className="small-btn primary" onClick={() => decide(i, 'approve')}>同意</button>
                    <button className="small-btn" onClick={() => decide(i, 'reject')}>丢弃</button>
                  </div>
                ) : (
                  <div className="proposal-actions">
                    {p._decision === 'approve' ? '✓ 将入库' : '✗ 已丢弃'}
                    <button className="small-btn" onClick={() => decide(i, undefined)}>撤销</button>
                  </div>
                )}
              </div>
            ))}
            <button className="small-btn primary wide" onClick={applyAll}>
              执行入库（仅同意项）
            </button>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <form className="chat-input" onSubmit={send}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="和小白说话…"
          disabled={busy}
        />
        <button type="button" className="review-btn" onClick={review} disabled={reviewing || messages.length === 0} title="回顾本段对话，提取共识生成入库提案">
          {reviewing ? '回顾中…' : '回顾对话'}
        </button>
        <button type="submit" disabled={busy || !input.trim()}>发送</button>
      </form>
    </div>
  )
}
