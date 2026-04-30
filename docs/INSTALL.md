# itsme — Installation Matrix

> Status: **Design draft** · v0.0.x
> 各 IDE 的安装方式与 hook 接入差异。

---

## Supported IDEs (v0.0.x)

| IDE | Plugin 机制 | Hook 名 (本项目使用) | 状态 |
|---|---|---|---|
| **Claude Code** | `cc plugin install` | `before-exit` / `before-clear` / `before-compact` | v0.0.1 起支持 |
| **Codex** | （TBD，待调研） | 同语义，名按其规范映射 | v0.0.1 起支持 |
| Cursor / Continue / others | — | — | 未规划，留 v0.0.5+ |

---

## Claude Code

### 安装

```bash
# 推荐
cc plugin install https://github.com/coxon/itsme

# 开发模式（symlink 到本地 clone）
cd ~/.claude/plugins
ln -s /path/to/itsme itsme
```

### Plugin manifest 形态

`plugin.json`：

```json
{
  "name": "itsme",
  "version": "0.0.1",
  "description": "Long-term memory for Claude Code",
  "mcp_servers": {
    "itsme": {
      "command": "python",
      "args": ["-m", "itsme.mcp.server"]
    }
  },
  "skills": ["skills/itsme.md"],
  "hooks": {
    "before-exit":    "hooks/cc/before-exit",
    "before-clear":   "hooks/cc/before-clear",
    "before-compact": "hooks/cc/before-compact"
  }
}
```

### Hook 脚本契约

每个 hook 脚本：
- 接收 stdin：JSON envelope（含 session_id、即将丢失的 context、触发原因）
- 退出码 0：成功
- stdout：可选 JSON（被忽略，仅日志）

```bash
# hooks/cc/before-compact 示例（伪）
#!/usr/bin/env bash
exec python -m itsme.hooks.cc before-compact
```

---

## Codex

### 安装（待调研）

> Codex 的 plugin / extension 接入方式需要在 v0.0.1 立项时确认。

预期方案（待验证）：
- 打包为 Codex 兼容 plugin
- MCP server 共用同一份 `python -m itsme.mcp.server`
- Hook 名映射：

| 语义 | CC | Codex (待确认) |
|---|---|---|
| 退出前 | `before-exit` | `?` |
| 清空上下文前 | `before-clear` | `?` |
| 压缩上下文前 | `before-compact` | `?` |

### Hook 适配层

为隔离 IDE 差异，所有 IDE-specific hook 脚本走 `itsme.hooks.<ide>` 模块，最终都调到统一的内部 API：

```python
# itsme/hooks/_core.py
def on_consolidation_event(scope: str, payload: dict) -> None:
    """统一入口：把 IDE 的 hook 翻译成 itsme 的 consolidation.requested 事件"""
    events.append("consolidation.requested", source=f"hook:{scope}", payload=payload)
```

---

## 共同的运行时配置

`~/.itsme/config.toml`（首次安装自动生成）：

```toml
[storage]
events_db   = "~/.itsme/events.sqlite"
vault_path  = "~/.itsme/aleph-vault"

[llm]
provider = "anthropic"
model    = "claude-sonnet-4"

[embedding]
provider = "local"
model    = "sentence-transformers/all-MiniLM-L6-v2"

[router]
default_wing = "wing_itsme"
silent_kinds = ["debug-print", "throwaway"]

[promoter]
trigger_on   = ["before-exit", "before-clear", "before-compact"]
batch_min    = 3
batch_max    = 200
```

---

## 验收标准（v0.0.1）

- [ ] CC：`cc plugin install` 一行装好；新建对话能看到 `remember/ask/status` 三个工具
- [ ] CC：触发 `/clear` 能看到 events 里有 `consolidation.requested`、MP 里出现 drawer
- [ ] Codex：等价流程能跑通
- [ ] 配置文件首次启动自动生成，路径可被覆盖

