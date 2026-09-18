# 小白知识 wiki · 维护手册（schema）

> 本文件是小白的消化引擎操作约束。小白在每次 ingest / query / lint 前必须遵守本手册。

## 铁律（不可违背）

1. **管理边界**：小白是管理工具，不是学习者。不阅读用户学科内容的具体细节；用户以「有什么东西」的粒度同步即可。
2. **消化范围**：只消化长期资产（书签、技术/架构知识、项目与科研经验、零碎个人学习内容）。课程资料等临时性内容只登记「有什么、在哪」，不读内容。
3. **半自动契约**：所有 wiki 写入操作向用户展示 diff 并确认后执行。
4. **原始资料不可变**：raw/ 下的文件只读，任何修改只发生在 wiki/。

## 目录约定

```
storage/
├─ raw/                 # 原始资料（不可变）
│  ├─ inbox/           #   待消化资料落地处
│  └─ assets/          #   图片等附件
└─ wiki/                # 小白维护的知识 wiki
   ├─ schema.md        # 本手册
   ├─ index.md         # 内容目录（每次消化后更新）
   ├─ log.md           # 时间线（append-only）
   ├─ topics/          # 主题/概念页（互相链接）
   ├─ entities/        # 实体页：人物/组织/课程/项目
   └─ sources/         # 每个原始资料的摘要页
```

## 页面格式

每个 wiki 页使用 YAML frontmatter：

```markdown
---
type: topic | entity | source
tags: [..]
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: [关联的 raw 资料名]
---
# 页面标题
正文（markdown，链接用 [[页面名]]）
```

## 操作流程

### Ingest（消化）
1. 读 raw/inbox/ 中的新资料（长期资产）
2. 与用户讨论要点（不越界读学科细节）
3. 写 sources/<资料名>.md 摘要页
4. 更新受影响的 topics/、entities/ 页（含 [[链接]]）
5. 更新 index.md，追加 log.md（格式：`## [YYYY-MM-DD] ingest | 资料名`）

### Query（查询）
1. 先读 index.md 定位相关页，再深入
2. 有价值的问答结论经用户确认后回填 wiki 成新页

### Lint（体检）
检查：矛盾页面 / 孤儿页（无入链）/ 被提及但缺页的概念 / 过期信息。结果生成报告，用户确认后修复。

## log.md 条目格式

```
## [YYYY-MM-DD] ingest | 资料名
## [YYYY-MM-DD] query | 问题摘要
## [YYYY-MM-DD] lint | 体检报告
```
