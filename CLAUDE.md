# CLAUDE.md

> 本文件指导 Claude Code（及任何 IDE 内 agent）在本仓库的工作方式。
> **每次 session 开始时必读**。

---

## Project at a glance

**itsme** — 给 agent IDE（Claude Code · Codex）提供长期记忆的 plugin。

- **状态**：design phase · v0.0.x · 尚无实现代码
- **语言**：Python（待启动）
- **入口文档**：
  - 架构：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
  - 路线图：[docs/ROADMAP.md](docs/ROADMAP.md)
  - 安装矩阵：[docs/INSTALL.md](docs/INSTALL.md)
  - 协作规范：[CONTRIBUTING.md](CONTRIBUTING.md)

> 任何架构问题先查 ARCHITECTURE.md，再问用户。

---

## ⛔ 硬性约束 — 必须遵守

### 1. 分支与 PR

- ❌ **NEVER 直接 commit 到 `main`**
- ❌ **NEVER 直接 push 到 `main`**
- ❌ **NEVER `git push --force` 到任何远程分支**（除非用户在当前 session 显式授权）
- ✅ 所有改动 → `feature/<area>-<desc>` 分支 → 走 PR 合回 `main`
- ✅ PR 标题用 Conventional Commits 格式（`feat(x): ...` / `fix(x): ...` / `docs(x): ...`）

### 2. 提交粒度

- 小步提交，每个 commit 单一职责
- commit message 用 Conventional Commits
- ❌ 不要把无关改动塞进同一个 commit

### 3. 文档同步

- 改架构 → 必须同步更新 `docs/ARCHITECTURE.md`
- 改任务 → 必须同步更新 `docs/ROADMAP.md`
- 改安装方式 → 必须同步更新 `docs/INSTALL.md`

### 4. 与用户协作

- 重大改动（新增模块、改公共 API、删除文件）**先问，再动**
- 设计阶段以**对话+文档**为主，不要擅自写实现代码
- 进入 v0.0.1 实现阶段后才开始写业务代码

### 5. 不擅自做的事

- 不主动 commit（除非用户说"commit"或同等指令）
- 不主动 push（除非用户说"push"或同等指令）
- 不主动开 PR（除非用户说"开 PR"或同等指令）
- 不主动安装新依赖（先在 PR 描述里列明）

---

## ✅ 推荐工作流

```
用户提出需求
   ▼
[设计阶段] 在对话里讨论 → 用户拍板
   ▼
git checkout -b feature/<area>-<desc>   ← 离开 main
   ▼
改文件（代码 / 文档）
   ▼
小步 commit（Conventional Commits 风格）
   ▼
等用户说 "push" / "开 PR"
   ▼
git push -u origin feature/<area>-<desc>
gh pr create --base main --title "..." --body "..."
   ▼
等 review（用户或 Claude bot）
   ▼
合并 → 回到 main 拉新代码
```

---

## Repo conventions

### 目录约定（设计阶段已定，实现时遵循）

```
itsme/
├── mcp/                  # 对外 3 个 MCP 动词：remember / ask / status
├── skills/               # 给 agent 装载的剧本
├── hooks/                # 各 IDE 的 hook 脚本（cc/ codex/）
├── core/                 # 内部引擎（agent 不可见）
│   ├── events/           # EventBus（sqlite ring）
│   ├── workers/          # router / promoter / curator / reader
│   ├── adapters/         # mempalace.py
│   ├── aleph/            # 自建的 wiki 管理员模块
│   └── llm.py            # provider 抽象
├── config/
├── docs/
└── tests/
```

### 命名

- wing：`wing_itsme_<project>` — 与其他 MemPalace 用户隔离
- room：`room_<topic>` — 主题级
- event type：用现有的 6 种（`raw.captured` / `memory.stored` / `memory.routed` / `wiki.promoted` / `memory.curated` / `memory.queried`）— 加新类型前先讨论

### 导入秘钥

- Anthropic API key：放 `.env`（已在 `.gitignore`），变量名 `ANTHROPIC_API_KEY`
- ❌ 不要把 key 写进任何 committed 文件

---

## Useful commands（实现阶段补全）

```bash
# 安装（启动开发）
git config core.hooksPath .githooks      # 启用本地 hook
uv sync                                   # 装依赖（v0.0.1 起）

# 检查
ruff check .
ruff format --check .
mypy .
pytest                                    # v0.0.1 起

# 运行
python -m itsme.mcp.server                # 起 MCP server（v0.0.1 起）
```

---

## 当前已锁的设计决策（不要再发明替代方案）

- ✅ Aleph 从 0 自建（进程内模块，非外部服务）
- ✅ 语言：Python
- ✅ `ask(promote=true)`：同步（并行 fetch + 融合 + 返回）
- ✅ LLM provider：Anthropic（先支持，留抽象层）
- ✅ Embedding：本地 sentence-transformers（v0.0.3 启用）
- ✅ Hook：`before-exit` / `before-clear` / `before-compact`（consolidation 边界，非 per-turn）
- ✅ 多 IDE：先支持 CC + Codex
- ✅ 分支：当前简化（main + feature/*），1.0 后升级 git-flow

---

## 当人有疑问时

1. 先查 `docs/ARCHITECTURE.md` 对应章节
2. 查 `docs/ROADMAP.md` 看是否在 backlog
3. 都没有 → 在对话里问用户

不要自作主张地做"小决定"。

