# itsme — Roadmap & Task Breakdown

> Status: **Design draft** · v0.0.x
> Repo: <https://github.com/coxon/itsme>
> Language: **Python**
> Last updated: 2026-05-08

策略：**端到端最薄一刀** 先打通，再逐步加厚。

---

## Locked decisions

- ✅ Aleph：**从 0 自建**（进程内模块，见 ARCHITECTURE §7.2）
- ✅ 语言：**Python**
- ✅ 起步版本：**v0.0.1**
- ~~`ask(promote=true)`~~：**dropped（2026-05-07）** — intake→AlephRound 已在写时聚合知识到 wiki 页面，Claude 在读时自然综合多源结果，双重 LLM 调用无附加价值
- ✅ LLM provider：**Anthropic**（v0.0.x 先支持，provider 抽象层为未来留口）
- ✅ Embedding：**本地 sentence-transformers**（v0.0.3 启用）
- ✅ Hook 触发：**被动 lifecycle**（SessionEnd=before-exit / PreCompact=before-compact）+ **主动 context-pressure**（UserPromptSubmit / PostToolUse，阈值采样 + Schmitt-trigger debounce）
- ✅ 多 IDE：**先支持 CC + Codex**，安装方式分别打包
- ✅ 仓库管理：**简化 gitflow**（main + feature/*；squash merge；1.0 后再升级到完整 git-flow，见 CONTRIBUTING.md）
- ✅ 包布局：**src-layout**（`src/itsme/`，避免与 MCP SDK 命名冲突，见 ARCHITECTURE §9）
- ✅ Vault 默认路径：`~/Documents/Aleph/`（Obsidian vault，Apple iCloud 同步，Karpathy llm-wiki 模式）。`$ITSME_ALEPH_ROOT` 可覆盖（`$ITSME_ALEPH_VAULT` 作为 legacy fallback 仍兼容）。无 vault 时降级（不报错，不写 wiki，不搜 wiki）
- ✅ MemPalace 适配：**Protocol + InMemory 参考实现**（`core/adapters/mempalace.py`）+ **stdio MCP-client backend 已落地**（`core/adapters/mempalace_stdio.py`，T1.13.5，v0.0.1 GA），由 `$ITSME_MEMPALACE_BACKEND={inmemory,stdio,auto}` 切换
- ✅ MCP server 框架：**FastMCP + stdio**（mcp Python SDK 1.27+）
- ✅ Router 策略：v0.0.1 **规则路由** — `kind` 直查 + 关键词推断（decision/todo/feeling/event）+ fallback general，**不引入 LLM**；LLM 路由推迟到 v0.0.2 配合 promoter 一并落地
- ✅ LLM 模型：统一用 **DeepSeek**（`deepseek-chat`，`$ITSME_LLM_MODEL` 可覆盖）；API key 通过 `$DEEPSEEK_API_KEY` 配置
- ✅ Aleph v0.0.2 形态：**双层存储** — (1) MemPalace ChromaDB（raw per-turn），(2) Obsidian wiki（`~/Documents/Aleph/`，跨 session 聚合知识条目，iCloud 同步）。数据流：intake → MemPalace raw + AlephRound → Obsidian wiki 页面。SQLite FTS5 extraction index 已在 T3.0 移除（wiki + MemPalace 双引擎已覆盖其用例）
- ✅ ask 搜索策略：**双引擎并行**（Aleph wiki 页面 + MemPalace raw）→ 合并去重返回。Wiki 命中排前（聚合知识），MemPalace 兜底（raw 全文）。LLM 提取遗漏时 MemPalace raw 兜底，永远不漏
- ✅ Intake 运行位置：**router 异步 consume loop**（不阻塞 hook 进程）；explicit `remember()` 不走 intake，保持同步 fast-path
- ✅ LLM 降级策略：API 不可用时 raw 直存 MemPalace（同 v0.0.1 行为），不写 Aleph，不阻塞

---

## Milestones at a glance

| 版本 | 主题 | 关键交付 | 估算 |
|---|---|---|---|
| **v0.0.1** | 端到端骨架 | hook → events → router → MemPalace → ask 直查 MP，能装进 CC | ✅ |
| **v0.0.2** | Intake + Aleph Wiki | LLM intake → MemPalace raw + AlephRound → Obsidian wiki 页面 → `ask(mode=auto)` 双引擎搜索 + `ask(mode=wiki)` | ✅ |
| **v0.0.3** | Search 打磨 + Embedding | CJK search 修复、wiki embedding via MemPalace、score 修复、mempalace 可选依赖 | ✅ |
| **v0.0.4** | Crosslink + Curator + 体验 | wiki crosslink 回填、refresh 去重、curator、skill 文档、status feed 渲染 | ✅ |
| **v0.0.5+** | 长尾 | 跨 session 主题聚类、主动召回、KG 推理 | — |

> **🎨 Icon track（平行路线）**：图标资产按 phase I1 / I2 / I3 / I4 分别挂在 v0.0.1 / v0.0.2 / v0.0.4 / v0.1+ 上一起发布。详见 [docs/ICONS.md](./ICONS.md)。

---

## v0.0.1 — End-to-end stub

**目标**：CC / Codex 装上 plugin，能 `remember()` 写入 MemPalace，能 `ask()` 查回来。**没有 Aleph，没有 promoter**。

### Tasks

#### P0 — 骨架
- [x] **T1.1** 仓库结构落地（按 ARCHITECTURE §9，src-layout）
- [x] **T1.2** `plugin.json` 完善（mcpServers 实启）+ `skills/itsme/SKILL.md` 实质内容
- [x] **T1.3** Python 包初始化（`pyproject.toml`、hatchling、ruff/pytest 配置）
- [x] **T1.4** 基础 README（安装方式、3 动词速查、链接到 docs）

#### P0 — EventBus
- [x] **T1.5** 定义 envelope schema（`core/events/schema.py`，pydantic）
- [x] **T1.6** sqlite ring buffer 实现（`core/events/ringbuf.py`，500 条容量可配）
- [x] **T1.7** EventBus facade API：`emit(type, source, payload)` / `tail(n, types?)` / `since(cursor_id, types?, limit)` / `count()` / `close()`
- [x] **T1.8** ULID 生成、ts 注入

#### P0 — MCP Surface
- [x] **T1.9** MCP server 入口（`mcp/server.py`，基于 `mcp` Python SDK，FastMCP + stdio）
- [x] **T1.10** `remember(content, kind?)` → 写 events，同步返回 id
- [x] **T1.11** `ask(question, mode?)` → 直调 MemPalace.search，**v0.0.1 不实现 promote**（仅 `mode='verbatim'`，`auto`/`wiki`/`now` 显式 `NotImplementedError`）
- [x] **T1.12** `status(scope?, format?)` → 读 events ring（`json` / `feed` 两种格式）

#### P0 — Adapter
- [x] **T1.13** MemPalace adapter（`core/adapters/mempalace.py`，Protocol + `InMemoryMemPalaceAdapter` 参考实现，stdio MCP-client backend 留待 T1.13.5）
- [x] **T1.13.5** **Persistent MemPalace backend**（`core/adapters/mempalace_stdio.py`，stdio JSON-RPC MCP-client adapter）— **v0.0.1 GA**：drawer 跨 MCP server 重启不再丢失。`build_default_memory` 通过 `$ITSME_MEMPALACE_BACKEND={inmemory,stdio,auto}` 切换；默认 **`auto`**（T1.20 dogfood 暴露 inmemory-default 导致 ask 0 hits 后翻盘）。失败模型清晰：`MemPalaceConnectError` vs `MemPalaceWriteError`；handshake / call timeout、子进程崩溃、空 palace 错误回包都有专属分支与单测。fake server 在 `tests/core/adapters/fake_mempalace_server.py`，真二进制 smoke 在 `tests/smoke/test_mempalace_stdio_roundtrip.py`（无 MemPalace 时自动 skip）。同次修复 T1.20 暴露的 InMemoryMemPalaceAdapter CJK 分词缺陷：`\w+` 把整段中文吞成单 token，导致短查询不命中长 drawer；改为 CJK 逐字符分词 + Latin 整 token 混合。
- [x] **T1.14** wing/room 命名规范（itsme 默认用 `wing_<project>` / `room_<topic>`，namespace 隔离）

#### P0 — Worker
- [x] **T1.15** router worker（规则路由，仅靠 `kind` 与简单关键词；sync fast-path + async consume loop，参见 `core/workers/router.py`）
- [x] **T1.16** worker 调度方式：`WorkerScheduler` — 后台线程独立 asyncio loop（不复用 FastMCP loop，避免 head-of-line blocking）

#### P0 — Hook
- [x] **T1.17** CC hook 脚本：`.claude-plugin/plugin.json` 内联 `hooks` 字段（绕开 [anthropics/claude-code#45296](https://github.com/anthropics/claude-code/issues/45296) 的外部 `hooks/hooks.json` 删除 bug）+ `hooks/cc/before-exit.sh` / `before-compact.sh`（CC SessionEnd / PreCompact 触发）。Python 实现：`itsme.hooks.lifecycle`，读 `transcript_path` JSONL 取 tail（默认 10K chars），emit `raw.captured` with `source=hook:before-<x>` + `transcript_ref`。
- [x] **T1.17b** **Context-pressure hook**（主动式）：CC `UserPromptSubmit` / `PostToolUse` 触发，读 `transcript_path` 估 tokens（`chars/4`），跨阈值（默认 0.70，可配 `$ITSME_CTX_THRESHOLD` / `$ITSME_CTX_MAX`）emit `raw.captured` with `source=hook:context-pressure` + `transcript_ref`。Schmitt-trigger debounce：触发后须 pressure 跌 ≥10% (`disarm_drop`) 才重新 arm，状态持久化到 `~/.itsme/state/pressure-<sid>.json`。比 `before-compact` 早，抢救窗口大（v0.0.2 由 Aleph promoter 消费）。
- [ ] **T1.18** Codex hook 适配（先调研 Codex 的 hook 接口，按其规范实现）— **优先级下调（2026-05-06）**：v0.0.1 GA 在 CC 上验证完成；T2.0a/b/c 暴露 hook capture 链路本身仍有写侧噪音 + 读侧召回的根问题，先治 CC 这套 → Codex 移植留到 v0.0.2 或之后。
- [x] **T1.19** hook 与 explicit remember 的去重（`core/dedup.py` 里算 `content_hash = sha256(content.strip())` 与 `producer_kind` 桶，所有 `raw.captured` producer 都打标；router 写前扫 `memory.stored` 的 `content_hash` — 命中就发一个 `memory.curated` with `reason="dedup"` + `original_stored_event_id` 并返回原 drawer。dedup 键故意用 `memory.stored`（post-write）而非 `memory.routed`（pre-write），失败写不会污染后续重试。跨 producer：explicit ↔ hook:lifecycle ↔ hook:context-pressure 同 content 互相 coalesce；stored 只留 1 条，caller 拿到同一个 drawer_id。没引入新 event type — 复用现有 6 个。）

#### P1 — 验收
- [x] **T1.20** Smoke test：自动 + 手动两层（`tests/smoke/` 17 项 + `docs/SMOKE.md` 真 CC runbook）。CC 装载、SessionEnd / PreCompact / context-pressure → events ring + router → MemPalace；surfaced T1.13.5 跨重启 drawer 丢失为 v0.0.1 GA blocker。**v0.0.1 GA 验收完成（2026-05-06）**：在真实 CC v2.1.119 + 自定义 gateway + mempalace pip 后端 + 内联 hooks (#16) 链路下，`/exit` → `raw.captured | hook:before-exit` → `memory.routed | worker:router` → `memory.stored | adapter:mempalace` 全链路在 `~/.itsme/events.db` 可见；同 session 后续 `ask` 跨 hook-snapshot 命中。dogfood 暴露的两个**配置**坑（非代码 bug）写进 `docs/INSTALL.md` "Real-world setup notes"：`claude --bare` 跳过所有 hooks；自定义 gateway 必须用 `ANTHROPIC_AUTH_TOKEN`（非 `_API_KEY`）。同次给 `skills/itsme/SKILL.md` 加 "Tool selection priority" 强引导，让模型在 WebSearch 等外部工具前先 `ask` itsme — 修复 dogfood 时观察到的 "Palantir 营收" 类 query 直奔 web 而绕过私人记忆的问题。
- [ ] **T1.21** Codex 装载同样验证 — **优先级下调（2026-05-06）**：blocked on T1.18；同 T1.18 一并推迟。
- [x] **T1.22** `status()` 在 IDE 里能看（`format='feed'` 升级成每事件一行的人类可读 feed：`HH:MM:SS  TAG  one-line summary`，per-event-type 渲染 — `raw.captured` 显 producer_kind + 80字内容片段，`memory.routed` 显 wing/room+rule，`memory.stored`/`memory.curated` 显 8字 drawer **后缀**（前缀都是 ULID 时间戳会撞），`memory.curated reason=dedup` 显被去重的 producer_kind，`memory.queried` 显问题+hit_count；feed 顶上加一条 summary header `12 events · 4 raw · 3 stored · 1 dedup · 1 query` 跳过 0 桶；空窗口显 `(no events in window)` 而不是空串。`format='json'` 完全不动 — 机器消费者拿到的还是原 `StatusResult`。21 个新测试 pin 行格式 / 排序 / 截断 / dedup 可见性 / JSON 不变 contract。）
- [x] **T1.23** **CC 标准安装路径**（`/plugin marketplace add coxon/itsme` + `/plugin install itsme@itsme`）：仓库自宿一份 `.claude-plugin/marketplace.json`，single-plugin self-host，`"source": "./"` 直接指向 marketplace root（plugin 与 marketplace 共用一个 repo）。`plugin.json` 与 hook shim 全部切到 `uv run --project ${CLAUDE_PLUGIN_ROOT} python -m ...`，由 uv 在插件 cache 目录里自管 venv，去掉对全局 `pip install itsme` / 用户 `$PATH` 上有正确 Python 的依赖。Hook timeout 15/10s 吸收首次冷启动 `uv sync`（~5-10s）开销；steady-state hook 仍是 ~50-100ms uv overhead。symlink dev 模式作为开发者备用路径保留并降级到 INSTALL.md 二级章节。

**v0.0.1 完成定义**：在 CC（或 Codex）里聊一段 → SessionEnd / PreCompact / context-pressure 触发 → MP 里看到 drawer → `ask` 能查回来。

---

## v0.0.2 — Intake + Aleph Wiki Pipeline

**目标**：hook 捕获经 LLM intake 提取结构化数据（summary/entities/claims），MemPalace 存 raw 原文（per-turn drawer），AlephRound 聚合知识到 Obsidian wiki 页面。`ask(mode=auto)` 双引擎搜索：wiki 页面（高精度聚合知识）+ MemPalace raw（高召回），合并去重。

> **设计原则**：MemPalace 是"什么都记"的原料仓——搜索面是 raw 全文。Aleph wiki 是 LLM 聚合的知识库（Obsidian 页面）。`ask` 搜两路合并，**wiki 漏覆盖时 MemPalace raw 兜底，永远不丢**。

> **成本**：Intake 用 DeepSeek（统一模型，`$ITSME_LLM_MODEL` 可覆盖），v0.0.3+ 视成本按需拆分。

### Tasks

#### Pre-intake — 结构性清洗（无 LLM）

- [x] **T2.0a** **Envelope 过滤**：regex 去掉 CC 注入的 `<local-command-caveat>` / `<command-name>` / `<command-message>` / `<command-args>` / `<local-command-stdout>` 五件套块。实现为 `core/filters/envelope.py`，可独立开关。**只过滤 hook capture，不过滤 explicit `remember()`**。
- [x] **T2.0b** **Turn 切片**：hook capture 从 "整段 transcript tail → 一个 `raw.captured`" 改为按 user/assistant turn 切成多条 `raw.captured`，每条独立 `content_hash` 和 `producer_kind`。实现在 `hooks/_common.py` + `hooks/lifecycle.py`。

#### LLM 基础设施

- [x] **T2.6** **LLM provider 抽象**（`core/llm.py`）：`LLMProvider` protocol + `DeepSeekProvider` 实现（OpenAI-compatible API）。统一模型配置（`$ITSME_LLM_MODEL` 默认 `deepseek-chat`，`$DEEPSEEK_API_KEY`）。最小接口：`complete(system, messages) → str`。

#### Aleph Extraction Index（轻量 — 不含 wiki / vault）

- [x] **T2.1** `core/aleph/store/index.py`：sqlite schema + FTS5。`extractions` 表（id ULID, turn_id, raw_event_id, summary TEXT, entities JSON, claims JSON, source TEXT, created_at REAL）。`extractions_fts` 虚拟表（summary, entities, claims）。
- [x] **T2.2** `core/aleph/search.py`：FTS5 关键词搜索 → ranked `ExtractionHit` 列表。v0.0.2 不含 embedding。
- [x] **T2.3** `core/aleph/api.py`：对内 SDK — `write_extraction(...)` / `search(query, limit)` / `close()`。

#### Intake Pipeline（核心新增）

- [x] **T2.0d** **LLM intake processor**（`core/workers/intake.py`）：
  - 在 router 异步 consume loop 中运行（不阻塞 hook 进程的 15s 超时）
  - 消费结构性清洗 + turn 切片后的 `raw.captured` 批次
  - 一次 LLM 调用，批量处理所有 turn：
    - keep → 提取 `{summary, entities, claims}`
    - skip → 标记 `skip_reason`（低信息 / 重复 / boilerplate）
  - **所有 turn**（keep + skip）→ `MemPalace.write(raw_turn, wing, room)`（全量入库）
  - **keep turn** 额外 → `Aleph.write_extraction(summary, entities, claims, turn_id)`
  - **skip turn** → emit `raw.triaged` with `skip_reason`（可观察性，不静默吞）
  - explicit `remember()` **不走 intake**，保持同步 fast-path
  - LLM 不可用时**降级**：raw 直存 MemPalace（同 v0.0.1），不写 Aleph，stderr 日志，不阻塞
  - Intake prompt 模板：`core/aleph/prompts/intake.md`

#### MCP 升级

- [x] **T2.19** `ask(mode=auto)` **双引擎搜索**：并行查 `Aleph.search(q)` (wiki 页面) + `MemPalace.search(q)` (raw) → 合并去重，wiki 命中排前 → 返回。**这是 v0.0.2 的 ask 默认模式**。
- [x] **T2.20** `ask(mode=verbatim)` MemPalace only（行为不变）。
- [x] **T2.21** `ask(mode=wiki)` Aleph wiki 页面搜索（已实现，非 NotImplementedError）。

#### Obsidian Vault 接通

- [x] **T2.27** `core/aleph/wiki.py`：Aleph — Obsidian wiki 读写适配器（dna.md/index.md/log.md/wings）。数据类：`PageMeta` / `PageHit` / `IndexEntry`。YAML frontmatter 解析与渲染。路径安全（traversal 防护 + 全局 slug 唯一）。CJK 逐字符分词（#28）。
- [x] **T2.28** `core/aleph/round.py`：AlephRound — LLM 驱动的对话→wiki 页面 create/update。prompt 在 `core/aleph/prompts/round.md`。解析健壮：空数组 / markdown fenced / 畸形 op / 值类型检查。
- [x] **T2.29** **Pipeline 接通**：IntakeProcessor 接受 `aleph` 参数，batch 完成后自动触发 AlephRound。kept turns（且 drawer_id 成功）→ wiki create/update。`wiki.promoted` 事件发射。
- [x] **T2.30** **Wiki 自动发现**：`$ITSME_ALEPH_ROOT` 或 `~/Documents/Aleph/`（`$ITSME_ALEPH_VAULT` legacy fallback）。`build_default_memory` 自动发现并传入。无 wiki 时降级（不报错）。
- [x] **T2.31** **真实烟测**：DeepSeek + `~/Documents/Aleph/` 临时副本。中文对话 → intake 2 kept / 1 skipped → wiki 新建 2 页 → `ask(mode=wiki)` 命中 → `ask(mode=auto)` 双引擎融合命中。全链路端到端通过。

#### 验收

- [x] **T2.23** 端到端：聊一段 → `/exit` → intake 提取 → Aleph index 有记录 + MemPalace 有 per-turn drawer → `ask(mode=auto)` 命中 Aleph 精准 hit + MemPalace 兜底 hit。
- [x] **T2.24** **Aleph 漏提取回归测试**：构造 LLM intake 未提取的实体（e.g. 一句话中的次要地名），验证 MemPalace raw search 仍能命中。Pin 为 fixture。
- [x] **T2.25** **LLM 降级测试**：API key 未配 / API 不可用时 hook capture 仍正常存入 MemPalace，`ask(mode=verbatim)` 正常返回，不报错。
- [x] **T2.26** `status(format=feed)` 能看到 `raw.triaged` 事件（skip/keep 可观察）。

**v0.0.2 完成定义**：hook capture → structural strip → turn slice → LLM intake → MemPalace raw + Obsidian wiki 页面 → `ask(mode=auto)` 双引擎搜索命中 → `ask(mode=wiki)` wiki 页面搜索。LLM 挂了也不丢数据。

---

## v0.0.3 — Search 打磨 + Embedding

**目标**：wiki 搜索质量提升（CJK 修复）。引入 embedding 混合检索提升 wiki 搜索召回。score 归一化修复。mempalace 可选依赖。

> **架构现状（2026-05-07）**：SQLite FTS5 extraction index 已在 T3.0 移除。Aleph = Obsidian wiki（`~/Documents/Aleph/`），source of truth。搜索 = 三条腿：wiki keyword + wiki embedding (via MemPalace `aleph` wing) + MemPalace raw（ChromaDB）。`ask(promote=true)` dropped — 写时 AlephRound 已聚合，读时 Claude 自然综合。

### Tasks

#### 已完成

- [x] **T3.0** **SQLite index 移除**：`core/aleph/store/index.py` 及相关代码已删除。wiki 页面 + MemPalace raw 双引擎覆盖原 per-turn extraction 用例。`dual_search` → `wiki_search` + `MemPalace.search`。
- [x] **T3.0b** 确认 vault 搜索覆盖度足够，砍掉 SQLite index + 一次 LLM 调用（intake extraction 简化为 keep/skip 判定 + wiki round）。

#### CJK Search 修复（#28）

- [x] **T3.8** **CJK wiki keyword search**：`wiki.py:search()` 原 `query.lower().split()` 把整段中文吞成单 token，导致不命中。改为 `_search_tokens()` — CJK 字符逐字切分 + Latin 整 token 混合。9 个 CJK 测试覆盖：中文标题/摘要/正文/别名、混合中英、日文平假名、排序。

#### Wiki Embedding via MemPalace（#30）

> **方案**：不独立引入 embedding provider，复用 MemPalace ChromaDB 的 embedding 能力。Wiki pages 同步到 MemPalace `aleph` wing，`dual_search` 增加 embedding 搜索腿。

- [x] **T3.11** **Wiki embedding sync**：`IntakeProcessor._sync_wiki_embeddings(slugs)` — AlephRound 成功后把受影响的 wiki pages 写入 MemPalace（`wing="aleph"`, `room="room_wiki"`）。内容格式：`title + summary + body`，给 embedding 模型完整上下文。`sync_all_wiki_pages()` 启动时 bootstrap 全量同步。
- [x] **T3.12** **命名空间隔离**：`adapters/naming.py` 新增 `WIKI_WING = "aleph"` / `WIKI_ROOM = "room_wiki"`，wiki embedding 与项目 raw 搜索隔离。
- [x] **T3.13** **三条腿搜索**：`search.py:dual_search()` 重写为三腿合并：① Aleph keyword → ② MemPalace embedding (`wing=aleph`) → ③ MemPalace raw (`wing=wing_<project>`)。content[:100] 去重防止 keyword + embedding 同一页面重复占位。
- [x] **T3.14** **启动 bootstrap**：`Memory.__init__` 调用 `sync_all_wiki_pages()` 索引现有页面。13 个测试覆盖：content formatting、sync plumbing、dual_search embedding、Memory startup、ask integration。

#### Score 修复 + mempalace 可选依赖（#31）

- [x] **T3.20** **Score 归一化**：`mempalace_stdio.py` 处理 MemPalace v3.3+ BM25-only 模式返回 `None` similarity + 旧版 v3.0.x 负值 similarity。
- [x] **T3.21** **mempalace 可选依赖**：`pyproject.toml` 新增 `[project.optional-dependencies] mempalace = ["mempalace>=3.0"]`。`pip install itsme[mempalace]` 后 `python3 -m mempalace.mcp_server` 直接可用，无需 `ITSME_MEMPALACE_COMMAND` 指定系统 python 路径。cross-restart 测试翻转为 happy path。

#### ARCHITECTURE.md 重写（#32）

- [x] **T3.23** ARCHITECTURE.md 全面重写：删除不存在的 promoter/curator/FTS5/promote=true，更新为 2 workers (router+intake)、Aleph 实际模块结构 (wiki.py+round.py+prompts/)、vault 实际布局、三条腿搜索、环境变量表。

#### 验收

- [x] **T3.24** **Embedding 搜索验证**：通过 `Memory.ask(mode='auto')` 验证真实 Aleph wiki（31 pages）的三条腿搜索。"海龙" → `wiki:hai-long` (score 0.6)、"产品设计" → starmap-agent-react + starmap。Embedding 腿需 `mempalace repair` 重建 cosine 索引后完全发挥作用。

**v0.0.3 完成定义** ✅：CJK search 修复 → wiki embedding 搜索 → score 归一化 → mempalace 可选依赖 → ARCHITECTURE.md 与现实同步。483 tests passing。

---

## v0.0.4 — Crosslink + Curator + 体验打磨

**目标**：wiki 自成长（crosslink 回填、refresh 去重）；去重失效跑起来；agent 真的"用得好" itsme。

### Tasks

#### Crosslink & 自成长（从 v0.0.3 推迟）

- [x] **T4.0** `core/aleph/pipeline/crosslink.py`：全量扫描 wiki body，自动插入 `[[wikilink]]`（目前 LLM 在 round prompt 中生成 `related: [[...]]`，但没有回填已有页面的 body 引用）。设计规则：first-occurrence-only、never-self-link、skip protected zones（fenced code / inline code / existing [[]] / callouts）、longest-match-first（CJK 感知）、shield new links（防子串泄漏）、idempotent + dry_run。29 个测试。
- [x] **T4.0b** `core/aleph/pipeline/refresh.py`：去重段落、清冗余（同一实体从多个 session 写入可能产生重复段落）。确定性、无 LLM：exact-duplicate paragraph removal（whitespace-normalized）、History entry dedup、blank-line collapse。Protected blocks（code / callouts）永不去重。23 个测试。
- [x] **T4.0c** 验收：真实 wiki（40 pages）crosslink dry-run → 17 links inserted, idempotent（pass 2 = 0 changes）。已在 `~/Documents/Aleph/` 实际执行。

#### Curator
- [x] **T4.1** Curator worker（`core/workers/curator.py`）：每次 wiki round 后自动运行 refresh → crosslink。`Curator` class 支持 standalone（`bus=None`）+ dry_run。已接入 IntakeProcessor 作为 Step 5（process_batch → wiki round → embedding sync → curator）。错误 log 但不阻塞 intake pipeline。7 个测试。
- [x] **T4.2** 语义重复检测：`MemPalaceAdapter.check_duplicate()` 扩展 + `core/aleph/pipeline/dedup_pages.py`。扫描 wiki 页面对，过滤自匹配，报告跨页重复候选（`MergeCandidate`）。不自动合并——emit `memory.curated(reason="merge_candidate")`。Curator Step 3（refresh → crosslink → dedup）。12 个新测试。
- [x] **T4.3** 失效模式识别（"我搬家了" / "项目结束了" 类语义）：intake prompt 提取 `invalidations` 字段，`_apply_invalidations` 调用 `adapter.kg_invalidate()` 标记 KG 事实过期，emit `memory.curated(reason="invalidation")`。17 个新测试。
- [x] **T4.4** 调 KG.invalidate — `kg_invalidate()` 加入 MemPalaceAdapter Protocol + InMemory（no-op）+ Stdio 实现。与 T4.3 同步完成。
- [ ] **T4.5** Aleph wiki 页面也能被 invalidate（frontmatter 加 `superseded_by`）
- [x] **T4.6** emit `memory.curated`：`source=worker:curator`，`reason=crosslink` 或 `reason=refresh`，payload 含 pages_modified / links_inserted / paragraphs_removed。dry_run 或无变更时不 emit。

#### Skill 文档
- [x] **T4.7** `skills/itsme/SKILL.md` 主 skill 文档升级 v0.0.4：mode 表格（auto/wiki/verbatim）、双引擎架构说明、hooks 背后流程、curator 维护、failure modes 更新。去掉 v0.0.1 NotImplementedError 引用。
- [x] **T4.8** Trigger guide 在 SKILL.md "When to remember" / "What NOT to remember" / "Anti-patterns" 三节覆盖完整，不独立拆文件。
- [x] **T4.9** Ask 提问范式在 SKILL.md "When to ask" / "Modes" / "Examples" / "When ask returns nothing" 四节覆盖。新增 `mode="auto"` 推荐默认。

#### 体验
- [x] **T4.10** `status(format=feed)` emoji 渲染：📥 raw / 🔀 route / 💾 store / ♻ dedup / 🔗 xlink / 🧹 clean / 🔍 query / 📝 wiki / ⚙ curat。summary line 新增 wiki/xlink/clean 桶。query 现在显示 mode。
- [ ] **T4.11** Observability：events viewer（CLI `itsme events tail`）
- [ ] **T4.12** 失败可观察：worker 异常进 events，不静默
- [ ] **T4.13** 配置热加载（默认阈值、wiki 路径）
- [ ] **T4.14** `## 用户笔记` 区块契约（wiki 中用户保留区域）

---

## v0.0.5+ — 长尾

- [ ] 跨 session 主题聚类（不只是 session 内）
- [ ] KG 推理（A is_friend_of B + B is_at C → A 可能去过 C）
- [ ] 主动召回（"你 30 天前为 X 决策过，是否仍适用？"）
- [ ] wiki 全文索引（Obsidian Dataview / 嵌入式搜索增强）
- [ ] 多 agent / 多用户隔离（`wing_<agent>_<user>`）
- [ ] Aleph wiki 历史版本（git-backed vault）

---

## Critical Path（v0.0.4）

```
T4.0,T4.0b (crosslink + refresh)   ← wiki 自成长
  │
  ▼
T4.1-T4.6 (curator)                ← 去重 / 失效
  │
  ▼
T4.7-T4.9 (skill docs)             ← agent 用得好
  │
  ▼
T4.10-T4.14 (体验打磨)
```

T4.0/T4.0b 与 T4.1-T4.6 两条线可交叉并行。

**v0.0.1 GA 验收必须满足**：

- 三动词 (`remember` / `ask` / `status`) 在 CC 里走通；
- T1.20 smoke（自动 + 手动 runbook）全绿；
- T1.13.5 持久化 backend 可用：操作者通过 `$ITSME_MEMPALACE_BACKEND={stdio,auto}` 切换后，drawer 跨 MCP server 重启可读回（默认 `auto`，先尝试 stdio 再 fallback 到 inmemory，失败显式 — `MemPalaceConnectError` / `MemPalaceWriteError`，不静默吞）；
- `tests/smoke/test_e2e_in_process.py::test_cross_restart_drawer_survives` 验证 drawer 跨重启持久化（mempalace 可选依赖安装后 auto 解析到 stdio backend）。

---

## Pre-flight Checklist

- [x] **Q1** Aleph 现状 → **从 0 自建**
- [x] **Q2** 实现语言 → **Python**
- [x] **Q3** ~~`ask(promote=true)` → **同步**~~ — dropped（2026-05-07）
- [x] **Q4** LLM provider → **Anthropic**（先支持，留抽象层）
- [x] **Q5** Embedding → **本地 sentence-transformers**（v0.0.3）
- [x] **Q6** Hook 触发 → **被动 lifecycle**（SessionEnd=before-exit / PreCompact=before-compact）+ **主动 context-pressure 1 个**（阈值采样，Schmitt-trigger debounce；T1.17b）。CC 无专门的 `/clear` hook，交由 context-pressure 覆盖提前抢救需求。
- [x] **Q7** Vault 默认路径 → `~/Documents/Aleph/`（Obsidian vault，iCloud 同步）。`$ITSME_ALEPH_ROOT`（`$ITSME_ALEPH_VAULT` legacy fallback）
- [x] **Q8** Plugin 安装 → **CC + Codex 双套**，分别按各自规范打包

全部已敲定 ✅

---

## Risk Register

| ID | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | hook 触发频繁导致 events 爆炸 | 性能 / 噪音 | ring buffer 上限 + router 静默规则（T2.0，v0.0.2） |
| R2 | LLM 提炼不稳定，wiki 质量差 | 用户失望 | promoter 加 self-eval + 人工纠错入口 |
| R3 | 与 MemPalace 既有用户的 wing 命名冲突 | 数据错乱 | itsme 默认 namespace 隔离（`wing_itsme_*`） |
| R4 | Aleph wiki 损坏 / 误删 | 记忆丢失 | 每次写前 git commit；定期备份 |
| R5 | ~~`ask(promote)` 滥用导致 LLM 成本失控~~ | ~~钱包~~ | ~~rate limit + 显式开关~~ — **dropped: promote 已取消** |
| R6 | Aleph 自建工期超估 | 上线推迟 | v0.0.2 砍 merge/crosslink/embedding，先粗放写入 |
| R7 | LLM 跨 provider 行为不一致 | wiki 质量参差 | provider 抽象层 + 固定 model 版本（不追新） |

