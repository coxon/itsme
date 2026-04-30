# itsme — Icon Design Plan

> Status: **Design draft** · v0.0.x
> Scope: 品牌标识 + 引擎图标 + MCP 动词图标 + Hook 图标
> Last updated: 2026-04-30

---

## 1. 目的与范围

`itsme` 在三个地方需要图标资产：

1. **README / 文档** — 品牌头图、说明性 inline icon
2. **IDE 集成展示** — Claude Code / Codex 加载 plugin 时的标识、`status()` feed 渲染
3. **未来 Obsidian Vault 装饰** — Aleph 写入的 entry 类型徽标（远期）

本计划只覆盖 v0.0.x 阶段的最小可用图标集（**MUI**：Minimum Usable Iconset）。

---

## 2. Locked decisions

- ✅ 格式：**SVG**（24×24 grid，2px stroke，rounded cap/join）
- ✅ 颜色策略：**单色 `currentColor`**，跟随主题色（IDE light/dark 自适应）
- ✅ 风格：**stroke-based 几何线条**，不用渐变 / 不用阴影
- ✅ 字号 / 视觉重量：与 [Lucide](https://lucide.dev/) 兼容（方便混排）
- ✅ License：**CC0 / MIT**（项目本体 license 一致），可商用可二改
- ✅ 命名规则：`<scope>-<name>.svg`，全小写、连字符
- ✅ 存放：`docs/assets/icons/`

---

## 3. Icon set（MUI · 9 个）

| Scope | Name | 文件名 | 含义 / 概念 |
|---|---|---|---|
| **brand** | itsme | `itsme.svg` | 记忆环 + 自我锚点（"m" 字形 + dot） |
| **engine** | mempalace | `engine-mempalace.svg` | 小神殿 / 柱廊：verbatim 房间网格 |
| **engine** | aleph | `engine-aleph.svg` | 翻开的书 + 一颗星：wiki + LLM 蒸馏 |
| **verb** | remember | `verb-remember.svg` | 书签 + 加号 |
| **verb** | ask | `verb-ask.svg` | 对话气泡 + 问号点 |
| **verb** | status | `verb-status.svg` | 心电波形 |
| **hook** | before-exit | `hook-before-exit.svg` | 门 + 钉入箭头 |
| **hook** | before-clear | `hook-before-clear.svg` | 扫帚 / 笔触 + 钉入 |
| **hook** | before-compact | `hook-before-compact.svg` | 双向箭头 + 中线 |

> 三个 hook 共享同一"钉入"语义但用不同的反向动作做区分（exit=出去 / clear=擦除 / compact=压缩）。

---

## 4. 视觉规范

### 网格与笔画

```text
canvas:        24 × 24
stroke-width:  2px
stroke-cap:    round
stroke-join:   round
fill:          none (除强调点用 currentColor)
viewBox:       "0 0 24 24"
```

### 颜色

```text
全部使用 stroke="currentColor"，不写死颜色。
强调点（如 brand mark 的 dot）才允许 fill="currentColor"。
```

### 一致性 checklist

- [ ] 每个图标视觉重心都落在 24×24 几何中心
- [ ] 出血留白 ≥ 1.5px（不顶到边缘）
- [ ] 所有 path 共享统一 stroke-width
- [ ] 与 Lucide 同尺寸放在一起肉眼无突兀感

---

## 5. 命名 / 存放

```text
docs/
└── assets/
    └── icons/
        ├── README.md               # 预览网格 + 用法说明
        ├── itsme.svg               # 品牌
        ├── engine-mempalace.svg
        ├── engine-aleph.svg
        ├── verb-remember.svg
        ├── verb-ask.svg
        ├── verb-status.svg
        ├── hook-before-exit.svg
        ├── hook-before-clear.svg
        └── hook-before-compact.svg
```

引用方式（README / docs）：

```markdown
![itsme](docs/assets/icons/itsme.svg)
```

---

## 6. 路线图（icon 专属）

按 itsme 主路线 milestone 对齐：

| Phase | Milestone | 交付 |
|---|---|---|
| **I0** | 现在 | 设计 plan（本文档） |
| **I1** | v0.0.1 一同上 | brand + 3 verb + 3 hook 共 7 个核心图标，README 用上 brand |
| **I2** | v0.0.2 一同上 | engine-mempalace / engine-aleph 上线，docs/ARCHITECTURE.md 引擎章节加 inline icon |
| **I3** | v0.0.4 体验打磨 | `status()` feed 渲染时按 event 类型显示 icon（CLI 用 unicode 兜底，IDE rich render 用 SVG） |
| **I4** | v0.1+ | Aleph entry 类型徽标（people / project / decision / ...）+ Obsidian Vault 主题包 |

### Tasks（拆到 ROADMAP 风格）

#### I1 — 核心 7 图标（与 v0.0.1 同发）
- [ ] **IC1.1** 落盘 7 个 SVG（brand / 3 verb / 3 hook）
- [ ] **IC1.2** `docs/assets/icons/README.md` 预览网格（用 HTML table 展示 + 用法 snippet）
- [ ] **IC1.3** 项目根 README 顶部加 brand mark
- [ ] **IC1.4** 视觉一致性 review：在 light/dark 双主题下肉眼过一遍
- [ ] **IC1.5** PR：`feature/icons-mui` → main，CodeRabbit review

#### I2 — 双引擎图标（与 v0.0.2 同发）
- [ ] **IC2.1** `engine-mempalace.svg` / `engine-aleph.svg` 落盘
- [ ] **IC2.2** docs/ARCHITECTURE.md §3 双引擎章节插入 inline icon
- [ ] **IC2.3** 在 README "What is this" 段把两个 engine 名字旁挂上 icon

#### I3 — status feed 渲染（与 v0.0.4 同发）
- [ ] **IC3.1** 定义 event → icon 映射表（`memory.stored` → remember icon, `wiki.promoted` → aleph icon, ...）
- [ ] **IC3.2** CLI `itsme events tail` 用 unicode/emoji 兜底
- [ ] **IC3.3** 富渲染（IDE / Obsidian webview）用 SVG

#### I4 — 长尾（v0.1+）
- [ ] **IC4.1** Aleph entry type 徽标设计（≥ 6 种类型）
- [ ] **IC4.2** Obsidian Vault 主题包（CSS snippet + icon assets）

---

## 7. 工作流

每一波 icon 上线都按 itsme 的 git-flow 子流程：

```bash
git checkout main && git pull
git checkout -b feature/icons-<phase>      # 如 feature/icons-mui
# 设计 / 落盘 / preview README
git add docs/assets/icons/
git commit -m "feat(icons): MUI core 7 icons"
git push -u origin feature/icons-<phase>
gh pr create --base main --title "feat(icons): MUI core 7 icons" --body "..."
# review (人 + CodeRabbit) → squash merge
```

---

## 8. 验收标准（DoD）

每一个 phase 落地需满足：

1. ✅ 所有图标 SVG 通过 lint（无内联 fill 颜色、无 transform 残留、viewBox 正确）
2. ✅ `docs/assets/icons/README.md` 预览页能在 GitHub 上正确渲染
3. ✅ 至少在 light + dark 两套主题下截图过一次（贴在 PR 描述里）
4. ✅ 主 README 顶部 brand mark 引用路径正确
5. ✅ 不破坏 markdown / docs 链接

---

## 9. 后备 / Open questions

| ID | 问题 | 当前倾向 |
|---|---|---|
| IQ1 | 是否需要 favicon / og-image？ | v0.0.x 不做，等有官网/产品页再做 |
| IQ2 | 是否做动效版本（SVG SMIL / Lottie）？ | 不做，徒增复杂度 |
| IQ3 | 是否打 npm/PyPI icon package？ | 暂不，仓库内引用即可 |
| IQ4 | brand color 是否要定一个主色？ | 暂用 `currentColor`，等品牌战需要时再加 `--itsme-accent` CSS var |
| IQ5 | 是否提供 PNG 导出？ | 仅在 README 顶部需要时手动导一张 PNG（其余全 SVG） |

---

## 10. 参考

- [Lucide Icons](https://lucide.dev/) — 笔画风格基准
- [Phosphor Icons](https://phosphoricons.com/) — 备选风格参考
- [The Noun Project](https://thenounproject.com/) — 概念灵感（不直接抄稿）
