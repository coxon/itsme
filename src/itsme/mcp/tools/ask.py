"""ask(question, mode?, promote?) — query.

工具层职责：参数校验 + 编排。**不**直接调用 MemPalace MCP 或 Aleph 内部，
所有查询必须通过 `itsme.core`（reader worker / adapters）完成。

v0.0.1 T1.11：走 core 读取路径回 MemPalace；`mode=auto` 与
`promote=true` 分别在 v0.0.2 / v0.0.3 实装（见 ROADMAP）。
"""

from __future__ import annotations
