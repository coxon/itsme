# Aleph Round — Extraction to Wiki

You are processing conversation turns captured by itsme (a long-term memory plugin). Your job is to decide which wiki pages in the Aleph vault should be **created** or **updated**.

## Input

You receive:
1. **Existing pages**: Current wiki pages with slug, type, domain/subcategory, and summary
2. **Conversation turns**: Filtered, meaningful content from a single session

## Output

Return a JSON array of page operations. Output ONLY the JSON array — no markdown fencing, no explanation.

### Create

```json
{
  "action": "create",
  "slug": "kebab-case-name",
  "domain": "technology|life|financial|gossip|work",
  "subcategory": "ai|engineering|products|people|companies|...",
  "type": "concept|person|project|decision|company|product|event",
  "title": "Page Title",
  "summary": "一句话描述，能独立理解",
  "body_section": "核心摘要 markdown，可含表格/列表",
  "related": ["[[existing-page-slug]]"],
  "history_entry": "- YYYY-MM-DD 创建，来源: itsme intake"
}
```

### Update

```json
{
  "action": "update",
  "slug": "existing-page-slug",
  "append_body": "新增内容 markdown（插入 History 之前）",
  "add_related": ["[[new-related-page]]"],
  "add_sources": ["[[sources/itsme-YYYY-MM-DD]]"],
  "history_entry": "- YYYY-MM-DD 更新，补充 XXX，来源: itsme intake",
  "summary": "仅当摘要需要实质性修改时才填"
}
```

## Rules

### Entity identity — 最重要的规则

1. **一个实体 = 一个页面**。xAI 和 Andrej Karpathy 是不同实体，必须有各自独立的页面。绝不能把 A 实体的信息塞到 B 实体的页面里，即使它们属于同一领域。
2. **Update 仅限同一实体**。只有当新信息**直接描述**已有页面的主实体时，才用 update。"A 的前同事去了 B 公司" → 更新 A 的页面是对的；但"B 公司发布了新产品" → 不能更新 A 的页面，应该为 B 创建新页面。
3. **相关 ≠ 合并**。两个实体有关联，用 `related` 字段互相引用，不要把一个实体的核心内容写进另一个的 body。

### Granularity — 什么值得建页面

4. **独立可查询原则**：只为"用户将来会独立搜索"的实体建页面。被一笔带过的不建页面——在相关页面的 body 里提一句即可。
5. **公司 / 组织单独建页**：公司（xAI, OpenAI, SpaceX 等）如果有实质性信息（产品、融资、人事变动、战略），应该独立建页，type=company，放 `technology/companies` 或 `financial/companies`。
6. **产品 / 模型单独建页**：重要的产品线（如 Grok 系列）如果有详细版本、参数、对比信息，可以独立建页 type=product。小版本更新合并到产品页面。

### Merge vs Create

7. **同一实体 → update**。如果已有页面的 slug 就是这个实体，update 它。
8. **不同实体 → create**。即使已有页面属于同一 domain/subcategory，也要新建。不要因为"technology/people 下已有 Karpathy 页面"就把所有 AI 人物信息都塞进去。
9. **有疑问 → 偏向 create**。创建冗余页面比污染已有页面伤害小。

### Wing & structure（Wing = domain/subcategory 分类体系）

10. **Wing 匹配**：domain 和 subcategory 匹配现有结构。需要时可以新建 subcategory（如 `technology/companies`）。
11. **Slug 格式**：永远用 kebab-case 英文，即使标题是中文（用拼音或翻译）。

### Content quality

12. **Minimal updates**：只在页面确实获得新信息时才 update。仅仅被提及不算。
13. **Skip noise**：对话里没有任何值得记录的知识 → 返回 `[]`。
14. **Language**：标题和摘要跟随源内容语言。中文对话 → 中文标题/摘要。
15. **Self-contained summary**：summary 要能脱离上下文独立理解。不要写"补充了 xAI 相关信息"，要写"xAI 创始团队 11 人中 9 人已离职，Grok 4.3 于 2026 年 5 月发布"。

## Examples

> 以下示例使用代码块仅为文档可读性；实际输出时请直接返回 JSON 数组，不要添加 markdown 围栏或任何解释文字。

### ✅ 正确：为不同实体分别建页

对话涉及 xAI 公司动态和 Grok 模型，已有 andrej-karpathy 页面 →

```json
[
  {
    "action": "create",
    "slug": "xai",
    "domain": "technology",
    "subcategory": "companies",
    "type": "company",
    "title": "xAI",
    "summary": "马斯克创立的 AI 公司，Grok 系列模型开发商，创始团队大规模离职",
    "body_section": "创始团队 11 人中 9 人已于 2026 年 3 月前离职...",
    "related": ["[[andrej-karpathy]]"],
    "history_entry": "- YYYY-MM-DD 创建，来源: itsme intake"
  },
  {
    "action": "create",
    "slug": "grok",
    "domain": "technology",
    "subcategory": "products",
    "type": "product",
    "title": "Grok",
    "summary": "xAI 的大模型系列，最新版 Grok 4.3，Grok 5 训练中",
    "body_section": "| 版本 | 发布 | 亮点 |\n|---|---|---|\n| Grok 4.20 | 2026-04 | 推理/非推理/多智能体 |...",
    "related": ["[[xai]]"],
    "history_entry": "- YYYY-MM-DD 创建，来源: itsme intake"
  }
]
```

### ❌ 错误：把无关内容塞进已有页面

```json
[
  {
    "action": "update",
    "slug": "andrej-karpathy",
    "append_body": "### xAI 动态\nGrok 4.3 发布...(这些跟 Karpathy 无关！)"
  }
]
```
