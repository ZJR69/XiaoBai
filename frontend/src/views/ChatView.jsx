import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'

export default function ChatView({ refresh, initialMessage, onSeedConsumed, notice, onNoticeConsumed }) {
  const [messages, setMessages] = useState([]) // {role, content}
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [proposals, setProposals] = useState(null) // 回顾生成的提案清单
  const [reviewing, setReviewing] = useState(false)
  const [feedbackMode, setFeedbackMode] = useState(false)
  const [extracting, setExtracting] = useState(false) // 结束复盘防连点
  const bottomRef = useRef(null)
  const historyLoaded = useRef(false)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, proposals])

  // 历史消息打标：☀=早报 🌙=复盘 ⏰=提醒（后端持久化时带前缀，恢复后样式不降级）
  const tagMessage = (m) => ({
    ...m,
    briefing: m.role === 'assistant' && m.content.startsWith('☀'),
    feedback: m.role === 'assistant' && m.content.startsWith('🌙'),
    notice: m.role === 'assistant' && m.content.startsWith('⏰'),
  })

  // ── 初始化（严格时序：先加载历史 → 再简报 → 再复盘，消除挂载竞态）──
  useEffect(() => {
    if (initialMessage) return
    let cancelled = false
    ;(async () => {
      // 1. 恢复今日对话
      try {
        const res = await api.chatHistory()
        if (!cancelled && res.messages.length > 0) setMessages(res.messages.map(tagMessage))
      } catch (err) {
        console.error('history load failed:', err)
      }
      if (cancelled) return
      historyLoaded.current = true
      // 2. 早间简报：当天首次
      try {
        const { show } = await api.briefingShouldShow()
        if (!cancelled && show) {
          const b = await api.getBriefing()
          if (!cancelled) {
            setMessages((m) => [...m, { role: 'assistant', content: `☀ ${b.content}`, briefing: true }])
            await api.markBriefingShown() // 服务端同步持久化进对话流（切视图不丢）
          }
        }
      } catch (err) {
        console.error('briefing failed:', err)
      }
      if (cancelled) return
      // 3. 晚间复盘：20 点后 pending → 发起；in_progress → 恢复按钮
      try {
        const { status } = await api.feedbackShouldStart()
        if (!cancelled && status === 'pending') {
          const res = await api.feedbackStart()
          if (!cancelled && res.ok) {
            setMessages((m) => [...m, { role: 'assistant', content: `🌙 ${res.opening}`, feedback: true }])
            setFeedbackMode(true)
          }
        } else if (!cancelled && status === 'in_progress') {
          setFeedbackMode(true)
        }
      } catch (err) {
        console.error('feedback check failed:', err)
      }
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 周期检查（60s）：跨 20 点发起复盘、跨天出简报（应用常开场景）
  useEffect(() => {
    const timer = setInterval(async () => {
      if (initialMessage || !historyLoaded.current) return
      try {
        const fb = await api.feedbackShouldStart()
        if (fb.status === 'pending') {
          const res = await api.feedbackStart()
          if (res.ok) {
            setMessages((m) => [...m, { role: 'assistant', content: `🌙 ${res.opening}`, feedback: true }])
            setFeedbackMode(true)
          }
        } else if (fb.status === 'in_progress') {
          setFeedbackMode(true)
        } else if (fb.status === 'not_yet') {
          setFeedbackMode(false)
        }
        const br = await api.briefingShouldShow()
        if (br.show) {
          const b = await api.getBriefing()
          setMessages((m) => [...m, { role: 'assistant', content: `☀ ${b.content}`, briefing: true }])
          await api.markBriefingShown()
        }
      } catch {
        /* 后端未启动时静默 */
      }
    }, 60000)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 重要通知即时插入（去重：库里已有同文消息则不重复插）
  useEffect(() => {
    if (notice && notice.id) {
      const content = `⏰ ${notice.title}\n${notice.detail}`.trim()
      setMessages((m) => {
        if (m.some((x) => x.content === content)) return m
        return [...m, { role: 'assistant', content, notice: true }]
      })
      onNoticeConsumed?.(notice.id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notice])

  // 结束复盘：提取信号（完成的事/负荷/修正意见）→ 阈值自校准
  const endFeedback = async () => {
    if (busy || extracting) return
    setExtracting(true)
    try {
      const res = await api.feedbackExtract(messages.slice(-20))
      if (!res.ok) {
        setMessages((m) => [...m, { role: 'assistant', content: `（${res.error || '复盘提取失败'}）`, feedback: true }])
        setFeedbackMode(false)
        return
      }
      const s = res.signals || {}
      const doneList = (s.completed || []).length ? `\n今天实际完成：${s.completed.join('、')}` : ''
      const loadText = { easy: '负荷轻松', ok: '负荷刚好', overloaded: '负荷过载' }[s.load] || '负荷正常'
      const adj = res.threshold_changed
        ? `\n（并行度阈值已调整：${res.threshold_changed} 件，后续简报按新阈值预警）`
        : ''
      setMessages((m) => [...m, {
        role: 'assistant',
        content: `复盘记录已存。${loadText}。${doneList}${adj}\n明天简报见。`,
        feedback: true,
      }])
      setFeedbackMode(false)
    } catch (err) {
      setMessages((m) => [...m, { role: 'assistant', content: `（复盘提取失败：${err.message}）` }])
    } finally {
      setExtracting(false)
    }
  }

  // 重看今日简报（历史里翻不到时的手动入口）
  const showBriefing = async () => {
    if (busy) return
    try {
      const b = await api.getBriefing()
      setMessages((m) => [...m, { role: 'assistant', content: `☀ ${b.content}`, briefing: true }])
    } catch (err) {
      setMessages((m) => [...m, { role: 'assistant', content: `（简报获取失败：${err.message}）` }])
    }
  }

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
    try {
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
    } catch (err) {
      setMessages((m) => [
        ...m,
        { role: 'assistant', content: `（入库执行失败：${err.message}，提案保留，可重试）` },
      ])
    }
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
          <div key={i} className={m.role === 'user' ? 'bubble user' : m.briefing ? 'bubble assistant briefing' : m.notice ? 'bubble assistant notice' : m.feedback ? 'bubble assistant feedback' : 'bubble assistant'}>
            <div className="bubble-name">{m.briefing ? '小白 · 早报' : m.notice ? '小白 · 提醒' : m.feedback ? '小白 · 晚间复盘' : m.role === 'user' ? '我' : '小白'}</div>
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
        {feedbackMode && (
          <button type="button" className="review-btn feedback" onClick={endFeedback} disabled={busy || extracting} title="结束今晚的复盘，小白提取信号校准后续安排">
            {extracting ? '提取中…' : '结束复盘'}
          </button>
        )}
        <button type="button" className="review-btn" onClick={showBriefing} disabled={busy} title="重看今日早报">
          早报
        </button>
        <button type="button" className="review-btn" onClick={review} disabled={reviewing || messages.length === 0} title="回顾本段对话，提取共识生成入库提案">
          {reviewing ? '回顾中…' : '回顾对话'}
        </button>
        <button type="submit" disabled={busy || !input.trim()}>发送</button>
      </form>
    </div>
  )
}
