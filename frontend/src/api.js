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
  confirmInbox: (id, type, title) =>
    request(`/api/inbox/${id}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ type, title }),
    }),
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
  applyProposals: (proposals) =>
    request('/api/proposals/apply', { method: 'POST', body: JSON.stringify({ proposals }) }),

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
