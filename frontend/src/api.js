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
  listProjects: () => request('/api/projects'),
  createProject: (p) =>
    request('/api/projects', { method: 'POST', body: JSON.stringify(p) }),
  addLog: (projectId, kind, content) =>
    request(`/api/projects/${projectId}/logs`, {
      method: 'POST',
      body: JSON.stringify({ kind, content }),
    }),
  contextPack: (projectId) => request(`/api/projects/${projectId}/context-pack`),

  // 知识库
  listWikiPages: () => request('/api/wiki/pages'),
  readWikiPage: (path) => request(`/api/wiki/page?path=${encodeURIComponent(path)}`),
  archiveWikiPage: (path) =>
    request('/api/wiki/archive', { method: 'POST', body: JSON.stringify({ path }) }),

  // 投放区（原料）
  scanDropzone: () => request('/api/raw/scan'),
  digestFiles: (paths) =>
    request('/api/raw/digest', { method: 'POST', body: JSON.stringify({ paths }) }),
  rawEvents: (since) => request(`/api/raw/events?since=${since}`),

  // 生活角
  listReminders: () => request('/api/reminders'),
  createReminder: (r) =>
    request('/api/reminders', { method: 'POST', body: JSON.stringify(r) }),
  toggleReminder: (id) => request(`/api/reminders/${id}/toggle`, { method: 'POST' }),
  deleteReminder: (id) => request(`/api/reminders/${id}`, { method: 'DELETE' }),

  // 对话
  chat: (message, history) =>
    request('/api/chat', { method: 'POST', body: JSON.stringify({ message, history }) }),
  chatHistory: (date) =>
    request(`/api/chat/history${date ? `?date=${date}` : ''}`),
  reviewConversation: (messages) =>
    request('/api/review', { method: 'POST', body: JSON.stringify({ messages }) }),
  applyProposals: (proposals, rejected = []) =>
    request('/api/proposals/apply', { method: 'POST', body: JSON.stringify({ proposals, rejected }) }),

  // 对话驱动操作（确认卡：执行/撤销）+ 实体锚点
  executeAction: (action) =>
    request('/api/chat/execute', { method: 'POST', body: JSON.stringify(action) }),
  undoAction: (undoId) =>
    request('/api/chat/undo', { method: 'POST', body: JSON.stringify({ undo_id: undoId }) }),
  listAnchors: () => request('/api/anchors'),

  // 日程表（M4）
  listSchedule: () => request('/api/schedule'),
  todaySchedule: () => request('/api/schedule/today'),
  createSlot: (s) =>
    request('/api/schedule', { method: 'POST', body: JSON.stringify(s) }),
  updateSlot: (id, patch) =>
    request(`/api/schedule/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteSlot: (id) => request(`/api/schedule/${id}`, { method: 'DELETE' }),

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

  // wiki 编辑 + 体检（FR-2.4 / M5）
  saveWikiPage: (path, content) =>
    request('/api/wiki/page', { method: 'PUT', body: JSON.stringify({ path, content }) }),
  wikiLint: () => request('/api/wiki/lint'),
  fixIndex: (paths) =>
    request('/api/wiki/fix-index', { method: 'POST', body: JSON.stringify({ paths }) }),

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
}
