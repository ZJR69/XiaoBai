import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import DraftStrip from './DraftStrip.jsx'

export default function ChatView({ refresh, notice, onNoticeConsumed, goView }) {
  const [messages, setMessages] = useState([]) // {role, content, actions?}
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [feedbackMode, setFeedbackMode] = useState(false)
  const [extracting, setExtracting] = useState(false) // 结束复盘防连点
  const [anchors, setAnchors] = useState([]) // 实体锚点名单（真实库条目，IDS 2.1）
  const [sessions, setSessions] = useState([]) // 会话列表（最近活跃在前）
  const [activeSession, setActiveSession] = useState(null) // 当前会话 id（null = 新对话，首条消息时后端建）
  const [sessionOpen, setSessionOpen] = useState(false) // 左侧会话面板开关
  const [mention, setMention] = useState(null) // 「#」引用中：{query, start}
  const [mentionIdx, setMentionIdx] = useState(0)
  const [draftMode, setDraftMode] = useState(false) // 输入框模式：对话 / 草稿（闪记并入，速记零决策）
  const [drafts, setDrafts] = useState([]) // 草稿本条目（inbox 未处理）
  const bottomRef = useRef(null)
  const historyLoaded = useRef(false)
  const inputRef = useRef(null)

  // 输入框自动增高（对标主流大模型产品）：1 行起步，随内容长高，约 6 行封顶后内部滚动
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [input])

  const loadAnchors = () =>
    api.listAnchors().then((r) => setAnchors(r.anchors || [])).catch(() => {})
  useEffect(() => { loadAnchors() }, [])

  const loadDrafts = () => api.listInbox().then(setDrafts).catch(() => {})
  useEffect(() => { loadDrafts() }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

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
      // 3. 晚间复盘（召唤式，阶段四）：只恢复进行中状态，不自动发起
      try {
        const { status } = await api.feedbackShouldStart()
        if (!cancelled && status === 'in_progress') setFeedbackMode(true)
      } catch (err) {
        console.error('feedback check failed:', err)
      }
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 周期检查（60s）：跨天出简报（应用常开场景；复盘改召唤式后不再自动发起）
  useEffect(() => {
    const timer = setInterval(async () => {
      if (!historyLoaded.current) return
      try {
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

  // 召唤式复盘（阶段四）：想复盘点一下，小白带着今日数据开场
  const startFeedback = async () => {
    if (busy) return
    try {
      const res = await api.feedbackStart()
      if (res.ok) {
        setMessages((m) => [...m, { role: 'assistant', content: `🌙 ${res.opening}`, feedback: true }])
        setFeedbackMode(true)
      } else {
        setMessages((m) => [...m, { role: 'assistant', content: '（今天的复盘已经存过了，明天再来）', feedback: true }])
      }
    } catch (err) {
      setMessages((m) => [...m, { role: 'assistant', content: `（复盘发起失败：${err.message}）` }])
    }
  }

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

  // 资源锚点（阶段二）：网址新开标签；文件/文件夹让后端 explorer 定位（选中不打开）
  const openLinkAnchor = async (a) => {
    if (a.link_kind === 'url') {
      window.open(a.target, '_blank', 'noopener')
      return
    }
    try {
      await api.openLink(a.link_id)
    } catch (err) {
      alert(`打开失败：${err.message}`)
    }
  }

  const renderAnchored = (text) => {
    if (!anchorRe) return text
    return text.split(anchorRe).map((part, i) => {
      const a = anchorMap[part]
      if (!a) return part
      if (a.kind === 'link') {
        return (
          <a key={i} className="entity-anchor" title={`${a.hint}：${a.target}`}
            onClick={() => openLinkAnchor(a)}>
            {part}
          </a>
        )
      }
      // 锚点只剩 project/task（阶段四 wiki 锚点已摘）：都跳工作台
      return (
        <a key={i} className="entity-anchor" title={a.hint}
          onClick={() => goView?.('workbench')}>
          {part}
        </a>
      )
    })
  }

  // ── 草稿本（闪记并入）：回车速记不走 LLM；转待办进任务池（无日期）；处理走对话提案闭环 ──
  const captureDraft = async (raw) => {
    const text = raw.trim()
    if (!text) return
    try {
      await api.capture(text)
      loadDrafts()
    } catch (err) {
      setMessages((m) => [...m, { role: 'assistant', content: `（草稿没存上：${err.message}）` }])
    }
  }

  const draftToTask = async (d) => {
    try {
      await api.createTask({ title: d.content, due_at: null, project_id: null })
      await api.discardInbox(d.id)
      loadDrafts()
      refresh?.()
    } catch (err) {
      alert(`转待办失败：${err.message}`)
    }
  }

  const dropDraft = async (d) => {
    await api.discardInbox(d.id).catch(() => {})
    loadDrafts()
  }

  // 发送共用入口：对话模式走 LLM，草稿模式直存 inbox
  const submit = () => {
    const text = input
    setInput('')
    setMention(null)
    if (draftMode) {
      captureDraft(text)
      return
    }
    sendText(text)
  }

  const send = (e) => {
    e.preventDefault()
    submit()
  }

  // ── 「#」引用真实条目（实体索引，防幻觉）：输入 #关键字 弹真实库条目，选中替换为精确名称 ──
  const onInputChange = (e) => {
    setInput(e.target.value)
    if (draftMode) return // 草稿模式不做 # 引用（速记零决策）
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
    // 「#」引用面板打开时：方向键导航 / Enter 选中 / Esc 关闭（优先于发送）
    if (mention && mentionOptions.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setMentionIdx((i) => (i + 1) % mentionOptions.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setMentionIdx((i) => (i - 1 + mentionOptions.length) % mentionOptions.length)
        return
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        pickMention(mentionOptions[mentionIdx])
        return
      }
      if (e.key === 'Escape') {
        setMention(null)
        return
      }
    }
    // Enter 发送 / Shift+Enter 换行（对标主流大模型产品；中文输入法组词确认的 Enter 不触发发送）
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  // 沉淀（回顾对话→提案裁决）已退役（2026-09-26 铁律分层定案）：
  // 进展记录由后端 _auto_memory 对话结束自动入库（进行中的事页面可见可删可改）；
  // 结构化操作（建任务/登记资源）由操作卡承担。这里不再有任何提案 UI。

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
      {/* 顶栏（对标 ChatGPT/Claude）：左=会话侧栏开关+新对话，右=早报/复盘（✦沉淀已随 2026-09-26 自动记忆定案退役） */}
      <div className="chat-topbar">
        <div className="chat-topbar-side">
          <button
            className="topbar-icon"
            onClick={() => { if (!sessionOpen) loadSessions(); setSessionOpen(!sessionOpen) }}
            title="对话列表：切换、新建、删除、分支"
          >
            <svg viewBox="0 0 24 24"><path d="M4 6.5h16M4 12h16M4 17.5h10"/></svg>
          </button>
          <button className="topbar-icon" onClick={newSession} disabled={busy} title="新对话">
            <svg viewBox="0 0 24 24"><path d="M15.5 4.5l4 4L8 20H4v-4L15.5 4.5Z"/><path d="M13.5 6.5l4 4"/></svg>
          </button>
        </div>
        <div className="chat-topbar-side">
          <button className="topbar-icon" onClick={showBriefing} disabled={busy} title="重看今日早报">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3.8"/><path d="M12 3.5v2M12 18.5v2M3.5 12h2M18.5 12h2M6 6l1.4 1.4M16.6 16.6 18 18M18 6l-1.4 1.4M7.4 16.6 6 18"/></svg>
          </button>
          {feedbackMode ? (
            <button className="topbar-icon on" onClick={endFeedback} disabled={busy || extracting} title="结束复盘：提取信号，校准后续安排">
              <svg viewBox="0 0 24 24"><path d="M20 13.5A8 8 0 0 1 10.5 4 8 8 0 1 0 20 13.5Z"/></svg>{extracting ? '…' : ''}
            </button>
          ) : (
            <button className="topbar-icon" onClick={startFeedback} disabled={busy} title="复盘：回顾今天，校准明天的安排">
              <svg viewBox="0 0 24 24"><path d="M20 13.5A8 8 0 0 1 10.5 4 8 8 0 1 0 20 13.5Z"/></svg>
            </button>
          )}
        </div>
      </div>
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
                  <div key={j} className={`action-card${act._done ? ' done' : ''}${act._skipped ? ' skipped' : ''}${String(act.op).startsWith('delete_') ? ' destructive' : ''}`}>
                    <div className="action-summary">
                      <span className="kind-chip">{String(act.op).startsWith('delete_') ? '删除' : '操作'}</span>
                      {act.summary || act.op}
                    </div>
                    {String(act.op).startsWith('delete_') && !act._done && (
                      <div className="action-attach danger">将删除「{act.target}」，执行后可在本条内撤销</div>
                    )}
                    {act.op === 'create_project' && (
                      <div className="action-attach">新建事务：<b>{act.patch?.name}</b>{act.patch?.category && `（${{ research: '科研', collaboration: '合作', family: '家庭', personal: '个人', club: '社团', course: '课程', other: '其他' }[act.patch.category] || act.patch.category}）`}</div>
                    )}
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
                    {act.op === 'create_link' && (
                      <div className="action-attach">
                        登记资源：<b>{act.patch?.title}</b>
                        {act.patch?.kind ? `（${{ url: '网址', file: '文件', folder: '文件夹' }[act.patch.kind] || act.patch.kind}）` : ''}
                        <div className="hint">{act.patch?.target}</div>
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
        <div ref={bottomRef} />
      </div>

      <DraftStrip drafts={drafts} onToTask={draftToTask} onDrop={dropDraft} />

      <form className="chat-input" onSubmit={send}>
        {mention && !draftMode && mentionOptions.length > 0 && (
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
        <div className="mode-toggle" title="对话：和小白商量；草稿：速记，有空再处理">
          <button type="button" className={draftMode ? '' : 'on'} onClick={() => setDraftMode(false)}>对话</button>
          <button type="button" className={draftMode ? 'on' : ''} onClick={() => setDraftMode(true)}>
            草稿{drafts.length > 0 ? ` ${drafts.length}` : ''}
          </button>
        </div>
        <textarea
          ref={inputRef}
          rows={1}
          value={input}
          onChange={onInputChange}
          onKeyDown={onInputKey}
          placeholder={draftMode ? '速记：想到什么记什么，回车存草稿…' : '和小白说话…（# 引用条目，Shift+Enter 换行）'}
          disabled={busy && !draftMode}
        />
        <button type="submit" disabled={draftMode ? !input.trim() : busy || !input.trim()}>{draftMode ? '记下' : '发送'}</button>
      </form>
    </div>
  )
}
