# itsme

> Long-term memory plugin for agent IDEs (Claude Code · Codex).
>
> **Status**: design draft · v0.0.x — no implementation yet.

---

## What is this

`itsme` 给 agent 一对**长期记忆**。内部双引擎：

- **MemPalace** — 海马体，verbatim 全量原料 + 知识图谱
- **Aleph** — 新皮层，LLM 驱动的 wiki 管理员，落 Obsidian Vault

agent 只看到 **3 个 MCP 动词**：

```
remember(content, kind?)            # 记一笔
ask(question, mode?, promote?)      # 问回来；promote=true 触发融合升格
status(scope?, format?)             # 看自己最近在想什么
```

加 **3 个 consolidation hook**（在上下文即将丢失时抢救固化）：

```
before-exit   · before-clear   · before-compact
```

---

## Design docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 完整架构（双引擎、EventBus、Aleph pipeline、关键流）
- [docs/ROADMAP.md](docs/ROADMAP.md) — milestone & 任务分解（v0.0.1 → v0.0.5+）
- [docs/INSTALL.md](docs/INSTALL.md) — 各 IDE 安装矩阵（CC · Codex）
- [CONTRIBUTING.md](CONTRIBUTING.md) — git-flow 分支模型、提交规范

---

## Status

| 版本 | 进度 |
|---|---|
| design (this) | ✅ ARCHITECTURE / ROADMAP / INSTALL / CONTRIBUTING 落地 |
| v0.0.1 | 未启动 |

---

## Repo

<https://github.com/coxon/itsme>

