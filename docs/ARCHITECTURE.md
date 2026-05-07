# itsme — Architecture

> Status: **Design draft** · v0.0.x
> Repo: <https://github.com/coxon/itsme>
> Language: **Python**
> Last updated: 2026-05-07

---

## 1. Vision

**itsme** 是一个为 agent IDE（Claude Code / Codex 等）提供"长期记忆"的 plugin。

它对外只暴露一个**窄接口**（3 个 MCP 动词 + 1 套 skill + 几个 hook），让 agent 可以：

- 自动 / 显式地**记下**有价值的事
- 在需要时**问回**自己过去的经验
- **观察**自己最近在想什么、做什么

它对内由两套引擎驱动：**MemPalace** 负责存所有原料，**Aleph** 负责把原料提炼成可读的 wiki，落到独立的 Obsidian Vault。

---

## 2. Mental Model — 双引擎记忆

借用人脑的两阶段记忆模型：

```
MemPalace (海马体)              Aleph (新皮层)
─────────────────                ──────────────
verbatim · 全量 · 短链            结构化 · 提炼 · 长链
"发生了什么 / 原话是什么"          "我是谁 / 我学到了什么"
原料 · 高召回搜索面                成品 · 高精度搜索面
高频写、低频读                    低频写、高频读
```

- **MemPalace** 是大脑：来什么记什么，verbatim + KG。**raw 全文是一等搜索面**。
- **Aleph** 是记忆管理员：定期把原料**抽取 / 结构化 / 去重 / 交叉链接 / 更新**，输出 wiki 风格的条目。

> **ask 搜索策略**：双引擎并行查询 → 合并去重 → 返回。Aleph 结构化层提供高精度命中；MemPalace raw 层提供高召回兜底。**LLM 提取遗漏时 MemPalace raw 兜底，永远不丢。**

> **Aleph 渐进式落地**：v0.0.2 只做 per-turn extraction index（sqlite + FTS5，无 wiki / vault）；v0.0.3 升级到 wiki consolidation + promoter + vault。

> 关键认知：**chat 是 I/O，wiki 才是 memory**。MemPalace 是日志，Aleph 才是记忆本身。

---

## 3. System Boundary

```
┌────────────────────────────────────────────────────────────┐
│  AGENT LAYER  (CC / Codex / 任意 IDE)                      │
│  ─────────────                                             │
│   skill: itsme.skill.md  ← 教 agent 怎么用                 │
│   hook : before-exit / before-clear / before-compact       │
│          ↑ 上下文收缩前的"抢救/固化"时刻                    │
└────────────────────────┬───────────────────────────────────┘
                         │ 3 MCP verbs
                         ▼
╔════════════════════════════════════════════════════════════╗
║                      🟦 itsme  (plugin)                    ║
║  ┌──────────────────────────────────────────────────────┐  ║
║  │  MCP SURFACE  (对 agent 可见)                        │  ║
║  │   remember(content, kind?)                           │  ║
║  │   ask(q, mode?, promote?)                            │  ║
║  │   status(scope?, format?)                            │  ║
║  └────────────────────┬─────────────────────────────────┘  ║
║                       │                                    ║
║  ┌────────────────────▼─────────────────────────────────┐  ║
║  │  EVENT BUS (sqlite ring · 单一真实流)                │  ║
║  └──┬─────────┬─────────┬─────────┬───────────────────┬─┘  ║
║     ▼         ▼         ▼         ▼                   │    ║
║  router   promoter   curator   reader                 │    ║
║     │         │         │         │                   │    ║
║     ▼         ▼         ▼         ▼                   │    ║
║  ┌──────────────┐ ┌──────────────┐                    │    ║
║  │  MemPalace   │ │    Aleph     │                    │    ║
║  │  (海马体)    │ │  (新皮层)    │                    │    ║
║  │              │ │   ↓          │                    │    ║
║  │              │ │  Obsidian    │                    │    ║
║  │              │ │  Vault       │                    │    ║
║  └──────────────┘ └──────────────┘                    │    ║
║     ↑                  ↑                              │    ║
║     └─── 内部 SDK ─────┴──────────────────────────────┘    ║
║         （对 agent 不可见）                                ║
╚════════════════════════════════════════════════════════════╝
```

**边界铁律：**

- agent **只看见** MCP 三个动词 + skill + status feed
- MemPalace / Aleph 的工具与 schema 对 agent **完全隐藏**
- 所有跨组件通信经 EventBus，不允许 worker 之间直接耦合

---

## 4. MCP Surface — 3 个动词

### 4.1 `remember(content, kind?, links?)`

写入一段记忆。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `content` | string | ✓ | 记忆正文，verbatim |
| `kind` | enum? | ✗ | `decision` / `fact` / `feeling` / `todo` / `event`，提示 router 走 fast-path |
| `links` | string[] | ✗ | 关联的 event id / drawer id |

**返回**：`{ id, routed_to: ["mempalace:<drawer>"], event_id }`

> `kind` 是**可选提示信号**：传了 → router 用规则路由；不传 → router 调 LLM 推断。skill 文档不强调，只给老司机用。

### 4.2 `ask(question, mode?, promote?)`

提问。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `question` | string | — | 自然语言提问 |
| `mode` | enum? | `auto` | `auto` / `wiki` / `verbatim` / `now` |
| `promote` | bool? | `false` | **是否触发融合升格** |

**模式说明：**

- `auto`：先查 Aleph，命中即返；不命中回退 MemPalace
- `wiki`：只查 Aleph
- `verbatim`：只查 MemPalace（要原话）
- `now`：聚合最近 events（"我最近在忙什么"）

**`promote=true` 的语义（核心新增）：**

> 把 ask 从 *read-only* 升级为 *read + reconsolidate*。这与人脑的"记忆唤起即重新巩固（reconsolidation）"同构 —— **每一次提问都是一次记忆的重新组织**。

**调用是同步的**：调用方等待并行检索 + 融合完成，拿到融合后的答案；副作用是 emit `wiki.promoted` 让 promoter 写回 Aleph。

流程：
1. **并行**抓 MemPalace 相关 verbatim 命中 + Aleph 现有 wiki 条目
2. LLM 融合：`(老 wiki) + (新原料) + (本次问题视角) → 新 wiki`
3. 返回融合后的答案给调用方
4. emit `wiki.promoted` 事件 → Aleph 写回 vault（写回失败不影响已返回的答案）

```
ask(q, promote=true) 时序

  MCP ──► reader ──► MemPalace.search(q)  ──► raw_hits
              │  └─► Aleph.search(q)      ──► wiki_entry
              │
              ▼ LLM.fuse(wiki_entry, raw_hits, q)
              │
              ├──► return fused_answer
              └──► emit wiki.promoted ──► promoter ──► Aleph.write
```

**返回**：
```json
{
  "answer": "...",
  "sources": [
    { "kind": "wiki",     "ref": "aleph:notes/decisions/x.md" },
    { "kind": "verbatim", "ref": "mempalace:drawer_xyz" }
  ],
  "promoted": true,
  "promotion_event_id": "evt_..."
}
```

### 4.3 `status(scope?, format?)`

观察自己最近的活动流。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `scope` | enum? | `recent` | `recent` / `today` / `session` |
| `format` | enum? | `json` | `json`（机读）/ `feed`（人读） |

**返回**：events 列表（json）或渲染后的 feed 文本。

---

## 5. EventBus — 单一中枢

所有动作都先成事件，再被 worker 消费。事件即真相。

### 5.1 Envelope schema

```python
{
  "id":      "evt_01HW...",          # ULID
  "ts":      1714521600,             # unix ms
  "type":    "raw.captured",         # 见 5.2
  "source":  "hook" | "explicit"
             | "worker:router"
             | "worker:promoter"
             | "worker:curator"
             | "worker:reader",
  "payload": { ... },                # type-specific
  "refs":    ["mp:drawer_xxx",
              "aleph:note_yyy",
              "evt_zzz"]             # 关联指针
}
```

### 5.2 事件类型（窄而稳）

| type | 谁发 | 含义 |
|---|---|---|
| `raw.captured` | hook / MCP | 原始输入进 bus |
| `memory.stored` | router | 已写入 MemPalace |
| `memory.routed` | router | 路由决策记录 |
| `wiki.promoted` | promoter | Aleph 已写入新条目 |
| `memory.curated` | curator | 去重 / 失效完成 |
| `memory.queried` | reader | 一次 ask 调用 |

> **铁律**：type 保持窄、扩展走 payload。任何新需求**先尝试塞进现有 type 的 payload**，加新 type 要谨慎。

### 5.3 持久化

- **sqlite ring buffer**，最近 N=500 条（可配）
- 落盘但不保证永久 —— 长期记忆是 MemPalace + Aleph 的事
- 用途：status feed、failure replay、observability

---

## 6. Workers — 4 个独立小脑

| worker | 输入事件 | 输出 | 核心职责 |
|---|---|---|---|
| **router** | `raw.captured` | `memory.stored` | 决定写入哪个 wing/room，调 MemPalace |
| **intake** | `raw.captured` (hook) | `memory.stored` + `extraction.indexed` | structural strip → turn slice → LLM 提取 → 双写 MemPalace (raw) + Aleph index (structured)。v0.0.2 新增 |
| **promoter** | `memory.stored`(批) / `wiki.promoted`(显式) | `wiki.promoted` | raw → 结构化 wiki，写 Aleph + Obsidian（v0.0.3） |
| **curator** | 定时 / `memory.stored` | `memory.curated` | 去重、KG 失效（valid_from / ended） |
| **reader** | `ask` 调用 | `memory.queried` | 路由查询、双引擎合并、必要时 LLM 融合 |

### 6.1 router 路由策略

- `kind` 已传 → 规则路由（fast-path）
- 未传 → LLM 推断 wing/room
- 路由结果写回 events，可观察、可调试

### 6.2 promoter 触发时机

**绑在上下文收缩边界上**（符合人脑睡眠固化的隐喻 —— 当工作记忆要被丢弃前，把它们固化进长期记忆）：

- `before-compact` hook → 上下文压缩前抢救
- `before-clear` hook → 用户清空对话前抢救
- `before-exit` hook → 退出 session 前最终固化
- `ask(promote=true)` → 即时融合（点对点，仅更新相关条目）

> v0.0.1 不实现 promoter，先让 ask 直查 MP；v0.0.2 加入。

**为什么不在每个 turn 都触发？**

per-turn 捕获会让 router/promoter 噪音爆炸。consolidation hook 只在**记忆即将丢失时**抢救，与人类睡眠期记忆固化机制同构。日常 turn 内的"我现在想记一个东西"由 agent 显式 `remember()` 处理。

### 6.3 curator 失效逻辑

- 重依赖 MemPalace KG 的 `valid_from` / `ended` 字段
- curator 是**策略层**：识别"新事实覆盖旧事实"的模式 → 调 KG.invalidate

### 6.4 reader 模式分流

```
ask(q, mode, promote)
  ├─ mode=now      → events.tail + 聚合 (v0.0.3+)
  ├─ mode=verbatim → MemPalace only
  ├─ mode=wiki     → Aleph wiki only (v0.0.3+)
  └─ mode=auto     → dual_search(Aleph extraction + MemPalace raw)
       ├─ Aleph hits first (high precision, LLM-extracted summaries)
       ├─ MemPalace gap-fills (high recall, raw text)
       ├─ dedup by drawer_id (same turn → Aleph wins)
       └─ promote=true → BOTH → LLM.fuse → write back (v0.0.3+)
```

---

## 7. Adapters — 与外部引擎的握手

### 7.1 MemPalace Adapter

包装 MemPalace MCP 工具集（add_drawer / search / kg_add / kg_query / ...）。

- 暴露给内部的 SDK：`mp.write(envelope)`、`mp.search(q)`、`mp.kg.*`
- agent 永远调不到 MemPalace 工具

### 7.2 Aleph Subsystem (built from scratch)

Aleph 不是外部服务，而是 itsme 进程内的一个**模块**（`core/aleph/`）。它把"记忆管理员"的标准流程拆成可观察的 pipeline，最终把成品落到 Obsidian Vault。

#### 7.2.1 内部模块

```
core/aleph/
├── api.py              # 对内 SDK：write / search / get / update
├── pipeline/
│   ├── extract.py      # raw → 实体/事实/关系
│   ├── route.py        # 这条 raw 该并入哪个 entry（新建 or 更新）
│   ├── merge.py        # 把新信息融进老 entry
│   ├── crosslink.py    # 插入 [[wikilink]]
│   └── refresh.py      # 去重、清理冗余段落
├── store/
│   ├── vault.py        # 读写 Obsidian .md 文件（含 frontmatter 解析）
│   └── index.py        # sqlite 索引（标题→路径、embedding、ref 倒排）
├── search.py           # 混合检索：embedding + 关键词
├── prompts/            # LLM prompt 模板
│   ├── extract.md
│   ├── merge.md
│   └── fuse.md         # ask(promote=true) 用
└── types.py            # WikiEntry / Claim / Reference 等数据类
```

#### 7.2.2 Wiki Entry 数据模型

每个 entry 是一个 Markdown 文件，frontmatter + body：

```yaml
---
id: alf_01HW...
title: "决定用 Python 实现 itsme"
type: decision           # person | project | decision | concept | place | event
created: 2026-04-30
updated: 2026-04-30
refs:                    # 来源指针
  - mp:drawer_xxx
  - mp:drawer_yyy
links:                   # 双向链接
  - "[[itsme]]"
  - "[[MemPalace]]"
tags: [python, plugin]
confidence: 0.92
---

## 摘要
一句话精炼。

## 上下文
为什么会发生这个决定。

## 关键事实
- ...
- ...

## 关联
- 相关决定：[[xxx]]
- 反对意见：[[yyy]]
```

#### 7.2.3 Pipeline 数据流

**写入路径（promoter 调用 `aleph.write(raw_batch)`）：**

```
raw_batch (来自 MemPalace)
    │
    ▼
extract  ── LLM ──► claims[] · entities[] · relations[]
    │
    ▼
route    ── 索引匹配 + LLM ──► target_entry (new or existing)
    │
    ├── new      ──► 生成 entry skeleton + body
    └── existing ──► merge ──► 整合到既有 entry
                       │
                       ▼
                   crosslink ──► 插入 [[wikilink]]
                       │
                       ▼
                   refresh   ──► 去重 / 清冗余
                       │
                       ▼
                  store.vault.write(.md)
                  store.index.update()
                       │
                       ▼
                  emit wiki.promoted
```

**查询路径（reader 调用 `aleph.search(q)`）：**

```
q ──► search.hybrid(q)
        ├─ keyword(q) over titles + tags
        └─ embedding(q) over body chunks
              │
              ▼
        ranked entries[]
              │
              ▼
        return [{entry, snippet, score}]
```

#### 7.2.4 与 MemPalace 的解耦

Aleph **从不直接依赖 MemPalace**：
- promoter 把 raw 数据**复制**给 Aleph，Aleph 只关心 raw 内容 + ref 指针
- ref 字段记录来源（`mp:drawer_xxx`），但 Aleph 不调 MP API
- 即使 MP 整体下线，Aleph 仍可独立运转、独立查询

#### 7.2.5 LLM 调用边界

Aleph 内部需要 LLM 的环节：
- `extract`：raw 文本 → 结构化 claims
- `route` 的歧义场景：索引匹配不确定时
- `merge`：新旧信息融合
- `fuse`（ask promote 专用）：老 wiki + 新 raw + 提问视角 → 新 wiki

**Provider 抽象**（`core/llm.py`）：单一接口，支持 Anthropic / OpenAI / 本地 Ollama 切换（Open Q4）。

#### 7.2.6 Vault 布局

```
aleph-vault/
├── _index.md           # 自动生成的总览
├── people/
├── projects/
├── decisions/
├── concepts/
├── places/
└── events/
```

- 文件名规则：`<slug-from-title>.md`，slug 冲突时追加 `-2` / `-3`
- Aleph **独占写**；用户在 vault 里只读 + 自己加 `## 用户笔记` 区块（v0.0.4 引入契约）

---

## 8. Key Flows

### 8.1 Capture flow（hook 抢救捕获）

```
user 触发 /clear   ┐
user 触发 /exit    ├─► 上下文即将丢失
context 满了要 compact ┘
              │
              ▼
hook 触发（before-clear / before-exit / before-compact）
              │
              ▼
  收集即将丢失的 turn / context 内容
              │
              ▼
  ① Structural strip（regex 去 CC envelope / boilerplate）
              │
              ▼
  ② Turn slice（按 user/assistant turn 切成多条）
              │
              ▼
  per-turn events.append(raw.captured, source="hook:before-<x>")
              │
              ▼
        intake worker consumes (async)
              │
              ├── ③ LLM intake (Haiku): 每 turn → keep/skip + {summary, entities, claims}
              │
              ├── ALL turns → MemPalace.add_drawer(raw_text)（全量入库，不筛）
              │                 events.append(memory.stored)
              │
              ├── KEEP turns → Aleph.write_extraction(summary, entities, claims)
              │                 events.append(extraction.indexed)
              │
              └── SKIP turns → events.append(raw.triaged, reason="low_info")（可观察性）
              │
              ▼
（如果是 before-exit / before-compact，v0.0.3+）
events.append(consolidation.requested)
              │
              ▼
        promoter consumes ──► Aleph.consolidate → wiki entry → vault（v0.0.3）
```

**核心**：hook 是**抢救机制**，不是常规 logging。日常对话不进 hook，只有"上下文要被丢弃"时 hook 才出手。这样：
- 噪音降到最低
- 与人脑睡眠固化机制同构
- 每条进 MP 的记忆都"有理由进来"

### 8.2 Capture flow（agent 显式）

```
agent calls remember("...", kind="decision")
  │
  ▼
events.append(raw.captured, source=explicit, kind=decision)
  │
  ▼
router (fast-path, kind 已知)
  │
  ▼
MemPalace.add_drawer(...)
```

### 8.3 显式 vs hook 去重

- hook envelope 标 `source=hook`
- explicit 标 `source=explicit`
- 同 turn 内，router 见到 explicit 后，把同段内容的 hook envelope 标 `superseded`

### 8.4 Query flow（普通 ask）

```
ask("X 是怎么决定的", mode=auto)          ← v0.0.2 default
  │
  ▼
reader.dual_search()
  ├─► Aleph.search("X")      ──► structured hits（summary/entity/claim 匹配，高精度）
  └─► MemPalace.search("X")  ──► raw hits（全文/embedding 匹配，高召回）
  │
  ▼
merge + dedup（同 turn_id 的结果合并，Aleph 命中排前）
  │
  ▼
emit memory.queried
  │
  ▼
return merged results
```

> **双引擎保障**：Aleph 提供精准命中（entity "Postgres" 直接匹配）；MemPalace 提供兜底召回（LLM 未提取的 "西雅图出差" 仍在 raw 里命中）。永远不漏。

### 8.5 Query + promote flow

```
ask("X 是怎么决定的", promote=true)
  │
  ▼
reader.fetch_both()
  ├─► Aleph.search("X")       → wiki_entry (老条目)
  └─► MemPalace.search("X")   → raw_hits (原料)
  │
  ▼
LLM.fuse(wiki_entry, raw_hits, q)  → fused_answer
  │
  ├─► return fused_answer to caller
  └─► emit wiki.promoted
        │
        ▼
       promoter ──► Aleph.write(updated_entry)
                     │
                     ▼
                Obsidian vault file 更新
```

**含义**：高频被问到的话题，自动获得更精炼的 wiki 条目。**use creates structure**。

### 8.6 Promotion flow（consolidation 边界批量）

```
hook before-exit / before-compact / before-clear
  │
  ▼
events.append(consolidation.requested, scope=<context|session>)
  │
  ▼
promoter
  ├─ 拉 scope 内所有 memory.stored
  ├─ 主题聚类（按 wing/room + LLM 辅助）
  └─ 逐话题调 LLM 生成 / 更新 wiki 条目
       │
       ▼
      Aleph.write × N
       │
       ▼
      events.append(wiki.promoted) × N
```

**before-compact 与 before-exit 的差异**：
- `before-compact`：上下文要被压缩，promoter 抢救即将丢失的细节
- `before-exit`：用户离场，promoter 做完整固化，可以慢一点也可以更彻底
- `before-clear`：用户主动清空，更激进的固化（用户明显不打算继续）

---

## 9. Plugin Layout

采用 **src-layout**：所有 Python 代码归属单一顶级包 `itsme`，避免与 MCP SDK
（`mcp` 包名）冲突。非 Python 资源（skills / hooks / config / docs / tests）
留在仓库根，跟 `src/` 平级。

```
itsme/                            # git repo root
├── .claude-plugin/
│   └── plugin.json               # CC plugin 注册（mcpServers + skills）
│
├── src/
│   └── itsme/                    # ← 唯一的 Python 包根
│       ├── __init__.py
│       ├── mcp/                  # 对外 3 个 verb（itsme.mcp.*）
│       │   ├── server.py
│       │   └── tools/
│       │       ├── remember.py
│       │       ├── ask.py
│       │       └── status.py
│       └── core/                 # 内部引擎（agent 不可见）
│           ├── events/
│           │   ├── schema.py
│           │   └── ringbuf.py
│           ├── workers/
│           │   ├── router.py
│           │   ├── promoter.py
│           │   ├── curator.py
│           │   └── reader.py
│           ├── adapters/
│           │   └── mempalace.py  # 包装 MemPalace MCP
│           ├── aleph/            # ← 自建，见 §7.2.1
│           │   ├── api.py
│           │   ├── pipeline/
│           │   ├── store/
│           │   ├── search.py
│           │   ├── prompts/
│           │   └── types.py
│           └── llm.py            # LLM provider 抽象
│
├── skills/                       # 给 agent 的剧本（markdown，非代码）
│   └── itsme/
│       └── SKILL.md
│
├── hooks/                        # 各 IDE 的 hook 适配脚本
│   ├── cc/                       # Claude Code
│   │   ├── before-exit
│   │   ├── before-clear
│   │   └── before-compact
│   └── codex/                    # Codex（hook 名按其规范映射）
│       └── ...
│
├── config/
│   └── default.toml              # vault 路径 / 阈值 / 触发策略
│
├── docs/
│   ├── ARCHITECTURE.md           # 本文
│   └── ROADMAP.md                # 任务分解
│
├── tests/                        # pytest
├── pyproject.toml                # hatchling 构建，packages = ["src/itsme"]
└── README.md
```

**为什么 src-layout：**
- `mcp` 是 [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) 的顶级包名。如果我们仓库根直接放 `mcp/`，运行时会 shadow 掉 SDK，本地 `import mcp.server.fastmcp` 拿不到 SDK。
- src-layout 让 `itsme.mcp.*`（我们）和 `mcp.*`（SDK）命名空间完全隔离。
- 同时强制开发者必须 `pip install -e .` 才能 import，避免 "在 repo 根跑 pytest 隐式用上未发布代码" 的常见坑。

---

## 10. Design Decisions / Trade-offs

| # | 决策 | 取舍 |
|---|---|---|
| D1 | **窄 MCP**（3 动词）而非全开放 MemPalace | 心智简单 vs 灵活性损失 |
| D2 | **Event-sourced**（一切经 EventBus） | 解耦 / 可观察 vs 多一跳延迟 |
| D3 | **Aleph 独立 vault** | 零冲突 vs 用户两个 vault |
| D4 | **Aleph 独占写**（用户只读 + 区块评论） | 简单 vs 用户失去手动整理权 |
| D5 | **promoter 绑 consolidation hook**（before-exit/clear/compact） | 噪音低、与睡眠固化同构 vs 实时性差 |
| D6 | **`ask(promote)` 反向触发 + 同步返回** | 高频话题自动精炼；调用方拿到融合答案 vs 调用阻塞稍久 |
| D7 | **kind 可选** | 灵活 vs router 实现复杂（双路径） |
| D8 | **Aleph 进程内模块**（非独立服务） | 部署简单、零跨进程开销 vs 与 itsme 强耦合 |
| D9 | **Python 全栈** | 与 MemPalace 同语言、生态成熟 vs CC plugin TS 生态对接需 bridge |
| D10 | **不做 per-turn hook**，只做 consolidation hook | 噪音低 vs 漏掉一些"想记没记"的瞬间（由 explicit remember 兜底） |
| D11 | **ask 双引擎并行**（Aleph structured + MemPalace raw）→ 合并 | 高精度 + 高召回 vs 合并逻辑复杂 |
| D12 | **Intake LLM (Haiku) 跑在 router async loop**，不在 hook 进程里 | 不阻塞 hook 超时 vs intake 有延迟 |
| D13 | **explicit `remember()` 不走 intake**（直存 MemPalace） | fast-path 保持同步 vs explicit 写入没有结构化搜索面 |
| D14 | **Aleph v0.0.2 = extraction index**（sqlite + FTS5），v0.0.3 升 wiki | 快速落地 + 渐进增强 vs 两步迁移成本 |
| D15 | **MemPalace 全量存 raw**（包括 LLM 判断为 skip 的 turn） | 永远不丢 vs 搜索有噪音（Aleph 层过滤） |

---

## 11. Out of Scope (v0.x)

- 多用户 / 多 agent 隔离
- 加密 / 隐私分级
- 跨设备同步（依赖 vault 自身机制即可）
- 主动总结 / 推荐（"你今天该回顾 X"）
- UI / Web 界面

---

## 12. Open Questions

已解决（移出列表）：
- ~~Q: Aleph 现状~~ → **从 0 自建（进程内模块）**
- ~~Q: 实现语言~~ → **Python**
- ~~Q: `ask(promote=true)` sync vs async~~ → **同步**：并行 fetch + 融合 + 返回，副作用 emit `wiki.promoted`
- ~~Q: LLM provider~~ → **v0.0.x 先支持 Anthropic**，provider 抽象层为未来留口
- ~~Q: Embedding provider~~ → **本地 sentence-transformers**
- ~~Q: Session 边界识别~~ → **before-exit / before-clear / before-compact**
- ~~Q: Vault 默认路径~~ → **`~/Documents/itsme/`**（与现有 `~/Documents/Aleph/` 同级，见 ROADMAP Q7）

待定：

1. **Wiki entry 类型集**（person/project/decision/concept/place/event）是否够用，是否需要预留扩展机制
2. **Aleph confidence 字段**的来源：LLM 自评 vs 来源数量启发式 vs 完全省略
3. **Codex 的 hook 名映射**：CC 是 `before-*`，Codex 对应叫什么？需调研
4. **Plugin 安装契约**：CC 用 `plugin install`，Codex 安装方式是什么？是否需要打成两个 artifact？

