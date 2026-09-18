# 小白 · Personal Context OS — 产品需求文档（PRD）

> 版本：v1.0
> 状态：待确认（确认后进入 M1 开发）
> 前置文档：系统蓝图-调研与规划.md（已批注）

---

## 1. 愿景

**小白**是一个常驻本地的个人智能体工作台。它是用户的外置大脑：所有事情（任务、知识、日程、生活琐事）的唯一权威记录处，让用户可以放心地把事情从脑子里「放下」。

三个月后的成功标准（用户定义）：
1. 脑子比现在更清楚
2. 更有时间管理能力
3. 能更好地管理自己的知识

**一句话定位**：半自动、有时间感知、持续进化的个人 Context 操作系统。

---

## 2. 用户画像与痛点

### 2.1 用户画像
- 在校生，多角色：课程学习、考试作业、社团任务、项目推进、大量协作交流
- 以电脑为主力设备；每天可投入 10–20 分钟维护系统
- 开发者（TRAE 重度用户），参与共同维护，代码进 Git 仓库
- 现有资产：飞书（组织知识库，仅作为输入源、不替代）、浏览器书签、电脑各文件夹课程资料

### 2.2 痛点 → 解决方案映射

| # | 痛点 | 解决方案 | 落点 |
|---|---|---|---|
| P1 | 多任务并行焦虑、脑子过载 | 单一事实来源 + 每日简报 + 温和收束 | M4 |
| P2 | context 同步成本高，他人/AI 无法帮忙 | 项目进展日志 + Context 打包器 | M3 |
| P3 | 收藏多消化少 | Karpathy 式消化入库 + 间隔重现 | M2/M5 |
| P4 | 任务规划乱、顾此失彼 | 简报中的优先级建议 + 收束机制 | M4 |
| P5 | 灵感/口头信息丢失 | 全局快捷键 5 秒速记 → Inbox | M1 |
| P6 | 忘记生活琐事（快递/房间/洗澡） | 时间/事件触发的生活提醒 | M6 |
| P7 | 情绪问题（焦虑/逃避/意义感丧失） | **不做情绪功能**，仅生活琐事提醒（用户选择 B） | — |
| P8 | 资料散落、找东西靠碰运气 | 统一索引（wiki index + SQLite 搜索） | M2 |

---

## 3. 设计哲学（不可违背）

1. **单一事实来源**：任何一件事只有小白这一处权威记录
2. **捕获零摩擦**：任何输入 5 秒内完成，先记下再分类
3. **消化回路闭合**：信息被处理时必须明确裁决——消化进 wiki 或明确放弃；裁决由用户发起，系统不主动催促（2026-09-18 闪记定案）
4. **半自动契约**：小白建议、用户确认；小白永不未经确认执行有后果的操作
5. **做减法**：任务过载时帮用户砍/延/委派，而不是堆更多
6. **时间感知**：跟着用户的一天走（日程表、截止时间），全天候适时辅助，而非只在晚间总结
7. **自进化**：通过每日对话式反馈持续校准自己的建议策略（并行度阈值、优先级判断）
8. **管理边界（铁律）**：小白是管理工具，不是学习者。不主动阅读用户文件的具体学科内容；用户以「有什么东西」的粒度同步即可。课程等临时性内容只做存在索引；书签、技术、架构、项目、科研等**长期资产**才值得消化入库

---

## 4. 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    用户界面（React Web）                   │
│   今日简报 │ Inbox │ 任务板 │ 项目页 │ 知识 wiki 浏览 │ 对话  │
└──────────────────────────┬──────────────────────────────┘
                           │ REST + SSE（小白主动消息推送）
┌──────────────────────────▼──────────────────────────────┐
│                  服务层（FastAPI）                        │
│  捕获服务 │ Inbox 处理 │ 任务/项目服务 │ 简报调度 │ 提醒调度  │
├──────────────────────────────────────────────────────────┤
│                  智能体层（小白核心）                      │
│  · 消化引擎：raw source → wiki 页（Karpathy 模式）          │
│  · 简报引擎：时间感知 + 优先级建议 + 收束建议               │
│  · 反馈引擎：每日对话式反馈 → 结构化信号提取                │
│  · Context 打包器：项目全程记录 → 一键背景摘要             │
│  · Lint 引擎：wiki 健康检查（矛盾/孤儿页/过期信息）          │
├──────────────────────────────────────────────────────────┤
│                  存储层（混合）                           │
│  SQLite：任务/项目/课表/提醒/反馈/建议记录（结构化）         │
│  Markdown 文件库：知识 wiki（LLM 维护，可 git、可 Obsidian） │
└──────────────────────────────────────────────────────────┘
```

### 4.1 知识层：Karpathy Wiki 模式

采用增量维护的持久 wiki，而非传统 RAG：

```
storage/
├─ raw/                  # 原始资料（不可变，source of truth）
│  ├─ inbox/            #   待消化资料落地处
│  └─ assets/           #   图片等附件
├─ wiki/                 # LLM 维护的知识 wiki
│  ├─ schema.md          # 维护手册：结构约定、消化流程、页面规范
│  ├─ index.md           # 内容目录（LLM 每次消化后更新）
│  ├─ log.md             # 时间线（append-only，可 grep）
│  ├─ topics/            # 主题/概念页（互相链接）
│  ├─ entities/          # 实体页：人物/组织/课程/项目
│  └─ sources/           # 每个原始资料对应一个摘要页
└─ xiaobai.db            # SQLite
```

**三个操作**：
- **Ingest（消化）**：新资料 → LLM 阅读 → 与用户讨论要点 → 写摘要页 → 更新 index/受影响的实体与主题页 → 记 log。一次消化可能触碰 10–15 个页面
- **Query（查询）**：问答先走 index 找页再深入；有价值的问答结论回填 wiki 成新页
- **Lint（体检）**：定期健康检查——找矛盾、孤儿页、缺页概念、过期信息

**关键差异**：入库 ≠ 打标签。入库 = 被消化成互相链接的知识页，知识编译一次、持续保鲜。

**消化范围边界**（用户明确）：
- **做消化**：书签、零碎个人学习内容、技术/架构知识、项目与科研经验——长期资产，值得花功夫
- **只做索引**：课程资料等临时性内容（为绩点而学的应付物）——仅登记「有什么、在哪」，用户口述同步即可，小白不读内容

### 4.2 时间感知层

- 内置**日程表**（而非课表）：课程只是学生日程的最大组成部分，本质是「该时段已被约出去」。日程条目类型：课程/会议/外出/个人安排
- 简报引擎结合日程判断用户当前状态（在上课/空闲/晚间），适配提醒时机
- 截止时间感知：任务临近截止时适时提醒，而非固定时刻
- 预留日历（.ics）同步接口，后续升级

### 4.3 文件资产层（本地投放区，2026-09-18 定案）

用户资产以文件为主（word/markdown/pdf 等）。本地部署形态下的文件方案：

**独立投放区模式**：
- 小白有一个专属投放目录（默认 `storage/raw/dropzone/`，路径可配置）
- 用户把想让小白处理的文件丢进去 = 明确表达消化意图；目录外一概不碰（铁律自动安全）
- **内部结构完全由用户自由**，不需要整理——结构由 LLM 在 wiki 层维护，raw 层就是原料堆放区（Karpathy 模式本意：用户只管投放，组织是小白的事）

**实时监听（watchdog）**：
- OS 级文件监听：放入/修改文件后数十秒内，小白在对话中提示「有新东西要处理」
- 感知变更 → **提议**扫描与消化（防污染契约：不自动重构）
- 提议内容为批量消化清单（新增 N / 修改 M），用户确认后统一执行（分钟级 LLM 操作，批量比逐个更合理）

**文件级状态标注**：
- 推广已实现的 wiki 哈希机制：每个投放文件记 `未归档/已归档/有修改`
- 知识库视图增加「原料区」页面：浏览投放区文件树 + 状态标注

**格式支持**：md/txt 直读；docx（python-docx）、pdf（pypdf）提取文本；未知/二进制格式仅登记名称位置不读内容；图片暂不支持

**取消原设计**：「文件上传接口 + 书签导入器」由本方案替代——本地应用直接读文件系统，丢文件即上传

### 4.4 自进化层（对话式反馈）

- 每日晚间小白主动开启一段对话：「今天实际做了什么、感觉如何」
- 用户以**自然语言**回应（不做结构化按钮，尊重用户偏好）
- 反馈引擎从对话中提取结构化信号：实际完成的事、负荷感受（轻松/刚好/过载）、对简报建议的修正意见
- 信号积累进入 `feedback_signals`，简报引擎据此持续校准：并行度预警阈值、优先级判断、建议的激进程度
- 每条小白建议记录用户动作（采纳/修改/拒绝），同样作为校准数据

### 4.5 每日节律

| 时段 | 事件 | 内容 |
|---|---|---|
| 早上开机 | 简报（小白发起） | 今日三件事 + 生活杂务提醒 + 本周回顾 + 并行度预警 |
| 白天 | 适时提醒 | 课间/截止前/速记确认等，基于课表与任务时间触发 |
| 晚间 | 反馈对话（小白发起） | 自然语言复盘当日，提取自进化信号 |
| 每周 | Wiki Lint | 知识库健康检查报告（用户确认后执行修复） |

### 4.6 记忆分层（2026-09-18 定案）

对标 Claude Code / ChatGPT 记忆架构（预计算注入 + 索引常驻 + 按需拉取）分三层：

**存储层：**

| 记忆类型 | 载体 | 写入路径 |
|---|---|---|
| 底座（我是谁、组织性质、战略定位、关键人物名单、长期方向） | `wiki/core.md`，按 `## 小节` 组织 | 对话沉淀：提案 kind=core，同名小节覆盖、新小节追加 |
| 进行中的事（申报、合作洽谈、家庭事务、社团…） | projects 语义升级：category 区分（科研/合作/家庭/个人/社团/其他）+ progress_logs | update 提案挂靠（复用 FR-3.1 机制） |
| 人物/机构细节（老师风格、机构诉求） | `wiki/entities/` 每人/每机构一页，叙事性写法 | 对话沉淀：提案 kind=entity |
| 文件消化知识 | `wiki/sources/`（已有） | 投放区消化（FR-2.3） |

**注入层（每次对话自动组装）：**
1. core.md 全文（约束精炼——文件越长注意力越稀释，业界共识）
2. 进行中的事索引：每件一行「名称 + 类别 + 下一步 + 最近动态」，封顶 30 条
3. 实体索引：每条一行「名字 + 一句话描述」
4. 知识库目录（index.md 条目）
5. **关键词触发**：用户消息提到某项目/人物名 → 自动附加该条的完整进展日志（最近 5 条）或实体页全文（Claude Code 同款模式，无需向量库）

**治理：**
- 所有写入经用户裁决（防污染铁律不变）
- 记忆整理（消解矛盾、防索引膨胀，Auto Dream 式）→ 并入 M5 与 Wiki Lint 一起做

---

## 5. 功能需求（按里程碑）

### M1 基座
- **FR-1.1 闪记**（原「全局速记+Inbox」重设计，2026-09-18 定案）：Ctrl+Shift+X 唤起迷你输入框（窗口级，M7 升系统级托盘），5 秒哑捕获进闪记池，不打扰、不要求分类
- **FR-1.2 闪记处理**：对话式——闪记视图勾选若干条 →「去对话处理」→ 对话流自动发起 → 小白看懂则提案（走提案裁决），看不懂则提问、用户解释；**随叫随到，永不主动催**
- **FR-1.3 任务 CRUD**：任务创建/编辑/完成（完成的任务保留可见，符合用户既有习惯）
- **FR-1.4 应用骨架**：FastAPI + React + SQLite + markdown 目录结构初始化

### M2 收拢与知识层（含前端重构，见 docs/交互设计文档.md）
- **FR-2.0 界面重构**：双页结构——主页面对话流（小白主场）+ 侧导航陈列区（项目全景/时间轴/知识库/生活角）；对话中实体锚点可点击跳转（防幻觉）
- **FR-2.1 闪记对话式分拣**（原「AI 分类建议」并入）：闪记处理时小白在对话中提议分类与去向，经提案裁决入库
- **FR-2.2 消化引擎 Ingest**：确认归入知识的条目走完整 Karpathy 消化流程（摘要页 + index/关联页更新 + log）
- **FR-2.6 记忆同步**：对话流内「回顾对话」按钮 → 批量提案清单（含去向：task/update/knowledge/core/entity，逐条同意/修改/丢弃）→ 一次性入库；小事走即时确认卡。**所有入库必须经过用户裁决（防污染，用户铁律）**
- **FR-2.8 记忆分层**（4.6 节定案）：core.md 底座全文注入；projects 语义升级为「进行中的事」（category 字段）；entities/ 实体页启用；对话关键词触发详情展开
- **FR-2.7 知识库 Git 化**：wiki 条目状态标注（已归档/未归档/有修改），统一整理提案后归档
- **FR-2.3 文件资产层**（4.3 节定案）：本地投放区（dropzone，路径可配置）+ watchdog 实时监听 + 文件级【未归档/已归档/有修改】标注 + 知识库视图「原料区」页面；变更后提议批量消化（不自动执行）；**课程资料只做目录级索引**（用户口述「有什么」，小白登记位置，不读内容）
- **FR-2.4 wiki 浏览**：知识库视图内浏览 wiki 页面、跟随链接、直接编辑
- **FR-2.5 统一搜索**：跨 wiki + 任务的搜索

### M3 项目与 Context 打包
- **FR-3.1 项目进展日志**：每个项目/任务维护结构化日志（背景 → 关键决策 → 当前状态 → 下一步），速记和对话中的相关信息可挂靠
- **FR-3.2 Context 打包器**：任意项目一键生成完整背景摘要（给老师/学长/其他 AI 用）

### M4 节律与时间感知

- **FR-4.1 内置日程表**：日程条目 CRUD（课程/会议/外出/个人安排，课程为其中一类），每学期更新
- **FR-4.2 每日简报**：早间简报（今日三件事/杂务提醒/本周回顾/并行度预警）
- **FR-4.3 适时提醒**：基于日程表与截止时间的白天提醒
- **FR-4.4 反馈对话**：晚间小白发起自然语言复盘，提取信号入库
- **FR-4.5 温和收束**：活跃任务超阈值时建议砍/延/委派（阈值随反馈自校准）

### M5 消化回路强化
- **FR-5.1 间隔重现**：入库知识按间隔重现机制在简报中出现（比 Anki 轻）
- **FR-5.2 收藏坟场清理**：超期未消化内容，小白建议放弃或强制归档

### M6 生活兜底
- **FR-6.1 生活提醒**：一次性（拿快递）与周期性（收拾房间/洗澡打卡）提醒，融入简报与适时提醒

### M7 扩展（视需要）
- 手机端速记入口、浏览器剪藏插件、飞书打通、日历 .ics 同步、Markdown 笔记（课堂笔记）管理

---

## 6. 数据模型初稿（SQLite）

```sql
-- 任务与项目
CREATE TABLE projects (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  category TEXT DEFAULT 'other',      -- research/collaboration/family/personal/club/other（4.6 语义升级：进行中的事）
  status TEXT DEFAULT 'active',      -- active/paused/closed
  created_at TEXT, closed_at TEXT
);

CREATE TABLE tasks (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  project_id INTEGER REFERENCES projects(id),
  status TEXT DEFAULT 'backlog',     -- backlog/active/done/cancelled
  priority INTEGER DEFAULT 3,        -- 1(高)–5(低)
  due_at TEXT,
  estimate TEXT,                      -- 用户/小白对难度的粗估
  created_at TEXT, completed_at TEXT
);

CREATE TABLE progress_logs (          -- 项目/任务进展日志（P2 的核心）
  id INTEGER PRIMARY KEY,
  project_id INTEGER REFERENCES projects(id),
  task_id INTEGER REFERENCES tasks(id),
  kind TEXT,                          -- background/decision/status/next_step/note
  content TEXT NOT NULL,
  source TEXT DEFAULT 'user',         -- user/agent
  created_at TEXT
);

-- 捕获与收件箱
CREATE TABLE inbox_items (
  id INTEGER PRIMARY KEY,
  content TEXT NOT NULL,
  source TEXT DEFAULT 'quick_note',  -- quick_note/bookmark/manual
  status TEXT DEFAULT 'pending',      -- pending/confirmed/discarded
  ai_suggestion TEXT,                 -- JSON：{type, tags, confidence, reason}
  captured_at TEXT, resolved_at TEXT
);

-- 时间感知
CREATE TABLE schedule_slots (         -- 内置日程表（课程/会议/外出/个人安排）
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  entry_type TEXT DEFAULT 'course',  -- course/meeting/outing/personal
  day_of_week INTEGER,               -- 0–6
  start_time TEXT, end_time TEXT,     -- "08:00"
  location TEXT,
  week_pattern TEXT DEFAULT 'all',    -- all/odd/even/单双周或自定义
  valid_from TEXT, valid_to TEXT      -- 有效范围（学期等）
);

CREATE TABLE reminders (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  trigger_type TEXT,                  -- once/daily/weekly/interval
  trigger_value TEXT,                 -- ISO时间或规则
  scope TEXT DEFAULT 'life',          -- life/task
  active INTEGER DEFAULT 1,
  last_fired_at TEXT
);

-- 自进化
CREATE TABLE feedback_sessions (      -- 每晚对话式反馈
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  transcript TEXT,                   -- 完整对话记录
  signals TEXT,                       -- JSON：提取的结构化信号
  created_at TEXT
);

CREATE TABLE agent_suggestions (      -- 小白建议的采纳记录
  id INTEGER PRIMARY KEY,
  date TEXT,
  kind TEXT,                          -- briefing/priority/prune/reminder
  content TEXT,
  user_action TEXT,                   -- accepted/modified/rejected
  created_at TEXT
);
```

> 知识 wiki 不入库表，由文件系统承载（schema.md 约定结构），SQLite 可选维护一张轻量 `wiki_pages` 索引表加速前端列表。

---

## 7. 接口初稿（REST + SSE）

```
# 捕获与收件箱
POST /api/capture              # 速记（{content} → inbox）
GET  /api/inbox                # 待处理列表（含 AI 建议）
POST /api/inbox/{id}/confirm   # 确认归类（可采纳/修改建议）
POST /api/inbox/{id}/discard

# 任务与项目
GET/POST /api/tasks            PATCH/DELETE /api/tasks/{id}
GET/POST /api/projects         GET /api/projects/{id}
GET  /api/projects/{id}/context-pack   # Context 打包器
POST /api/projects/{id}/logs   # 追加进展日志

# 知识 wiki
POST /api/wiki/ingest/{inbox_item_id}  # 触发消化流程
GET  /api/wiki/pages           GET /api/wiki/pages/{path}
POST /api/wiki/query            # 问答（结论可回填）
POST /api/wiki/lint             # 触发健康检查
GET  /api/search?q=             # 全局搜索（wiki+任务）

# 时间与简报
GET/POST /api/schedule          PATCH/DELETE /api/schedule/{id}
GET/POST /api/reminders
GET  /api/briefing              # 当日简报（生成+缓存）

# 反馈与对话
POST /api/feedback/start        # 发起晚间反馈对话
POST /api/feedback/{id}/reply   # 用户回复（SSE 流式返回小白回应）
POST /api/chat                  # 自由对话（入口统一）

# 小白主动消息
GET  /api/events                # SSE：适时提醒、简报就绪、消化完成通知
```

---

## 8. 技术栈（已确认）

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | Python FastAPI | agent/LLM 生态成熟 |
| 前端 | React（Vite） | 极简 UI 风格（用户偏好） |
| 结构化存储 | SQLite | 单文件、可迁移 |
| 知识存储 | Markdown 文件库 | LLM 维护、git 版本化、可 Obsidian 浏览 |
| LLM | DeepSeek / GLM API | 云端，中文强、便宜（具体选哪家：待用户提供 key 时定） |
| 主动消息 | SSE | 小白主动推送 |
| 部署 | 本地常驻服务 + 开机自启 | 电脑为主场景 |
| 版本管理 | Git 仓库（用户共建） | 待用户提供仓库信息 |

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| LLM 消化质量不稳，wiki 越维护越乱 | schema.md 从严约定；Lint 定期体检；消化过程用户在场确认（半自动契约） |
| 每日 10–20 分钟维护预算被超支 | 所有 AI 流程可一键跳过；速记零摩擦设计；攒批处理 |
| 自进化信号噪声大 | 信号只做趋势参考，阈值调整需用户在反馈对话中确认 |
| 存量资料导入工作量失控 | 分批消化：书签先行，课程资料按目录分批，每批用户验收 |
| 半自动变全自动的信任滑坡 | agent_suggestions 全量留痕，前端可审计小白做过什么 |

---

## 10. 事项清单（已确认/待办）

- [x] **GitHub**：github.com/ZJR69，用户授权现阶段全权操作入库
- [x] **LLM**：DeepSeek（API key 用户稍后提供，本地配置文件，不入库）
- [x] **存量迁移策略**（用户修正）：长期资产（书签/技术/架构/项目）优先消化；课程资料仅目录级索引，不读内容
- [x] **课表 → 日程表**：已修正，课程只是日程的一类
- [x] **小白人设**：已定（见交互设计文档第 3 节：不预设性别，聪明，正常大模型默认语气，不做角色扮演）✅ 2026-09-18 结案
- [ ] **课程资料索引登记**：M2 期间用户提供「有什么」清单
- [ ] **GitHub push**：仓库 github.com/ZJR69/XiaoBai 已建，本机直连 443 被阻断，待用户提供代理环境后推送

---

## 11. 下一步

1. 用户确认本 PRD（尤其第 10 节开放事项）
2. 初始化 Git 仓库 + 项目骨架（FastAPI + React + 存储目录结构）
3. 开始 M1 开发：全局速记 → Inbox 手动模式 → 任务 CRUD
