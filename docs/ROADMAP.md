# itsme — Roadmap & Task Breakdown

> Status: **Design draft** · v0.0.x
> Repo: <https://github.com/coxon/itsme>
> Language: **Python**
> Last updated: 2026-05-07

策略：**端到端最薄一刀** 先打通，再逐步加厚。

---

## Locked decisions

- ✅ Aleph：**从 0 自建**（进程内模块，见 ARCHITECTURE §7.2）
- ✅ 语言：**Python**
- ✅ 起步版本：**v0.0.1**
- ✅ `ask(promote=true)`：**同步**（并行 fetch + 融合 + 返回，副作用写回）
- ✅ LLM provider：**Anthropic**（v0.0.x 先支持，provider 抽象层为未来留口）
- ✅ Embedding：**本地 sentence-transformers**（v0.0.3 启用）
- ✅ Hook 触发：**被动 lifecycle**（SessionEnd=before-exit / PreCompact=before-compact）+ **主动 context-pressure**（UserPromptSubmit / PostToolUse，阈值采样 + Schmitt-trigger debounce）
- ✅ 多 IDE：**先支持 CC + Codex**，安装方式分别打包
- ✅ 仓库管理：**简化 gitflow**（main + feature/*；squash merge；1.0 后再升级到完整 git-flow，见 CONTRIBUTING.md）
- ✅ 包布局：**src-layout**（`src/itsme/`，避免与 MCP SDK 命名冲突，见 ARCHITECTURE §9）
- ✅ Vault 默认路径：`~/Documents/itsme/`（与现有 `~/Documents/Aleph/` 同级）
- ✅ MemPalace 适配：**Protocol + InMemory 参考实现**（`core/adapters/mempalace.py`）+ **stdio MCP-client backend 已落地**（`core/adapters/mempalace_stdio.py`，T1.13.5，v0.0.1 GA），由 `$ITSME_MEMPALACE_BACKEND={inmemory,stdio,auto}` 切换
- ✅ MCP server 框架：**FastMCP + stdio**（mcp Python SDK 1.27+）
- ✅ Router 策略：v0.0.1 **规则路由** — `kind` 直查 + 关键词推断（decision/todo/feeling/event）+ fallback general，**不引入 LLM**；LLM 路由推迟到 v0.0.2 配合 promoter 一并落地
- ✅ LLM 模型：统一用 **DeepSeek**（`deepseek-chat`，`$ITSME_LLM_MODEL` 可覆盖）；API key 通过 `$DEEPSEEK_API_KEY` 配置
- ✅ Aleph v0.0.2 形态：**per-turn extraction index**（sqlite + FTS5），不含 wiki consolidation / vault 写入 / merge / crosslink。完整 Aleph pipeline 推迟到 v0.0.3
- ✅ ask 搜索策略：**双引擎并行**（Aleph structured + MemPalace raw）→ 合并去重返回。结构化层是**搜索增强器，不是替代器**——LLM 提取遗漏时 MemPalace raw 兜底，永远不漏
- ✅ Intake 运行位置：**router 异步 consume loop**（不阻塞 hook 进程）；explicit `remember()` 不走 intake，保持同步 fast-path
- ✅ LLM 降级策略：API 不可用时 raw 直存 MemPalace（同 v0.0.1 行为），不写 Aleph，不阻塞

---

## Milestones at a glance

| 版本 | 主题 | 关键交付 | 估算 |
|---|---|---|---|
| **v0.0.1** | 端到端骨架 | hook → events → router → MemPalace → ask 直查 MP，能装进 CC | ~1.5 周 |
| **v0.0.2** | Intake + Aleph Index | LLM intake → per-turn extraction index + MemPalace raw → `ask(mode=auto)` 双引擎搜索 | ~3-4 周 |
| **v0.0.3** | Wiki Consolidation | promoter → wiki entry → vault；embedding 搜索；`ask(promote=true)` 反向升格；merge / crosslink | ~3-4 周 |
| **v0.0.4** | Curator + 体验 | 去重、KG 失效、skill 文档完整、status feed 渲染 | ~1.5 周 |
| **v0.0.5+** | 长尾 | 跨 session 主题聚类、主动召回、KG 推理、用户笔记区块契约 | — |

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

## v0.0.2 — Intake + Aleph Extraction Index

**目标**：hook 捕获经 LLM intake 提取结构化数据（summary/entities/claims），存入 Aleph extraction index（sqlite + FTS5）。MemPalace 继续存 raw 原文（per-turn drawer，不再是 2000 token blob）。`ask(mode=auto)` 双引擎搜索：Aleph 结构化（高精度）+ MemPalace raw（高召回），合并去重。

> **设计原则**：MemPalace 是"什么都记"的原料仓——搜索面是 raw 全文。Aleph extraction index 是 LLM 提取的结构化搜索增强层。`ask` 搜两路合并，**结构化层漏提取时 MemPalace raw 兜底，永远不丢**。

> **成本**：Intake 用 Sonnet 4.6（统一模型，`$ITSME_LLM_MODEL` 可覆盖），v0.0.3+ 视成本按需拆分。

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

- [x] **T2.19** `ask(mode=auto)` **双引擎搜索**：并行查 `Aleph.search(q)` + `MemPalace.search(q)` → 合并去重（同 `turn_id` / `raw_event_id` 的结果合并为一条，Aleph 命中排前） → 返回。**这是 v0.0.2 的 ask 默认模式**。
- [x] **T2.20** `ask(mode=verbatim)` MemPalace only（行为不变）。
- [x] **T2.21** `ask(mode=wiki)` → `NotImplementedError`（wiki entry 在 v0.0.3）。

#### 验收

- [x] **T2.23** 端到端：聊一段 → `/exit` → intake 提取 → Aleph index 有记录 + MemPalace 有 per-turn drawer → `ask(mode=auto)` 命中 Aleph 精准 hit + MemPalace 兜底 hit。
- [x] **T2.24** **Aleph 漏提取回归测试**：构造 LLM intake 未提取的实体（e.g. 一句话中的次要地名），验证 MemPalace raw search 仍能命中。Pin 为 fixture。
- [x] **T2.25** **LLM 降级测试**：API key 未配 / API 不可用时 hook capture 仍正常存入 MemPalace，`ask(mode=verbatim)` 正常返回，不报错。
- [x] **T2.26** `status(format=feed)` 能看到 `raw.triaged` 事件（skip/keep 可观察）。

**v0.0.2 完成定义**：hook capture → structural strip → turn slice → LLM intake → Aleph index + MemPalace 双存 → `ask(mode=auto)` 双路搜索命中。LLM 挂了也不丢数据。

---

## v0.0.3 — Wiki Consolidation + Promoter

**目标**：Aleph 从 per-turn extraction 升级到跨 session wiki consolidation。Promoter 在 session 边界把 extraction index 聚类 → LLM 融合 → wiki entry → Obsidian vault。`ask(promote=true)` 反向升格。引入 embedding 搜索。

### Tasks

#### Aleph Wiki — 数据模型 & 存储
- [ ] **T3.1** `core/aleph/types.py`：`WikiEntry` / `Claim` / `Reference` 数据类
- [ ] **T3.2** `core/aleph/store/vault.py`：Markdown frontmatter 解析与写入（用 `python-frontmatter`）
- [ ] **T3.3** Vault 初始化：默认路径 `~/Documents/itsme/` + 目录骨架（people/projects/decisions/...）
- [ ] **T3.4** Entry 文件命名 slug 规则 + 冲突解决

#### Aleph Pipeline — consolidation / merge / crosslink
- [ ] **T3.5** `core/aleph/pipeline/consolidate.py`：从 extraction index 按 entity 聚类 + LLM 融合 → wiki entry
- [ ] **T3.6** `core/aleph/pipeline/merge.py`：老 entry + 新 extractions → 更新后的 entry
- [ ] **T3.7** `core/aleph/pipeline/crosslink.py`：扫描 entry body，自动插入 `[[wikilink]]`
- [ ] **T3.8** `core/aleph/pipeline/refresh.py`：去重段落、清冗余

#### Promoter Worker
- [ ] **T3.9** promoter worker：session 边界触发（消费 `hook:before-exit` / `hook:before-compact` / `hook:context-pressure`），读 Aleph extraction index → 主题聚类 → LLM consolidation (Sonnet 4.6) → wiki entry → vault
- [ ] **T3.10** emit `wiki.promoted`

#### Aleph 搜索升级
- [ ] **T3.11** Embedding provider 抽象（local sentence-transformers / 远程 API 可切）
- [ ] **T3.12** Body chunking 策略
- [ ] **T3.13** `core/aleph/search.py` 升级为混合检索（FTS5 + embedding）
- [ ] **T3.14** wiki entry 也进搜索索引

#### `ask(promote=true)`
- [ ] **T3.15** reader 升级：并行拉 MemPalace + Aleph（extractions + wiki）
- [ ] **T3.16** `core/aleph/prompts/fuse.md`：老 wiki + 新原料 + 提问视角 → 新 wiki
- [ ] **T3.17** sync 实现（v0.0.3 默认 sync）
- [ ] **T3.18** 写回 Aleph + emit `wiki.promoted`
- [ ] **T3.19** 返回 `promoted=true` + `promotion_event_id`

#### 验收
- [ ] **T3.20** 一个完整 session：聊 → 退出 → vault 出现新 .md → Obsidian 可读
- [ ] **T3.21** 同一问题问两次：第二次 wiki 比第一次更精炼
- [ ] **T3.22** vault 中的 entry 含真实 `[[wikilink]]` 双向链接（Obsidian Graph view 可用）
- [ ] **T3.23** `ask(mode=wiki)` 命中 wiki entry

---

## v0.0.4 — Curator + Skill 与体验打磨

**目标**：去重失效跑起来；agent 真的"用得好" itsme，而不是"能调用" itsme。

### Tasks

#### curator
- [ ] **T4.1** 定时任务调度（每 N 分钟 / 每 session 末）
- [ ] **T4.2** 重复检测（依赖 MemPalace 已有 `check_duplicate`）
- [ ] **T4.3** 失效模式识别（"我搬家了" / "项目结束了" 类语义）
- [ ] **T4.4** 调 KG.invalidate
- [ ] **T4.5** Aleph entry 也能被 invalidate（frontmatter 加 `superseded_by`）
- [ ] **T4.6** emit `memory.curated`

#### Skill 文档
- [ ] **T4.7** `skills/itsme.md` 主 skill 文档（角色、能力、边界）
- [ ] **T4.8** `skills/triggers.md`：哪些场景必 remember、哪些别记
- [ ] **T4.9** `skills/usage.md`：ask 的提问范式、何时用 promote

#### 体验
- [ ] **T4.10** `status(format=feed)` 渲染（emoji / 折叠 / 时间线）
- [ ] **T4.11** Observability：events viewer（CLI `itsme events tail`）
- [ ] **T4.12** 失败可观察：worker 异常进 events，不静默
- [ ] **T4.13** 配置热加载（默认阈值、vault 路径）
- [ ] **T4.14** `## 用户笔记` 区块契约（vault 中用户保留区域）

---

## v0.0.5+ — 长尾

- [ ] 跨 session 主题聚类（不只是 session 内）
- [ ] KG 推理（A is_friend_of B + B is_at C → A 可能去过 C）
- [ ] 主动召回（"你 30 天前为 X 决策过，是否仍适用？"）
- [ ] vault 全文索引（Obsidian Dataview / 嵌入式搜索增强）
- [ ] 多 agent / 多用户隔离（`wing_<agent>_<user>`）
- [ ] Aleph entry 历史版本（git-backed vault）

---

## Critical Path（v0.0.2）

```
T2.0a (envelope strip) ─┐
T2.0b (turn slice)      ─┤── 并行，无 LLM 依赖
T2.6  (LLM provider)   ─┘
         │
         ▼
T2.1,T2.2,T2.3 (Aleph extraction index + search + API)
         │
         ▼
T2.0d (LLM intake processor) ── 依赖 T2.0a + T2.0b + T2.6 + T2.1-T2.3
         │
         ▼
T2.19 (ask mode=auto 双引擎搜索)
         │
         ▼
T2.23-T2.26 (验收)
```

T2.0a / T2.0b / T2.6 三条线**并行开发**，在 T2.0d 汇合。

**v0.0.1 GA 验收必须满足**：

- 三动词 (`remember` / `ask` / `status`) 在 CC 里走通；
- T1.20 smoke（自动 + 手动 runbook）全绿；
- T1.13.5 持久化 backend 可用：操作者通过 `$ITSME_MEMPALACE_BACKEND={stdio,auto}` 切换后，drawer 跨 MCP server 重启可读回（默认 `auto`，先尝试 stdio 再 fallback 到 inmemory，失败显式 — `MemPalaceConnectError` / `MemPalaceWriteError`，不静默吞）；
- `tests/smoke/test_e2e_in_process.py::test_cross_restart_drawer_loss_v001_known_gap` 在默认翻 `auto` 时会自动翻红，强制 docs/ROADMAP 同步更新。

---

## Pre-flight Checklist

- [x] **Q1** Aleph 现状 → **从 0 自建**
- [x] **Q2** 实现语言 → **Python**
- [x] **Q3** `ask(promote=true)` → **同步**（并行 fetch + 融合 + 返回）
- [x] **Q4** LLM provider → **Anthropic**（先支持，留抽象层）
- [x] **Q5** Embedding → **本地 sentence-transformers**（v0.0.3）
- [x] **Q6** Hook 触发 → **被动 lifecycle**（SessionEnd=before-exit / PreCompact=before-compact）+ **主动 context-pressure 1 个**（阈值采样，Schmitt-trigger debounce；T1.17b）。CC 无专门的 `/clear` hook，交由 context-pressure 覆盖提前抢救需求。
- [x] **Q7** Vault 默认路径 → `~/Documents/itsme/`（与现有 `~/Documents/Aleph/` 同级）
- [x] **Q8** Plugin 安装 → **CC + Codex 双套**，分别按各自规范打包

全部已敲定 ✅

---

## Risk Register

| ID | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | hook 触发频繁导致 events 爆炸 | 性能 / 噪音 | ring buffer 上限 + router 静默规则（T2.0，v0.0.2） |
| R2 | LLM 提炼不稳定，wiki 质量差 | 用户失望 | promoter 加 self-eval + 人工纠错入口 |
| R3 | 与 MemPalace 既有用户的 wing 命名冲突 | 数据错乱 | itsme 默认 namespace 隔离（`wing_itsme_*`） |
| R4 | Aleph vault 损坏 / 误删 | 记忆丢失 | 每次写前 git commit；定期备份 |
| R5 | `ask(promote)` 滥用导致 LLM 成本失控 | 钱包 | rate limit + 显式开关 |
| R6 | Aleph 自建工期超估 | 上线推迟 | v0.0.2 砍 merge/crosslink/embedding，先粗放写入 |
| R7 | LLM 跨 provider 行为不一致 | wiki 质量参差 | provider 抽象层 + 固定 model 版本（不追新） |

