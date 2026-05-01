# itsme — Roadmap & Task Breakdown

> Status: **Design draft** · v0.0.x
> Repo: <https://github.com/coxon/itsme>
> Language: **Python**
> Last updated: 2026-04-30

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
- ✅ MemPalace 适配：**Protocol + InMemory 参考实现**（v0.0.1，见 `core/adapters/mempalace.py`）；stdio MCP-client backend 推迟到 T1.13.5
- ✅ MCP server 框架：**FastMCP + stdio**（mcp Python SDK 1.27+）
- ✅ Router 策略：v0.0.1 **规则路由** — `kind` 直查 + 关键词推断（decision/todo/feeling/event）+ fallback general，**不引入 LLM**；LLM 路由推迟到 v0.0.2 配合 promoter 一并落地

---

## Milestones at a glance

| 版本 | 主题 | 关键交付 | 估算 |
|---|---|---|---|
| **v0.0.1** | 端到端骨架 | hook → events → router → MemPalace → ask 直查 MP，能装进 CC | ~1.5 周 |
| **v0.0.2** | Aleph MVP | 自建 Aleph 模块（extract+write+search），promoter 跑通，wiki 落 vault | ~3-4 周 |
| **v0.0.3** | 反向升格 | `ask(promote=true)` 融合 + 写回；merge / crosslink pipeline | ~2 周 |
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
- [x] **T1.14** wing/room 命名规范（itsme 默认用 `wing_<project>` / `room_<topic>`，namespace 隔离）

#### P0 — Worker
- [x] **T1.15** router worker（规则路由，仅靠 `kind` 与简单关键词；sync fast-path + async consume loop，参见 `core/workers/router.py`）
- [x] **T1.16** worker 调度方式：`WorkerScheduler` — 后台线程独立 asyncio loop（不复用 FastMCP loop，避免 head-of-line blocking）

#### P0 — Hook
- [x] **T1.17** CC hook 脚本：`hooks/hooks.json` + `hooks/cc/before-exit.sh` / `before-compact.sh`（CC SessionEnd / PreCompact 触发）。Python 实现：`itsme.hooks.lifecycle`，读 `transcript_path` JSONL 取 tail（默认 10K chars），emit `raw.captured` with `source=hook:before-<x>` + `transcript_ref`。
- [x] **T1.17b** **Context-pressure hook**（主动式）：CC `UserPromptSubmit` / `PostToolUse` 触发，读 `transcript_path` 估 tokens（`chars/4`），跨阈值（默认 0.70，可配 `$ITSME_CTX_THRESHOLD` / `$ITSME_CTX_MAX`）emit `raw.captured` with `source=hook:context-pressure` + `transcript_ref`。Schmitt-trigger debounce：触发后须 pressure 跌 ≥10% (`disarm_drop`) 才重新 arm，状态持久化到 `~/.itsme/state/pressure-<sid>.json`。比 `before-compact` 早，抢救窗口大（v0.0.2 由 Aleph promoter 消费）。
- [ ] **T1.18** Codex hook 适配（先调研 Codex 的 hook 接口，按其规范实现）
- [ ] **T1.19** hook 与 explicit remember 的去重标记

#### P1 — 验收
- [ ] **T1.20** Smoke test：CC 装载、跑一段对话、SessionEnd / PreCompact / context-pressure 触发 hook → 检查 MemPalace 是否落库
- [ ] **T1.21** Codex 装载同样验证
- [ ] **T1.22** `status()` 能在 IDE 里显示最近 N 条事件

**v0.0.1 完成定义**：在 CC（或 Codex）里聊一段 → SessionEnd / PreCompact / context-pressure 触发 → MP 里看到 drawer → `ask` 能查回来。

---

## v0.0.2 — Aleph MVP（从 0 自建）

**目标**：写出 Aleph 进程内模块，能 extract、能 write entry、能 search、能被 promoter 调度。session 结束自动把 raw 蒸馏到 wiki，落 Obsidian vault。`ask(mode=auto)` 先 wiki 后 verbatim。

> ⚠️ 这是工期最大的一版。Aleph 从 0 写，含 LLM pipeline、vault 读写、混合检索。

### Tasks

#### Aleph 核心 — 数据模型 & 存储
- [ ] **T2.1** `core/aleph/types.py`：`WikiEntry` / `Claim` / `Reference` 数据类
- [ ] **T2.2** `core/aleph/store/vault.py`：Markdown frontmatter 解析与写入（用 `python-frontmatter`）
- [ ] **T2.3** `core/aleph/store/index.py`：sqlite 索引表（id / title / type / path / refs / updated_at）
- [ ] **T2.4** Vault 初始化：默认路径 + 目录骨架（people/projects/decisions/...）
- [ ] **T2.5** Entry 文件命名 slug 规则 + 冲突解决

#### Aleph 核心 — Pipeline (MVP 版)
- [ ] **T2.6** `core/llm.py`：Provider 抽象（先实现一个：Anthropic 或 OpenAI）
- [ ] **T2.7** `core/aleph/prompts/extract.md`：raw → claims/entities prompt 模板
- [ ] **T2.8** `core/aleph/pipeline/extract.py`：调 LLM，解析输出
- [ ] **T2.9** `core/aleph/pipeline/route.py`：v0.0.2 简化版 — 仅按 type + 标题相似度匹配 existing entry
- [ ] **T2.10** v0.0.2 **不实现** merge / crosslink，新 raw 都开新 entry（粗放但可工作）
- [ ] **T2.11** `core/aleph/api.py`：对内 SDK — `write(raw_batch)` / `search(q)` / `get(ref)`

#### Aleph 核心 — 搜索
- [ ] **T2.12** `core/aleph/search.py`：v0.0.2 **关键词版本** — 标题 + tags 全文匹配
- [ ] **T2.13** Embedding 推迟到 v0.0.3（避免提前引入依赖）

#### promoter worker
- [ ] **T2.14** consolidation 边界监听（消费 `raw.captured` with `source=hook:before-exit`/`hook:before-compact`/`hook:context-pressure`；参见 T1.17/T1.17b）
- [ ] **T2.15** 拉取本次抢救范围内的 `memory.stored` 列表
- [ ] **T2.16** 主题聚类：v0.0.2 简化版 — 按 wing/room 分组
- [ ] **T2.17** 调 `aleph.api.write(raw_batch)` per group
- [ ] **T2.18** emit `wiki.promoted`

#### MCP 升级
- [ ] **T2.19** `ask(mode=auto)` 路由：Aleph 优先 → miss 回退 MemPalace
- [ ] **T2.20** `ask(mode=wiki)` / `mode=verbatim` 单引擎查询
- [ ] **T2.21** `sources[]` 字段填充（mp / aleph 双类型）
- [ ] **T2.22** Hook → promoter 接线：CC 的 SessionEnd / PreCompact / context-pressure 产生的 `raw.captured` 批量喂给 Aleph；Codex 同理

#### 验收
- [ ] **T2.23** 一个完整 session：聊 → 退出 → vault 出现新 .md → ask 命中 wiki
- [ ] **T2.24** 能在 Obsidian 打开 vault，看到 frontmatter + 章节渲染正常
- [ ] **T2.25** `itsme.hooks._common._iter_transcript_texts` 改成尾部增量读（从 EOF 往回 seek + 按块解析完整 JSONL 行），替换当前 `read_text()` 整文件方案。v0.0.1 transcripts 小，不痛；v0.0.2 接 Aleph promoter 后吞吐变大再优化。

---

## v0.0.3 — 反向升格 + Pipeline 加厚

**目标**：`ask(promote=true)` 触发即时融合写回；Aleph 学会 merge / crosslink；引入 embedding 搜索。

### Tasks

#### Aleph Pipeline — merge / crosslink / refresh
- [ ] **T3.1** `core/aleph/prompts/merge.md`：老 entry + 新 raw → 更新后的 entry
- [ ] **T3.2** `core/aleph/pipeline/merge.py`
- [ ] **T3.3** `core/aleph/pipeline/crosslink.py`：扫描 entry body，自动插入 `[[wikilink]]`
- [ ] **T3.4** `core/aleph/pipeline/refresh.py`：去重段落、清冗余
- [ ] **T3.5** route.py 升级：能识别 "应并入 existing entry" vs "应建新 entry"

#### Aleph 搜索升级
- [ ] **T3.6** Embedding provider 抽象（local sentence-transformers / 远程 API 可切）
- [ ] **T3.7** Body chunking 策略
- [ ] **T3.8** `core/aleph/search.py` 升级为混合检索（keyword + embedding）

#### `ask(promote=true)`
- [ ] **T3.9** reader 升级：并行拉 MP + Aleph
- [ ] **T3.10** `core/aleph/prompts/fuse.md`：老 wiki + 新原料 + 提问视角 → 新 wiki
- [ ] **T3.11** sync 实现（v0.0.3 默认 sync，参见 Open Q1）
- [ ] **T3.12** 写回 Aleph + emit `wiki.promoted`
- [ ] **T3.13** 返回 `promoted=true` + `promotion_event_id`

#### 验收
- [ ] **T3.14** 同一问题问两次：第二次 wiki 比第一次更精炼
- [ ] **T3.15** vault 中的 entry 含真实 `[[wikilink]]` 双向链接（Obsidian Graph view 可用）

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

## Critical Path（v0.0.1）

```
T1.1 ─► T1.5,T1.6 ─► T1.9,T1.10,T1.11,T1.12 ─► T1.13 ─► T1.15 ─► T1.17,T1.17b ─► T1.20
        (events)     (MCP surface)               (adapter) (router) (CC hooks)      (smoke)
```

T1.14 / T1.18 (Codex) / T1.19 / T1.21 / T1.22 与主路径并行。

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
| R1 | hook 触发频繁导致 events 爆炸 | 性能 / 噪音 | ring buffer 上限 + router 静默规则 |
| R2 | LLM 提炼不稳定，wiki 质量差 | 用户失望 | promoter 加 self-eval + 人工纠错入口 |
| R3 | 与 MemPalace 既有用户的 wing 命名冲突 | 数据错乱 | itsme 默认 namespace 隔离（`wing_itsme_*`） |
| R4 | Aleph vault 损坏 / 误删 | 记忆丢失 | 每次写前 git commit；定期备份 |
| R5 | `ask(promote)` 滥用导致 LLM 成本失控 | 钱包 | rate limit + 显式开关 |
| R6 | Aleph 自建工期超估 | 上线推迟 | v0.0.2 砍 merge/crosslink/embedding，先粗放写入 |
| R7 | LLM 跨 provider 行为不一致 | wiki 质量参差 | provider 抽象层 + 固定 model 版本（不追新） |

