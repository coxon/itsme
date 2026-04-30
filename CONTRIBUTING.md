# Contributing to itsme

> 本仓库使用**标准 git-flow** 分支模型。

---

## Branch model

```
main         ─●─────────────●─────────●──────►   只放发布版本（带 tag）
                ╲             ╲         ╱
release/*       ─●─────●──────●         ●─────►   发布前的稳定化分支
                  ╲     ╲    ╱
develop      ─●───●──●───●──●───●──●──●──���────►   集成分支，所有 feature 汇入
                   ╲   ╱      ╲    ╱
feature/*  ────────●─●         ●──●            ►   单个特性，从 develop 起，回 develop

hotfix/*   ─────────────────●──────●  ─────►      紧急修复，从 main 起，回 main + develop
```

---

## Branches

| 分支 | 用途 | 起源 | 合并去向 |
|---|---|---|---|
| `main` | 生产可发布版本，每次合并打 tag | — | — |
| `develop` | 集成分支，下一个版本的工作面 | `main` (init) | `release/*` |
| `feature/<name>` | 新特性、单个 task / task 组 | `develop` | `develop` (squash or merge) |
| `release/v0.0.x` | 版本稳定化（bug fix、文档、版本号） | `develop` | `main` + `develop` |
| `hotfix/<name>` | 生产紧急修复 | `main` | `main` + `develop` |

---

## Naming conventions

- **feature**：`feature/<area>-<short-desc>`，如 `feature/events-ringbuf`、`feature/aleph-extract-pipeline`
- **release**：`release/v0.0.1`、`release/v0.0.2`
- **hotfix**：`hotfix/<short-desc>`，如 `hotfix/router-null-kind`
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
git checkout develop
git pull
git checkout -b feature/events-ringbuf

# 2. 开发 + 提交（小步）
git add ...
git commit -m "feat(events): scaffold ringbuf module"
git commit -m "feat(events): implement append/tail/consume"
git commit -m "test(events): cover ring rollover"

# 3. 推到远端
git push -u origin feature/events-ringbuf

# 4. 开 PR → develop
#    审、评、调
#    合并（推荐 squash & merge，保持 develop 线性）

# 5. 删分支
git branch -d feature/events-ringbuf
```

---

## Workflow — 一次发布

```bash
# 1. develop 攒够一个 milestone（比如 v0.0.1 全部 task ✅）
git checkout develop
git checkout -b release/v0.0.1

# 2. 在 release 分支上：
#    - 改版本号（pyproject.toml / __version__）
#    - 更新 CHANGELOG.md
#    - 最后的小修小补，不加新特性

# 3. 合并到 main，打 tag
git checkout main
git merge --no-ff release/v0.0.1
git tag -a v0.0.1 -m "v0.0.1: end-to-end stub"
git push origin main --tags

# 4. 回灌 develop
git checkout develop
git merge --no-ff release/v0.0.1
git push origin develop

# 5. 删 release 分支
git branch -d release/v0.0.1
```

---

## Versioning

[Semantic Versioning](https://semver.org/) — pre-1.0 阶段：

- `0.0.x`：设计 + 早期 MVP，可能频繁破坏性变化
- `0.x.0`：MVP 稳定后的迭代
- `1.0.0`：API 冻结、production-ready

---

## Code style

- **Python**：`ruff` (lint + format) + `mypy` (strict)
- **Markdown**：保持 ATX heading（`#` 而非 underline）；行宽不强制换行
- **Commit message**：英文为主，必要时中英混写

---

## Pre-commit hooks (建议)

```yaml
# .pre-commit-config.yaml（v0.0.1 落地时启用）
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    hooks:
      - id: ruff
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    hooks:
      - id: mypy
```

---

## PR checklist

- [ ] 分支起源正确（feature 起自 develop，hotfix 起自 main）
- [ ] commit message 遵循 Conventional Commits
- [ ] 单元测试覆盖（ruff + mypy + pytest 全绿）
- [ ] docs 同步（架构变更必须更新 ARCHITECTURE.md）
- [ ] CHANGELOG.md 加一行（release 时统一整理也行）

