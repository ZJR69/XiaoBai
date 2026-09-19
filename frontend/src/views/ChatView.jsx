import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'

export default function ChatView({ refresh, initialMessage, onSeedConsumed, notice, onNoticeConsumed, goView }) {
  const [messages, setMessages] = useState([]) // {role, content, actions?}
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [proposals, setProposals] = useState(null) // 回顾生成的提案清单
  const [reviewing, setReviewing] = useState(false)
  const [feedbackMode, setFeedbackMode] = useState(false)
  const [extracting, setExtracting] = useState(false) // 结束复盘防连点
  const [anchors, setAnchors] = useState([]) // 实体锚点名单（真实库条目，IDS 2.1）
  const [editIdx, setEditIdx] = useState(null) // 修改中的提案序号
  const [editDraft, setEditDraft] = useState({ title: '', detail: '' })
  const [sessions, setSessions] = useState([]) // 会话列表（最近活跃在前）
  const [activeSession, setActiveSession] = useState(null) // 当前会话 id（null = 新对话，首条消息时后端建）
  const [sessionOpen, setSessionOpen] = useState(false) // 左侧会话面板开关
  const [mention, setMention] = useState(null) // 「#」引用中：{query, start}
  const [mentionIdx, setMentionIdx] = useState(0)
  const bottomRef = useRef(null)
  const historyLoaded = useRef(false)

  const loadAnchors = () =>
    api.listAnchors().then((r) => setAnchors(r.anchors || [])).catch(() => {})
  useEffect(() => { loadAnchors() }, [])

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

  // ── 会话管理 ──
  const loadSessions = async () => {
    try {
      const res = await api.listChatSessions()
      setSessions(res.sessions || [])
      return res.sessions || []
    } catch {
      return []
    }
  }

  // 打开会话：加载其消息（今天的早报等天级消息由后端按需合并）
  const openSession = async (sid) => {
    if (busy) return // 流式输出中切换会话会让增量写到错误的会话视图上
    setSessionOpen(false)
    setProposals(null)
    try {
      const res = await api.sessionMessages(sid)
      setMessages(res.messages.map(tagMessage))
      setActiveSession(sid)
    } catch (err) {
      console.error('session load failed:', err)
    }
  }

  // 新对话：清空视图即可，首条消息发出时后端自动建会话
  const newSession = () => {
    if (busy) return
    setSessionOpen(false)
    setProposals(null)
    setMessages([])
    setActiveSession(null)
  }

  const removeSession = async (sid) => {
    if (busy) return
    if (!window.confirm('删除这个对话？消息不可恢复。')) return
    try {
      await api.deleteChatSession(sid)
      const rest = await loadSessions()
      if (sid === activeSession) {
        if (rest.length > 0) await openSession(rest[0].id)
        else newSession()
      }
    } catch (err) {
      alert(`删除失败：${err.message}`)
    }
  }

  // 从某条消息开分支：复制截至该消息的历史为新会话并切换过去
  const branchFrom = async (m) => {
    if (busy || !m?.id || !activeSession) return
    try {
      const res = await api.branchSession(activeSession, m.id)
      await loadSessions()
      await openSession(res.id)
    } catch (err) {
      alert(`分支失败：${err.message}`)
    }
  }

  // ── 初始化（严格时序：先打开最近会话 → 再简报 → 再复盘，消除挂载竞态）──
  useEffect(() => {
    if (initialMessage) return
    let cancelled = false
    ;(async () => {
      // 1. 打开最近活跃的会话（没有则空视图，等首条消息）
      try {
        const list = await loadSessions()
        if (!cancelled && list.length > 0) {
          const res = await api.sessionMessages(list[0].id)
          if (!cancelled) {
            setMessages(res.messages.map(tagMessage))
            setActiveSession(list[0].id)
          }
        }
      } catch (err) {
        console.error('session load failed:', err)
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
    const history = messages
      .filter((m) => !m.feedback && !m.briefing && !m.notice && !m._streaming)
      .slice(-20)
    setMessages((m) => [...m, { role: 'user', content: text }])
    setBusy(true)
    // 流式：先挂一条空 assistant 消息，增量往上填；<<<ACTION>>> 块不进正文展示
    setMessages((m) => [...m, { role: 'assistant', content: '', _streaming: true }])
    try {
      const final = await api.chatStream(text, history, (delta) => {
        setMessages((ms) => {
          const copy = [...ms]
          const last = copy[copy.length - 1]
          if (last && last._streaming) {
            copy[copy.length - 1] = { ...last, content: last.content + delta }
          }
          return copy
        })
      }, activeSession)
      // 新建会话（activeSession 原为 null）→ 记住；会话列表标题/排序已变 → 刷新
      if (final?.session_id && final.session_id !== activeSession) setActiveSession(final.session_id)
      loadSessions()
      setMessages((ms) => {
        const copy = [...ms]
        const last = copy[copy.length - 1]
        if (last && last._streaming) {
          copy[copy.length - 1] = final
            ? { role: 'assistant', content: final.reply, actions: final.actions || [] }
            : { ...last, _streaming: false }
        }
        return copy
      })
    } catch (err) {
      setMessages((ms) => {
        const copy = [...ms]
        const last = copy[copy.length - 1]
        if (last && last._streaming) {
          copy[copy.length - 1] = { role: 'assistant', content: `${last.content}（出错了：${err.message}）` }
        }
        return copy
      })
    } finally {
      setBusy(false)
    }
  }

  // ── 操作卡（对话驱动操作，IDS 2.4）：执行 / 撤销 / 忽略 ──
  const patchAction = (mi, ai, patch) => {
    setMessages((ms) =>
      ms.map((m, x) =>
        x !== mi ? m : { ...m, actions: m.actions.map((a, y) => (y !== ai ? a : { ...a, ...patch })) }
      )
    )
  }

  const execAction = async (mi, ai) => {
    const act = messages[mi]?.actions?.[ai]
    if (!act) return
    try {
      const res = await api.executeAction({
        op: act.op, target: act.target, patch: act.patch || {}, summary: act.summary || '',
      })
      if (res.ok) {
        patchAction(mi, ai, { _done: true, _undo_id: res.undo_id, _error: null })
        refresh?.()
        loadAnchors() // 新建的任务/提醒即时成为可点击锚点
      } else {
        patchAction(mi, ai, { _error: res.error || '执行失败' })
      }
    } catch (err) {
      patchAction(mi, ai, { _error: err.message })
    }
  }

  const undoAction = async (mi, ai) => {
    const act = messages[mi]?.actions?.[ai]
    if (!act?._undo_id) return
    try {
      const res = await api.undoAction(act._undo_id)
      if (res.ok) {
        patchAction(mi, ai, { _done: false, _undo_id: null })
        refresh?.()
      } else {
        patchAction(mi, ai, { _error: res.error || '撤销失败' })
      }
    } catch (err) {
      patchAction(mi, ai, { _error: err.message })
    }
  }

  // ── 实体锚点（IDS 2.1 防幻觉）：真实条目名渲染为可点击 ──
  const anchorMap = anchors.reduce((acc, a) => {
    if (a.name && a.name.length >= 2 && !acc[a.name]) acc[a.name] = a
    return acc
  }, {})
  const anchorRe = Object.keys(anchorMap).length
    ? new RegExp(`(${Object.keys(anchorMap)
        .sort((a, b) => b.length - a.length) // 长名优先，避免子串吞并
        .map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
        .join('|')})`, 'g')
    : null

  const renderAnchored = (text) => {
    if (!anchorRe) return text
    return text.split(anchorRe).map((part, i) => {
      const a = anchorMap[part]
      if (!a) return part
      const view = a.kind === 'project' ? 'projects' : a.kind === 'task' ? 'timeline' : 'knowledge'
      return (
        <a key={i} className="entity-anchor" title={a.hint}
          onClick={() => goView?.(view)}>
          {part}
        </a>
      )
    })
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
    setMention(null)
    sendText(text)
  }

  // ── 「#」引用真实条目（实体索引，防幻觉）：输入 #关键字 弹真实库条目，选中替换为精确名称 ──
  const onInputChange = (e) => {
    setInput(e.target.value)
    const pos = e.target.selectionStart
    const m = e.target.value.slice(0, pos).match(/#[^\s#]*$/)
    setMention(m ? { query: m[0].slice(1), start: pos - m[0].length } : null)
    setMentionIdx(0)
  }

  const mentionRank = (a) => (a.kind === 'project' ? 0 : 1) // 进行中的事排前
  const mentionOptions = mention
    ? anchors
        .filter((a) => a.name.toLowerCase().includes(mention.query.toLowerCase()))
        .sort((a, b) => mentionRank(a) - mentionRank(b))
        .slice(0, 8)
    : []

  const pickMention = (a) => {
    const end = mention.start + 1 + mention.query.length
    setInput(input.slice(0, mention.start) + a.name + ' ' + input.slice(end))
    setMention(null)
  }

  const onInputKey = (e) => {
    if (!mention || mentionOptions.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setMentionIdx((i) => (i + 1) % mentionOptions.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setMentionIdx((i) => (i - 1 + mentionOptions.length) % mentionOptions.length)
    } else if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      pickMention(mentionOptions[mentionIdx])
    } else if (e.key === 'Escape') {
      setMention(null)
    }
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

  // 修改后同意（IDS 2.2 三种裁决之一）：改标题/内容再入库
  const startEditProposal = (idx) => {
    const p = proposals.proposals[idx]
    setEditIdx(idx)
    setEditDraft({ title: p.title || '', detail: p.detail || '' })
  }

  const saveEditProposal = () => {
    setProposals((p) => ({
      ...p,
      proposals: p.proposals.map((x, i) =>
        i === editIdx ? { ...x, title: editDraft.title.trim() || x.title, detail: editDraft.detail.trim() || x.detail, _decision: 'approve' } : x
      ),
    }))
    setEditIdx(null)
  }

  const applyAll = async () => {
    const approved = proposals.proposals.filter((p) => p._decision === 'approve')
    const rejected = proposals.proposals.filter((p) => p._decision === 'reject')
    if (approved.length === 0 && rejected.length === 0) {
      setProposals(null)
      return
    }
    try {
      const res = await api.applyProposals(approved, rejected)
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
      if (res.flash_closed > 0) {
        parts.push(`闪记池已同步关闭 ${res.flash_closed} 条`)
      }
      setMessages((m) => [
        ...m,
        { role: 'assistant', content: parts.join('\n\n') || `已记录：丢弃 ${rejected.length} 条提案。` },
      ])
      setProposals(null)
      refresh?.()
      loadAnchors() // 新建条目即时成为锚点
    } catch (err) {
      setMessages((m) => [
        ...m,
        { role: 'assistant', content: `（入库执行失败：${err.message}，提案保留，可重试）` },
      ])
    }
  }

  return (
    <div className="chat-view">
      {sessionOpen && (
        <>
          <div className="session-backdrop" onClick={() => setSessionOpen(false)} />
          <div className="session-panel">
            <div className="session-head">
              对话记录
              <button className="icon-btn" onClick={() => setSessionOpen(false)}>×</button>
            </div>
            <button className="session-new" onClick={newSession}>＋ 新对话</button>
            <div className="session-list">
              {sessions.length === 0 && <p className="empty">还没有对话，发第一条消息就开始了。</p>}
              {sessions.map((s) => (
                <div key={s.id} className={`session-item${s.id === activeSession ? ' current' : ''}`}>
                  <button className="session-open" onClick={() => openSession(s.id)} title={s.title}>
                    {s.parent_id != null && <span className="session-branch-mark" title="分支对话">↳</span>}
                    <span className="session-title">{s.title || '对话'}</span>
                    <span className="session-meta">{(s.updated_at || '').slice(5, 10)} · {s.n} 条</span>
                  </button>
                  <button className="session-del" onClick={() => removeSession(s.id)} title="删除这个对话">×</button>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
      <div className="chat-scroll">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p>和小白说话。它记得你的项目和任务，直接说事就行。</p>
            <p className="hint">例：今天该干什么？/ 把 XX 的截止推到周五 / 我最近有点乱，帮我理一理</p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === 'user' ? 'bubble user' : m.briefing ? 'bubble assistant briefing' : m.notice ? 'bubble assistant notice' : m.feedback ? 'bubble assistant feedback' : 'bubble assistant'}>
            <div className="bubble-name">
              {m.briefing ? '小白 · 早报' : m.notice ? '小白 · 提醒' : m.feedback ? '小白 · 晚间复盘' : m.role === 'user' ? '我' : '小白'}
              {m.id && !m.briefing && !m.notice && !m.feedback && activeSession && (
                <button className="branch-btn" onClick={() => branchFrom(m)} title="从这条消息开一个分支，各走各路">分支</button>
              )}
            </div>
            <div className="bubble-content">
              {renderAnchored(m._streaming ? m.content.split('<<<ACTION>>>')[0] : m.content)}
              {m._streaming && <span className="stream-cursor">▍</span>}
            </div>
            {m.actions?.length > 0 && (
              <div className="action-stack">
                {m.actions.map((act, j) => (
                  <div key={j} className={`action-card${act._done ? ' done' : ''}${act._skipped ? ' skipped' : ''}`}>
                    <div className="action-summary">
                      <span className="kind-chip">操作</span>
                      {act.summary || act.op}
                    </div>
                    {act.patch?.project && (
                      <div className="action-attach">挂靠：<b>{act.patch.project}</b></div>
                    )}
                    {(act.op === 'create_event' || act.op === 'delete_event') && (
                      <div className="action-attach">
                        {act.patch?.date && `${act.patch.date} `}
                        {act.patch?.start_time && `${act.patch.start_time}–${act.patch.end_time || ''} `}
                        <b>{act.patch?.title || act.target}</b>
                      </div>
                    )}
                    {act._error && <div className="action-error">{act._error}</div>}
                    {act._done ? (
                      <div className="action-row">
                        <span className="action-state">✓ 已执行</span>
                        <button className="small-btn" onClick={() => undoAction(i, j)}>撤销</button>
                      </div>
                    ) : act._skipped ? (
                      <div className="action-row"><span className="action-state muted">已忽略</span></div>
                    ) : (
                      <div className="action-row">
                        <button className="small-btn primary" onClick={() => execAction(i, j)}>执行</button>
                        <button className="small-btn" onClick={() => patchAction(i, j, { _skipped: true })}>忽略</button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && !(messages.length > 0 && messages[messages.length - 1]._streaming) && (
          <div className="bubble assistant"><div className="bubble-name">小白</div><div className="bubble-content typing">…</div></div>
        )}

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
                {editIdx === i ? (
                  <>
                    <input
                      className="proposal-edit-title"
                      value={editDraft.title}
                      onChange={(e) => setEditDraft((d) => ({ ...d, title: e.target.value }))}
                      placeholder="标题"
                    />
                    <textarea
                      className="proposal-edit-detail"
                      value={editDraft.detail}
                      onChange={(e) => setEditDraft((d) => ({ ...d, detail: e.target.value }))}
                      rows={3}
                      placeholder="内容"
                    />
                    <div className="proposal-actions">
                      <button className="small-btn primary" onClick={saveEditProposal}>保存并同意</button>
                      <button className="small-btn" onClick={() => setEditIdx(null)}>取消</button>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="proposal-title">
                      <span className="kind-chip">{p.kind}</span> {p.title}
                    </div>
                    <div className="proposal-detail">{p.detail}</div>
                    <div className="proposal-evidence">来源：{p.evidence}</div>
                    {!p._decision ? (
                      <div className="proposal-actions">
                        <button className="small-btn primary" onClick={() => decide(i, 'approve')}>同意</button>
                        <button className="small-btn" onClick={() => startEditProposal(i)}>修改后同意</button>
                        <button className="small-btn" onClick={() => decide(i, 'reject')}>丢弃</button>
                      </div>
                    ) : (
                      <div className="proposal-actions">
                        {p._decision === 'approve' ? '✓ 将入库' : '✗ 已丢弃'}
                        <button className="small-btn" onClick={() => decide(i, undefined)}>撤销</button>
                      </div>
                    )}
                  </>
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
        {mention && mentionOptions.length > 0 && (
          <div className="mention-panel">
            {mentionOptions.map((a, i) => (
              <button
                type="button"
                key={a.name}
                className={`mention-item${i === mentionIdx ? ' active' : ''}`}
                onMouseDown={(e) => { e.preventDefault(); pickMention(a) }}
              >
                <span className="mention-name">{a.name}</span>
                <span className="mention-hint">{a.hint}</span>
              </button>
            ))}
          </div>
        )}
        <input
          value={input}
          onChange={onInputChange}
          onKeyDown={onInputKey}
          placeholder="和小白说话…（输入 # 可引用具体某件事）"
          disabled={busy}
        />
        <button
          type="button"
          className="review-btn"
          onClick={() => { if (!sessionOpen) loadSessions(); setSessionOpen(!sessionOpen) }}
          disabled={busy}
          title="对话列表：切换、新建、删除、分支"
        >
          对话
        </button>
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
