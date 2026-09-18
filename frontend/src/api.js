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
  capture: (content) =>
    request('/api/capture', { method: 'POST', body: JSON.stringify({ content }) }),

  listInbox: () => request('/api/inbox'),

  confirmInbox: (id, type, title) =>
    request(`/api/inbox/${id}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ type, title }),
    }),

  discardInbox: (id) => request(`/api/inbox/${id}/discard`, { method: 'POST' }),

  listTasks: () => request('/api/tasks'),

  createTask: (task) =>
    request('/api/tasks', { method: 'POST', body: JSON.stringify(task) }),

  updateTask: (id, patch) =>
    request(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),

  deleteTask: (id) => request(`/api/tasks/${id}`, { method: 'DELETE' }),
}
