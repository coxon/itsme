# Contributing to itsme

> 当前阶段（v0.0.x）使用**简化分支模型**：`main` + `feature/*`。
> 1.0 之后或团队扩大时再升级到完整 git-flow（加入 `develop` / `release/*` / `hotfix/*`）。

---

## Branch model (current — v0.0.x)

```
main          ─●──────────●──────────●──────►   单一主线，受保护
                ╲          ╲          ╱
feature/*       ─●─●────●──┘    ●──●─┘          每个 feature 一个分支，PR 回 main
                 (commits)        (commits)
```

**铁律：**

- ❌ **禁止直接 commit/push 到 `main`**
- ✅ 任何变更必须走 `feature/*` 分支 + Pull Request
- ✅ PR 必须经过 review（自评或 CodeRabbit 评审）才能合并
- ✅ 合并到 main 推荐 **squash & merge**，保持主线线性

> **本规则由三层强制：**
> 1. `CLAUDE.md` 指令（让 Claude Code 在每个 session 都看到）
> 2. 本地 `.githooks/pre-push`（首次开发时 `git config core.hooksPath .githooks`）
> 3. GitHub branch protection（服务端硬约束）

---

## Future model (post-1.0)

升级到完整 git-flow：

```
main         ●──────●──────●──►        # release tags only
              ╲      ╲    ╱
release/*    ─●──●───●               # version stabilization
              ╲   ╱
develop      ●─●──●──●──●──►        # integration
                ╲    ╱
feature/*    ───●──●                # individual features
hotfix/*     ────────●──●           # urgent prod fixes (off main)
```

迁移触发条件：第一个 `1.0.0` 发布前。

---

## Naming conventions

- **feature**：`feature/<area>-<short-desc>`，如 `feature/events-ringbuf`、`feature/aleph-extract-pipeline`
- **hotfix**（罕见）：`hotfix/<short-desc>`
- **commit**：[Conventional Commits](https://www.conventionalcommits.org/)
  ```
  feat(events): add sqlite ring buffer
  fix(aleph): handle empty raw_batch in promoter
  docs(arch): update §7.2 with merge pipeline
  chore(deps): bump anthropic-sdk to x.y.z
  ```

---

## Workflow — 一个 feature 的生命周期

```bash
# 1. 起 feature 分支
git checkout main
git pull
git checkout -b feature/events-ringbuf

# 2. 开发 + 提交（小步）
git add ...
git commit -m "feat(events): scaffold ringbuf module"

# 3. 推到远端
git push -u origin feature/events-ringbuf

# 4. 开 PR → main
gh pr create --base main --title "feat(events): ring buffer" --body "..."

# 5. 等 review / CodeRabbit 评，绿后 squash merge

# 6. 删分支（远端会在合并后自动删，本地手动）
git checkout main && git pull && git branch -d feature/events-ringbuf
```

---

## Versioning

[Semantic Versioning](https://semver.org/) — pre-1.0 阶段：

- `0.0.x`：设计 + 早期 MVP，可能频繁破坏性变化
- `0.x.0`：MVP 稳定后的迭代
- `1.0.0`：API 冻结、production-ready，迁移到完整 git-flow

发布步骤（pre-1.0）：

```bash
# 1. main 上每个 milestone 完成时
git checkout main && git pull
# 2. 改版本号 + CHANGELOG（用一个 chore commit 或在合并 PR 里完成）
# 3. 打 tag
git tag -a v0.0.1 -m "v0.0.1: end-to-end stub"
git push origin v0.0.1
```

---

## Code style

- **Python**：`ruff` (lint + format) + `mypy` (strict)
- **Markdown**：保持 ATX heading（`#` 而非 underline）；行宽不强制
- **Commit message**：英文为主，必要时中英混写

---

## First-time setup（启用本地 hook）

```bash
git config core.hooksPath .githooks
```

之后每次 `git push` 会自动校验：
- 不允许 push 到 `main`
- 推荐启用前先运行 `ruff check` / `ruff format --check`

---

## PR checklist

- [ ] 分支命名规范（`feature/<area>-<desc>`）
- [ ] commit message 遵循 Conventional Commits
- [ ] 代码改动有测试覆盖（v0.0.1 起）
- [ ] 架构变更必须更新 `docs/ARCHITECTURE.md`
- [ ] 不直接改 `main`

