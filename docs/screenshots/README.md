# 改造前后对比截图

把 3 组截图放这个目录，文件名建议：

| 文件 | 对比内容 | 怎么拍 |
|---|---|---|
| `01-transport.png` | **MCP 传输层**：改造前任意路径可 POST、无会话头；改造后仅 `/mcp`、响应带 `Mcp-Session-Id` | 两个终端并排，各跑 `curl -i` |
| `02-play-history.png` | **播放次数修复**：改造前 `get_play_history` 显示 `plays: 0`；改造后显示真实次数 | MCP 客户端或网页「听歌排行」页 |
| `03-web-console.png` | **新增前端**：改造前访问 `/` 返回 `{"error":"Not found"}`；改造后打开完整控制台 | 浏览器左右分屏 |

## 具体命令

### 01 传输层

```bash
# 改造前
git stash && git checkout 7a9344b
python server/mcp-server/server.py
curl -i -X POST http://127.0.0.1:3456/ -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# 观察：根路径也返回 200、响应无 Mcp-Session-Id

# 改造后
git checkout main
python server/mcp-server/server.py
curl -i -X POST http://127.0.0.1:3456/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
# 观察：仅 /mcp 有效，响应带 Mcp-Session-Id 与 protocolVersion
```

### 02 播放次数

```bash
# 改造前：值恒为 0
# 改造后：
curl -s -X POST http://127.0.0.1:3456/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_play_history","arguments":{"all_time":true,"limit":5}}}'
```

### 03 网页控制台

```
改造前：浏览器打开 http://127.0.0.1:3456/   →  {"error": "Not found", ...}
改造后：浏览器打开 http://127.0.0.1:3456/   →  完整控制台（红色顶栏、歌单、排行…）
```