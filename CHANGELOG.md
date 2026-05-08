# Changelog

All notable changes to **itsme** are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/).
Versions follow [PEP 440](https://peps.python.org/pep-0440/).

---

## [0.0.4] — 2026-05-08

Wiki 自成长：crosslink 回填 + refresh 去重 + curator 自动维护。

### Added
- **Crosslink pipeline** (`core/aleph/pipeline/crosslink.py`): 全量扫描 wiki body，自动插入 `[[wikilink]]` 反向链接。first-occurrence-only、never-self-link、skip protected zones（fenced code / inline code / existing wikilinks / callouts）、longest-match-first（CJK 感知）、shield new links、idempotent。
- **Refresh pipeline** (`core/aleph/pipeline/refresh.py`): 确定性去重（无 LLM）——精确重复段落移除（whitespace-normalized）、History 条目去重、空行折叠。Protected blocks 永不去重。
- **Curator worker** (`core/workers/curator.py`): 每次 wiki round 后自动运行 refresh → crosslink，emit `memory.curated` 事件（`reason=crosslink` / `reason=refresh`）。支持 standalone + dry_run。已接入 IntakeProcessor 作为 Step 5。
- **Status feed emoji**: 📥 raw / 🔀 route / 💾 store / ♻ dedup / 🔗 xlink / 🧹 clean / 🔍 query / 📝 wiki / ⚙ curat。summary line 新增 wiki/xlink/clean 桶。
- `wiki.promoted` 事件在 feed 中渲染。
- `Aleph.extract_body()` 提升为公共 API。

### Changed
- **SKILL.md** 全面重写 v0.0.4：mode 表格（auto/wiki/verbatim）、双引擎架构说明、hooks 流程、curator 维护、failure modes。
- `memory.curated` 事件 source 扩展：`router` + `worker:curator`。
- `_feed_summary_line` 按 `reason` 分桶（dedup / xlink / clean 独立计数）。
- query feed line 现在显示 `mode`。

### Fixed
- Crosslink 幂等性：pre-scan 已有 `[[slug...]]` link，跳过已链接 target 的所有剩余纯文本出现。
- Multi-line Obsidian callout 正则：`> [!info]\n> line1\n> line2` 整体保护。
- `_dedup_history` 在遇到下一个 `##` section 时停止，不再误删 History 之后的列表项。

---

## [0.0.3] — 2026-05-07

Search 质量提升 + embedding 混合检索。

### Added
- **Wiki embedding via MemPalace**: wiki pages 同步到 MemPalace `aleph` wing，`dual_search` 增加 embedding 搜索腿（三条腿：keyword + embedding + raw）。
- 启动时 bootstrap `sync_all_wiki_pages()` 索引现有页面。
- `mempalace` 可选依赖：`pip install itsme[mempalace]`。

### Changed
- **SQLite FTS5 extraction index 移除**：wiki pages + MemPalace raw 双引擎覆盖原 per-turn extraction 用例。
- ARCHITECTURE.md 全面重写：与 v0.0.3 现实对齐。

### Fixed
- **CJK wiki keyword search** (#28): `search()` 的 `query.lower().split()` 改为 CJK 逐字切分 + Latin 整 token 混合。
- **Score 归一化** (#31): 处理 MemPalace v3.3+ BM25-only 模式返回 `None` similarity + 旧版负值。

---

## [0.0.2] — 2026-05-06

Intake + Aleph Wiki pipeline：hook 捕获经 LLM 提取，写入 MemPalace raw + Obsidian wiki。

### Added
- **LLM intake processor** (`core/workers/intake.py`): 异步消费 hook 捕获的 `raw.captured`，LLM 提取 keep/skip + summary/entities/claims，全量写 MemPalace，keep turns 写 wiki。
- **Aleph wiki adapter** (`core/aleph/wiki.py`): Obsidian vault 读写，YAML frontmatter 解析，CJK 分词搜索。
- **AlephRound** (`core/aleph/round.py`): LLM 驱动的对话→wiki 页面 create/update。
- **LLM provider 抽象** (`core/llm.py`): `LLMProvider` protocol + `DeepSeekProvider`。
- `ask(mode=auto)` 双引擎搜索：Aleph wiki + MemPalace raw → 合并去重。
- `ask(mode=wiki)` Aleph wiki 页面搜索。
- Structural strip (`core/filters/envelope.py`): 去除 CC envelope boilerplate。
- Turn slicing: hook 捕获从整段 transcript → 按 user/assistant turn 切分。
- Wiki 自动发现：`$ITSME_ALEPH_ROOT` / `~/Documents/Aleph/`。

### Changed
- `ask(mode=auto)` 成为推荐默认模式。
- LLM 降级：API 不可用时 raw 直存 MemPalace，不写 Aleph，不阻塞。

---

## [0.0.1] — 2026-05-05

端到端骨架：3 个 MCP 动词 + MemPalace + hooks。

### Added
- **EventBus**: sqlite ring buffer（500 条），ULID，6 个事件类型。
- **MCP surface**: `remember(content, kind?)` / `ask(question, mode?)` / `status(scope?, format?)`。
- **MemPalace adapter**: `InMemoryMemPalaceAdapter` + `StdioMemPalaceAdapter`（持久化 subprocess）。
- **Router worker**: 规则路由（kind + 关键词推断），sync fast-path。
- **Content-hash dedup** (T1.19): 跨 producer 去重，`memory.curated(reason=dedup)`。
- **CC hooks**: `before-exit` / `before-compact`（lifecycle）+ `context-pressure`（主动式，Schmitt-trigger debounce）。
- **Status feed** (T1.22): `format='feed'` 人类可读，per-event-type 渲染。
- **CC marketplace install**: `/plugin marketplace add coxon/itsme`。
- SKILL.md agent guidance。

---

[0.0.4]: https://github.com/coxon/itsme/compare/e7caf30...cb1353d
[0.0.3]: https://github.com/coxon/itsme/compare/90f237a...e7caf30
[0.0.2]: https://github.com/coxon/itsme/compare/14fb515...90f237a
[0.0.1]: https://github.com/coxon/itsme/compare/c888822...14fb515
