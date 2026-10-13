// 草稿本（闪记并入对话页）：输入区上方的速记堆。
// 场景：时间紧、无决策带宽时秒记（老师在讲、疑点一闪而过），有空时对小白说「处理草稿」走提案闭环；
// 单条也可直接「转待办」（进任务池无日期）或「丢弃」。
export default function DraftStrip({ drafts, onToTask, onDrop }) {
  if (!drafts.length) return null
  return (
    <div className="draft-strip">
      {drafts.map((d) => (
        <div key={d.id} className="draft-item">
          <span className="draft-content">{d.content}</span>
          <span className="draft-time">{d.captured_at?.slice(5, 16).replace('T', ' ')}</span>
          <span className="draft-ops">
            <button className="small-btn" onClick={() => onToTask(d)} title="直接进任务池（无日期），不经过对话">
              转待办
            </button>
            <button className="small-btn" onClick={() => onDrop(d)} title="这条不要了">
              丢弃
            </button>
          </span>
        </div>
      ))}
    </div>
  )
}
