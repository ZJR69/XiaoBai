// 后端 API 封装（经 Vite 代理走相对路径）

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(detail.detail || `请求失败 ${res.status}`)
  }
  return res.json()
}

export const api = {
  // 捕获与收件箱
  capture: (content) =>
    request('/api/capture', { method: 'POST', body: JSON.stringify({ content }) }),
  listInbox: () => request('/api/inbox'),
  discardInbox: (id) => request(`/api/inbox/${id}/discard`, { method: 'POST' }),

  // 任务
  listTasks: () => request('/api/tasks'),
  createTask: (task) =>
    request('/api/tasks', { method: 'POST', body: JSON.stringify(task) }),
  updateTask: (id, patch) =>
    request(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteTask: (id) => request(`/api/tasks/${id}`, { method: 'DELETE' }),

  // 项目
  listProjects: (includeClosed) =>
    request(`/api/projects${includeClosed ? '?include_closed=true' : ''}`),
  createProject: (p) =>
    request('/api/projects', { method: 'POST', body: JSON.stringify(p) }),
  updateProject: (id, patch) =>
    request(`/api/projects/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  addLog: (projectId, kind, content) =>
    request(`/api/projects/${projectId}/logs`, {
      method: 'POST',
      body: JSON.stringify({ kind, content }),
    }),
  listLogs: (projectId) => request(`/api/projects/${projectId}/logs`),
  updateLog: (projectId, logId, patch) =>
    request(`/api/projects/${projectId}/logs/${logId}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteLog: (projectId, logId) =>
    request(`/api/projects/${projectId}/logs/${logId}`, { method: 'DELETE' }),
  contextPack: (projectId) => request(`/api/projects/${projectId}/context-pack`),

  // 生活角
  listReminders: () => request('/api/reminders'),
  createReminder: (r) =>
    request('/api/reminders', { method: 'POST', body: JSON.stringify(r) }),
  toggleReminder: (id) => request(`/api/reminders/${id}/toggle`, { method: 'POST' }),
  deleteReminder: (id) => request(`/api/reminders/${id}`, { method: 'DELETE' }),

  // 对话
  chat: (message, history) =>
    request('/api/chat', { method: 'POST', body: JSON.stringify({ message, history }) }),
  // 会话管理（多会话 + 分支）
  listChatSessions: () => request('/api/chat/sessions'),
  deleteChatSession: (id) => request(`/api/chat/sessions/${id}`, { method: 'DELETE' }),
  sessionMessages: (id) => request(`/api/chat/sessions/${id}/messages`),
  branchSession: (id, messageId) =>
    request(`/api/chat/sessions/${id}/branch`, {
      method: 'POST',
      body: JSON.stringify({ message_id: messageId }),
    }),
  // 流式对话（SSE）：onDelta 收增量，返回结束事件 {done, reply, actions, session_id}
  chatStream: async (message, history, onDelta, sessionId) => {
    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, history, session_id: sessionId ?? undefined }),
    })
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}))
      throw new Error(detail.detail || `请求失败 ${res.status}`)
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    let final = null
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const raw = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        if (!raw.startsWith('data: ')) continue
        try {
          const ev = JSON.parse(raw.slice(6))
          if (ev.done) final = ev
          else if (ev.delta) onDelta?.(ev.delta)
        } catch { /* 半截 JSON 由下轮补齐 */ }
      }
    }
    return final
  },

  // 沉淀（review/proposals/apply）已退役 2026-09-26：进展自动入库（后端 _auto_memory），操作走确认卡

  // 对话驱动操作（确认卡：执行/撤销）+ 实体锚点
  executeAction: (action) =>
    request('/api/chat/execute', { method: 'POST', body: JSON.stringify(action) }),
  undoAction: (undoId) =>
    request('/api/chat/undo', { method: 'POST', body: JSON.stringify({ undo_id: undoId }) }),
  listAnchors: () => request('/api/anchors'),

  // 资源索引（重构蓝图阶段二：知识库新定位——链接/文件/资料地址登记与直达）
  listLinks: () => request('/api/links'),
  createLink: (l) => request('/api/links', { method: 'POST', body: JSON.stringify(l) }),
  updateLink: (id, patch) => request(`/api/links/${id}`, { method: 'PUT', body: JSON.stringify(patch) }),
  moveLink: (id, body) => request(`/api/links/${id}/move`, { method: 'POST', body: JSON.stringify(body) }),
  deleteLink: (id) => request(`/api/links/${id}`, { method: 'DELETE' }),
  openLink: (id) => request(`/api/links/${id}/open`, { method: 'POST' }),
  pickLinkPath: (kind) => request('/api/links/pick', { method: 'POST', body: JSON.stringify({ kind }) }),

  // 日程表（M4）
  listSchedule: () => request('/api/schedule'),
  todaySchedule: () => request('/api/schedule/today'),
  weekSchedule: (start) => request(`/api/schedule/week${start ? `?start=${start}` : ''}`),
  createSlot: (s) =>
    request('/api/schedule', { method: 'POST', body: JSON.stringify(s) }),
  updateSlot: (id, patch) =>
    request(`/api/schedule/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteSlot: (id) => request(`/api/schedule/${id}`, { method: 'DELETE' }),

  // 一次性日程（日程的默认形态；周期性课程/固定组会才用上面的 slots）
  listEvents: (start, end) =>
    request(`/api/schedule/events${start || end ? `?${start ? `start=${start}` : ''}${start && end ? '&' : ''}${end ? `end=${end}` : ''}` : ''}`),
  createEvent: (e) =>
    request('/api/schedule/events', { method: 'POST', body: JSON.stringify(e) }),
  updateEvent: (id, patch) =>
    request(`/api/schedule/events/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteEvent: (id) => request(`/api/schedule/events/${id}`, { method: 'DELETE' }),

  // 通知（M4/M6 调度器）
  listNotifications: () => request('/api/notifications'),
  dismissNotifications: (ids) =>
    request('/api/notifications/dismiss', { method: 'POST', body: JSON.stringify({ ids }) }),

  // 早间简报（M4）
  getBriefing: () => request('/api/briefing'),
  briefingShouldShow: () => request('/api/briefing/should-show'),
  markBriefingShown: () => request('/api/briefing/mark-shown', { method: 'POST' }),

  // 晚间复盘（M4 FR-4.4）
  feedbackShouldStart: () => request('/api/feedback/should-start'),
  feedbackStart: () => request('/api/feedback/start', { method: 'POST' }),
  feedbackExtract: (messages) =>
    request('/api/feedback/extract', { method: 'POST', body: JSON.stringify({ messages }) }),

  // 统一搜索（FR-2.5）
  search: (q) => request(`/api/search?q=${encodeURIComponent(q)}`),

  // 状态栏
  statusBar: async () => {
    const [tasks, inbox, reminders] = await Promise.all([
      request('/api/tasks'),
      request('/api/inbox'),
      request('/api/reminders'),
    ])
    const open = tasks.filter((t) => t.status !== 'done').length
    return { openTasks: open, inbox: inbox.length, reminders: reminders.filter((r) => r.active).length }
  },

  // 保存筛选（对话裁决入库；页面只读+删除）
  listFilters: () => request('/api/filters'),
  createFilter: (f) =>
    request('/api/filters', { method: 'POST', body: JSON.stringify(f) }),
  deleteFilter: (id) => request(`/api/filters/${id}`, { method: 'DELETE' }),
}
