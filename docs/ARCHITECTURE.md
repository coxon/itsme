# itsme — Architecture

> Status: **v0.0.4** · production-ready for Claude Code
> Repo: <https://github.com/coxon/itsme>
> Language: **Python 3.12+**
> Last updated: 2026-05-08

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

- **MemPalace** 是大脑：来什么记什么，verbatim。**raw 全文是一等搜索面**。
- **Aleph** 是记忆管理员：定期把原料**抽取 / 结构化 / 去重 / 交叉链接 / 更新**，输出 wiki 风格的条目。

> **ask 搜索策略**：三条腿并行查询 → 合并去重 → 返回。
> ① Aleph keyword（高精度）→ ② Aleph embedding via MemPalace（语义兜底）→ ③ MemPalace raw（高召回兜底）。

> 关键认知：**chat 是 I/O，wiki 才是 memory**。MemPalace 是日志，Aleph 才是记忆本身。

---

## 3. System Boundary

```
┌────────────────────────────────────────────────────────────┐
│  AGENT LAYER  (CC / Codex / 任意 IDE)                      │
│  ─────────────                                             │
│   skill: itsme.skill.md  ← 教 agent 怎么用                 │
│   hook : SessionEnd / PreCompact                           │
│          ↑ 上下文收缩前的"抢救/固化"时刻                    │
└────────────────────────┬───────────────────────────────────┘
                         │ 3 MCP verbs
                         ▼
╔════════════════════════════════════════════════════════════╗
║                      🟦 itsme  (plugin)                    ║
║  ┌──────────────────────────────────────────────────────┐  ║
║  │  MCP SURFACE  (对 agent 可见)                        │  ║
║  │   remember(content, kind?)                           │  ║
║  │   ask(q, mode?)                                      │  ║
║  │   status(scope?, format?)                            │  ║
║  └────────────────────┬─────────────────────────────────┘  ║
║                       │                                    ║
║  ┌────────────────────▼─────────────────────────────────┐  ║
║  │  EVENT BUS (sqlite ring · 单一真实流)                │  ║
║  └──┬─────────┬─────────────────────────────────────────┘  ║
║     ▼         ▼                                            ║
║  router    intake                                          ║
║  (sync)    (async)                                         ║
║     │         │                                            ║
║     ▼         ├──────────────────┐                         ║
║  MemPalace    ▼                  ▼                         ║
║  (raw)     MemPalace          Aleph                        ║
║            (raw + wiki embed)  (wiki)                      ║
║               │                  │                         ║
║               │                  ▼                         ║
║               │              Obsidian                      ║
║               │              Vault                         ║
║               └──────────────────┘                         ║
║           (wiki pages → MemPalace embedding)               ║
╚════════════════════════════════════════════════════════════╝
```

**边界铁律：**

- agent **只看见** MCP 三个动词 + skill + status feed
- MemPalace / Aleph 的工具与 schema 对 agent **完全隐藏**
- 所有跨组件通信经 EventBus，不允许 worker 之间直接耦合

---

## 4. MCP Surface — 3 个动词

### 4.1 `remember(content, kind?)`

写入一段记忆。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `content` | string | ✓ | 记忆正文，verbatim |
| `kind` | enum? | ✗ | `decision` / `fact` / `feeling` / `todo` / `event`，提示 router 走 fast-path |

**返回**：`{ id, drawer_id, wing, room, routed_to, stored_event_id }`

> `kind` 是**可选提示信号**：传了 → router 用规则路由到对应 room；不传 → router 用关键词推断。

### 4.2 `ask(question, mode?)`

提问。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `question` | string | — | 自然语言提问 |
| `mode` | enum? | `verbatim` | `auto` / `wiki` / `verbatim` |

**模式说明：**

- `auto`：三条腿搜索——Aleph keyword + Aleph embedding (via MemPalace) + MemPalace raw → 合并去重
- `wiki`：只查 Aleph wiki pages（标题/别名/摘要/正文 keyword 匹配）
- `verbatim`：只查 MemPalace raw（原话全文 embedding 检索）

**返回**：
```json
{
  "answer": "[wiki 0.85] ...\n\n---\n\n[verbatim 0.72] ...",
  "sources": [
    { "kind": "wiki",     "ref": "wiki:hai-long",              "score": 0.6 },
    { "kind": "verbatim", "ref": "mempalace:mp-search:...",     "score": 0.42 }
  ],
  "queried_event_id": "01JW..."
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
  "id":      "01JW...",              # ULID (26 chars)
  "ts":      "2026-05-07T...",       # UTC datetime
  "type":    "raw.captured",         # 见 5.2
  "source":  "explicit"              # producer label
             | "hook:lifecycle"
             | "hook:context-pressure"
             | "worker:router"
             | "worker:intake"
             | "worker:intake:wiki-round",
  "payload": { ... }                 # type-specific
}
```

### 5.2 事件类型

| type | 谁发 | 含义 |
|---|---|---|
| `raw.captured` | hook / MCP `remember()` | 原始输入进 bus |
| `memory.stored` | router / intake | 已写入 MemPalace drawer |
| `memory.routed` | router / intake | 路由决策记录（verdict + wing/room） |
| `wiki.promoted` | intake:wiki-round | AlephRound 写入/更新 wiki pages |
| `memory.curated` | router / curator | 去重跳过（dedup）/ crosslink 回填 / refresh 清理 |
| `memory.queried` | reader | 一次 ask 调用 |

> **铁律**：type 保持窄、扩展走 payload。任何新需求**先尝试塞进现有 type 的 payload**，加新 type 要谨慎。

### 5.3 持久化

- **sqlite ring buffer**，最近 N=500 条（可配）
- 落盘但不保证永久 —— 长期记忆是 MemPalace + Aleph 的事
- 用途：status feed、failure replay、observability、dedup cursor

---

## 6. Workers — 3 个独立 worker

| worker | 触发 | 输入事件 | 输出 | 核心职责 |
|---|---|---|---|---|
| **router** | `remember()` 同步调用 | `raw.captured` (explicit) | `memory.stored` + `memory.routed` | 规则路由 → MemPalace raw 写入 |
| **intake** | async consume loop | `raw.captured` (hook) | `memory.stored` + `memory.routed` + `wiki.promoted` | LLM 提取 → MemPalace raw + AlephRound wiki |
| **curator** | intake post-round | (none — triggered internally) | `memory.curated` | wiki 维护：refresh dedup → crosslink backlinks |

### 6.1 router — 同步快路径

处理 `remember()` 显式调用：

1. 根据 `kind` 或关键词推断 room（`room_decisions` / `room_facts` / `room_feelings` / `room_todos` / `room_events` / `room_general`）
2. 内容哈希 dedup（T1.19）：相同内容 5 分钟内重复 → `memory.curated(reason=dedup)`
3. 写入 MemPalace drawer → emit `memory.stored`
4. **不走 LLM**（fast-path，同步返回）

### 6.2 intake — 异步处理 hook 捕获

处理 hook 产生的 `raw.captured` events（source ≠ explicit）：

1. 按 `capture_batch_id` 分组
2. **LLM extraction**：每组 turn → keep/skip + summary + entities + claims
3. **全量写入**：所有 turns → MemPalace raw（不管 keep/skip）
4. **Wiki consolidation**：keep turns → AlephRound → create/update wiki pages
5. **Embedding sync**：新建/更新的 wiki pages → MemPalace `aleph` wing（embedding 搜索面）
6. **Curator**：post-round wiki 维护 → refresh dedup + crosslink backlinks → emit `memory.curated`

### 6.3 curator — wiki 维护

每次 wiki round 后自动运行：

1. **Refresh**：去重段落（whitespace-normalized 精确匹配）+ History 条目去重 + 空行折叠
2. **Crosslink**：全量扫描 wiki body，为提到其他页面标题/别名的纯文本插入 `[[wikilink]]`
3. 有变更时 emit `memory.curated(reason=crosslink/refresh)`

也可独立调用（`Curator(aleph=..., bus=None).run()`）用于 CLI / 手动维护。

**LLM 降级**：无 API key 或网络错误时，turns 仍写入 MemPalace raw（v0.0.1 行为），只是跳过 wiki 合并。

---

## 7. Adapters — 与外部引擎的握手

### 7.1 MemPalace Adapter

两个实现：

| 实现 | 用途 | 持久化 |
|---|---|---|
| `InMemoryMemPalaceAdapter` | 测试 / 开发 | ✗（RAM only） |
| `StdioMemPalaceAdapter` | 生产 | ✓（ChromaDB via subprocess） |

API surface：
- `write(content, wing, room, source_file?)` → `MemPalaceWriteResult`
- `search(query, limit?, wing?, room?)` → `list[MemPalaceHit]`
- `close()`

**命名空间**（`adapters/naming.py`）：

```
wing_<project>  — 项目级隔离（e.g. wing_itsme）
room_<topic>    — 主题 room（e.g. room_decisions, room_user-turns）
aleph           — wiki embedding 专用 wing（不混进项目搜索）
room_wiki       — wiki page 存储 room
```

**后端选择**（`$ITSME_MEMPALACE_BACKEND`）：

| 值 | 行为 |
|---|---|
| `auto`（默认） | 尝试 `stdio`；失败回退 `inmemory` + stderr 警告 |
| `stdio` | 启动 MemPalace subprocess（需 `mempalace` 已安装） |
| `inmemory` | 进程内 dict（重启丢失） |

> **可选依赖**：`pip install itsme[mempalace]` 安装 `mempalace>=3.0`，让 `python3 -m mempalace.mcp_server` 直接可用，无需 `ITSME_MEMPALACE_COMMAND` 指定路径。

### 7.2 Aleph Subsystem

Aleph 不是外部服务，而是 itsme 进程内的一个**模块**（`core/aleph/`）。它把"记忆管理员"的标准流程拆成可观察的 pipeline，最终把成品落到 Obsidian Vault。

#### 7.2.1 内部模块

```
core/aleph/
├── wiki.py           # Wiki 读写 + keyword 搜索（Aleph class）
├── round.py          # AlephRound — LLM 驱动的 create/update 决策
├── pipeline/
│   ├── crosslink.py  # Auto-insert [[wikilink]] backlinks (T4.0)
│   └── refresh.py    # Dedup paragraphs + history entries (T4.0b)
└── prompts/
    ├── intake.md     # per-turn extraction prompt
    └── round.md      # wiki page create/update prompt
```

#### 7.2.2 Wiki Page 数据模型

每个 page 是一个 Markdown 文件，YAML frontmatter + body：

```yaml
---
title: 海龙
type: person
domain: work
subcategory: people
aliases: [hailong]
summary: 星图计划呈现端主负责人
sources:
  - "[[sources/2026-04-29-session]]"
links:
  - title: "星图计划"
    url: ""
related:
  - "[[starmap]]"
tags:
  - wing/work
  - type/person
last_verified: 2026-04-30
---

# 海龙

负责星图项目的产品设计和规划。
```

**page type**：`person` / `project` / `decision` / `concept` / `event` / `meeting`

#### 7.2.3 Search

Aleph 提供两种搜索面：

1. **Keyword search**（`wiki.py:search`）：
   - 标题 / 别名精确匹配（高权重）
   - 摘要 / 正文 token 重叠（Jaccard-like）
   - CJK 字符逐字切分（T3.8 修复）

2. **Embedding search**（via MemPalace `aleph` wing）：
   - Wiki pages 全量同步到 MemPalace（`_wiki_page_for_embedding`：title + summary + body）
   - ChromaDB embedding 检索语义匹配（"谁管产品" → 找到标题为"海龙"的 page）
   - 启动时 bootstrap（`sync_all_wiki_pages`），运行时增量同步

#### 7.2.4 AlephRound — wiki 合并

IntakeProcessor 的 keep turns 通过 AlephRound 合并到 wiki：

```
kept_turns (来自 LLM extraction)
    │
    ▼
AlephRound.process(turns)
    │
    ▼ LLM: "这些 turns 应该 create/update 哪些 pages？"
    │
    ├── create  ──► Aleph.write_page(slug, frontmatter, body)
    └── update  ──► Aleph.read_page → merge → write_page
                       │
                       ▼
                  emit wiki.promoted
                       │
                       ▼
                  _sync_wiki_embeddings(slugs_affected)
```

#### 7.2.5 Vault 布局

```
~/Documents/Aleph/              # $ITSME_ALEPH_ROOT（或自动发现）
├── dna.md                      # Vault DNA（Aleph 的 identity）
├── index.md                    # Claude 自动维护的总览表
├── log.md                      # Append-only 操作日志
├── wings/                      # 按 domain 分组的 wiki pages
│   ├── work/
│   │   ├── projects/           # type=project
│   │   └── people/             # type=person
│   ├── technology/
│   │   ├── engineering/        # type=concept, decision
│   │   └── projects/
│   ├── life/
│   └── ...
└── sources/                    # 来源记录（session summaries）
```

- Aleph **独占写**，用户只读
- 发现规则：`$ITSME_ALEPH_ROOT` → `$ITSME_ALEPH_VAULT`（legacy）→ `~/Documents/Aleph/`
- 识别标志：`dna.md` 存在

#### 7.2.6 LLM 调用边界

Aleph 内部需要 LLM 的环节：
- **intake extraction**：per-turn → keep/skip + summary + entities + claims
- **AlephRound**：batch of kept turns → create/update decisions for wiki pages

**Provider 抽象**（`core/llm.py`）：`LLMProvider` 接口，当前实现 DeepSeek（`$DEEPSEEK_API_KEY`）。`StubProvider` 用于测试。

---

## 8. Key Flows

### 8.1 Hook 捕获（自动，异步）

```
hook 触发（SessionEnd / PreCompact）
              │
              ▼
  收集即将丢失的 turn / context 内容
              │
              ▼
  ① Structural strip（去 CC envelope / boilerplate）
              │
              ▼
  ② Turn slice（按 user/assistant turn 切分）
              │
              ▼
  per-turn emit(raw.captured, source="hook:lifecycle")
              │
              ▼
        intake worker consumes (async)
              │
              ├── ③ LLM intake: 每 turn → keep/skip + {summary, entities, claims}
              │
              ├── ALL turns → MemPalace.write(raw_text, wing, room)
              │                 emit(memory.stored)
              │
              ├── KEEP turns → AlephRound.process(turns) → wiki pages
              │                 emit(wiki.promoted)
              │
              └── Wiki pages → MemPalace.write(aleph wing, room_wiki)
                               （embedding sync for semantic search）
```

### 8.2 显式记忆（同步）

```
agent calls remember("...", kind="decision")
  │
  ▼
emit(raw.captured, source="explicit")
  │
  ▼
router (fast-path, rule-based)
  │
  ├── content_hash dedup check → skip if duplicate
  │
  ▼
MemPalace.write(content, wing, room)
  │
  ▼
emit(memory.stored) → return RememberResult
```

### 8.3 查询（三条腿搜索）

```
ask("产品设计", mode=auto)
  │
  ▼
dual_search()
  │
  ├── Leg 1: Aleph.search("产品设计")     ─► wiki keyword hits (高精度)
  │           (title/alias/summary/body token matching)
  │
  ├── Leg 2: MemPalace.search("产品设计", wing="aleph")
  │           ─► wiki embedding hits (语义匹配)
  │           (ChromaDB embedding 找 keyword 漏掉的)
  │
  └── Leg 3: MemPalace.search("产品设计", wing="wing_<project>")
              ─► raw verbatim hits (高召回兜底)
  │
  ▼
merge + dedup（content[:100] 去重 keyword/embedding）
  │
  ▼
emit(memory.queried) → return AskResult
```

> **三条腿保障**：Aleph keyword 精准（"海龙"直接匹配）；Aleph embedding 语义补全（"产品负责人"→海龙）；MemPalace raw 高召回兜底（LLM 未提取的内容仍在 raw 里命中）。

### 8.4 Dedup（T1.19）

- 每条 `remember()` 内容计算 `content_hash`
- router 检查 5 分钟内是否已有相同 hash 的 `memory.stored`
- 重复 → emit `memory.curated(reason=dedup)` → 返回原 drawer_id
- hook 与 explicit 的交叉去重也经此路径

---

## 9. Plugin Layout

采用 **src-layout**：

```
itsme/                                  # git repo root
├── .claude-plugin/
│   ├── plugin.json                     # CC plugin 注册（mcpServers + hooks）
│   ├── marketplace.json
│   └── itsme/
│       └── itsme.skill.md              # skill 文档
│
├── src/
│   └── itsme/                          # ← 唯一的 Python 包根
│       ├── mcp/                        # 对外 3 个 verb
│       │   ├── server.py               # FastMCP server bootstrap
│       │   └── tools/
│       │       ├── remember.py
│       │       ├── ask.py
│       │       └── status.py
│       ├── core/                       # 内部引擎
│       │   ├── api.py                  # Memory orchestrator（核心 facade）
│       │   ├── search.py               # dual_search / wiki_search（三条腿）
│       │   ├── dedup.py                # content_hash + producer_kind
│       │   ├── llm.py                  # LLMProvider / DeepSeekProvider / StubProvider
│       │   ├── events/
│       │   │   ├── schema.py           # EventType enum + EventEnvelope
│       │   │   ├── ringbuf.py          # SQLite ring buffer
│       │   │   └── bus.py              # EventBus facade
│       │   ├── workers/
│       │   │   ├── router.py           # sync fast-path (explicit remember)
│       │   │   ├── intake.py           # async intake (hooks → LLM → wiki)
│       │   │   ├── curator.py          # wiki maintenance (crosslink + refresh)
│       │   │   └── scheduler.py        # WorkerScheduler (async tasks)
│       │   ├── adapters/
│       │   │   ├── mempalace.py        # MemPalaceAdapter ABC + InMemory impl
│       │   │   ├── mempalace_stdio.py  # StdioMemPalaceAdapter (subprocess)
│       │   │   └── naming.py           # wing/room slug helpers + WIKI_WING
│       │   ├── aleph/
│       │   │   ├── wiki.py             # Aleph class: read/write/search pages
│       │   │   ├── round.py            # AlephRound: LLM wiki consolidation
│       │   │   ├── pipeline/
│       │   │   │   ├── crosslink.py    # Auto-insert [[wikilink]] backlinks
│       │   │   │   └── refresh.py      # Dedup paragraphs + history entries
│       │   │   └── prompts/
│       │   │       ├── intake.md       # per-turn extraction prompt
│       │   │       └── round.md        # wiki page create/update prompt
│       │   └── filters/
│       │       └── envelope.py         # structural strip + turn slice
│       └── hooks/                      # hook scripts (Python entry points)
│           ├── lifecycle.py            # SessionEnd hook
│           └── context_pressure.py     # PreCompact hook
│
├── hooks/                              # shell wrappers for IDE hook integration
│   ├── cc/                             # Claude Code
│   │   └── before-exit.sh
│   └── codex/
│
├── docs/
│   ├── ARCHITECTURE.md                 # 本文
│   └── ROADMAP.md
│
├── tests/                              # pytest (549 tests)
├── pyproject.toml                      # hatchling 构建
└── README.md
```

---

## 10. Design Decisions / Trade-offs

| # | 决策 | 取舍 |
|---|---|---|
| D1 | **窄 MCP**（3 动词）而非全开放 MemPalace | 心智简单 vs 灵活性损失 |
| D2 | **Event-sourced**（一切经 EventBus） | 解耦 / 可观察 vs 多一跳延迟 |
| D3 | **Aleph 独立 vault** | 零冲突 vs 用户两个 vault |
| D4 | **Aleph 独占写**（用户只读） | 简单 vs 用户失去手动整理权 |
| D5 | **hook 绑 SessionEnd / PreCompact** | 噪音低、与睡眠固化同构 vs 实时性差 |
| D6 | **kind 可选** | 灵活 vs router 需要双路径（规则 + 关键词推断） |
| D7 | **Aleph 进程内模块**（非独立服务） | 部署简单、零跨进程开销 vs 与 itsme 强耦合 |
| D8 | **Python 全栈** | 与 MemPalace 同语言、生态成熟 vs CC plugin TS 生态对接需 bridge |
| D9 | **不做 per-turn hook**，只做 consolidation hook | 噪音低 vs 漏掉一些瞬间（由 explicit remember 兜底） |
| D10 | **ask 三腿搜索**（Aleph keyword + embedding + MemPalace raw）→ 合并 | 高精度 + 语义 + 高召回 vs 合并逻辑复杂 |
| D11 | **Intake LLM 跑在 async loop**，不在 hook 进程里 | 不阻塞 hook 超时 vs intake 有延迟 |
| D12 | **explicit `remember()` 不走 intake**（直存 MemPalace） | fast-path 保持同步 vs explicit 写入没有 wiki 搜索面 |
| D13 | **MemPalace 全量存 raw**（包括 LLM 判断为 skip 的 turn） | 永远不丢 vs 搜索有噪音（Aleph 层过滤） |
| D14 | **Wiki embedding 存 MemPalace `aleph` wing** | 复用已有 ChromaDB infra vs 搜索需区分 wing |
| D15 | **mempalace 为可选依赖** | 安装简单 vs 基础包更重 |

---

## 11. Out of Scope (v0.x)

- 多用户 / 多 agent 隔离
- 加密 / 隐私分级
- 跨设备同步（依赖 vault 自身机制即可）
- 主动总结 / 推荐（"你今天该回顾 X"）
- UI / Web 界面

---

## 12. Environment Variables

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | DeepSeek API key，启用 LLM intake + wiki consolidation |
| `ITSME_MEMPALACE_BACKEND` | `auto` | `auto` / `stdio` / `inmemory` |
| `ITSME_MEMPALACE_COMMAND` | `python3 -m mempalace.mcp_server` | MemPalace subprocess command |
| `ITSME_ALEPH_ROOT` | — | Aleph vault path（自动发现 `~/Documents/Aleph/`） |
| `ITSME_ALEPH_VAULT` | — | legacy alias for `ITSME_ALEPH_ROOT` |
