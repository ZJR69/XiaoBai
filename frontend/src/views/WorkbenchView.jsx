import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { ScheduleSection } from './TimelineView.jsx'
import TasksView from './TasksView.jsx'

// 工作台（2026-09-26 改版）：日程表 | 任务池 左右两栏，中间滑条拖动调宽（比例存 localStorage）。
// 进行中的事已独立成单独页面（用户裁决：三合一太挤）。
const MIN_R = 0.3
const MAX_R = 0.75
const DEFAULT_R = 0.58

export default function WorkbenchView({ refreshKey, refresh }) {
  const [ratio, setRatio] = useState(() => {
    const v = parseFloat(localStorage.getItem('wb_split'))
    return v >= MIN_R && v <= MAX_R ? v : DEFAULT_R
  })
  const containerRef = useRef(null)

  // 保存筛选页签（2026-10-10）：「全部」+ 各保存筛选，选中态存 localStorage；创建只能走对话裁决卡
  const [filters, setFilters] = useState([])
  const [activeId, setActiveId] = useState(() => localStorage.getItem('wb_filter') || '')

  const loadFilters = () => api.listFilters().then(setFilters).catch(console.error)
  useEffect(() => {
    loadFilters()
  }, [refreshKey])

  const pick = (id) => {
    setActiveId(id)
    if (id) localStorage.setItem('wb_filter', id)
    else localStorage.removeItem('wb_filter')
  }

  const removeFilter = async (f) => {
    if (!window.confirm(`删除筛选「${f.title}」？（任务不受影响）`)) return
    await api.deleteFilter(f.id)
    if (String(f.id) === activeId) pick('')
    await loadFilters()
  }

  const activeFilter = filters.find((f) => String(f.id) === activeId) || null

  // 拖滑条：window 级监听（拖出滑条也不丢），比率钳制在 [MIN_R, MAX_R]
  const onSplitterDown = (e) => {
    e.preventDefault()
    const move = (ev) => {
      if (!containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      const r = Math.min(MAX_R, Math.max(MIN_R, (ev.clientX - rect.left) / rect.width))
      setRatio(r)
      localStorage.setItem('wb_split', String(r))
    }
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  return (
    <div className="workbench-split" ref={containerRef}>
      <div className="wb-pane" style={{ width: `${ratio * 100}%` }}>
        <ScheduleSection refreshKey={refreshKey} refresh={refresh} />
      </div>
      <div className="wb-splitter" onMouseDown={onSplitterDown} title="拖动调整左右宽度" />
      <div className="wb-pane wb-pane-right">
        {filters.length > 0 && (
          <div className="filter-tabs">
            <button
              className={activeId ? 'filter-tab' : 'filter-tab active'}
              onClick={() => pick('')}
            >全部</button>
            {filters.map((f) => (
              <button
                key={f.id}
                className={String(f.id) === activeId ? 'filter-tab active' : 'filter-tab'}
                title={f.filter_json}
                onClick={() => pick(String(f.id))}
              >
                {f.title}
                <span
                  className="filter-tab-x" title="删除这个筛选"
                  onClick={(e) => { e.stopPropagation(); removeFilter(f) }}
                >×</span>
              </button>
            ))}
          </div>
        )}
        <TasksView refreshKey={refreshKey} refresh={refresh} activeFilter={activeFilter} />
      </div>
    </div>
  )
}
