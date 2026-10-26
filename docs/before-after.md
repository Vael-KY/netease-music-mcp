# 改造前后对比

## 一、请求链路对比（L3 判定依据）

### 改造前（上游 `7a9344b`）

```
MCP 客户端
  │
  │  POST <任意路径>            ← do_POST 不校验路径
  ▼
server.py :: MCPHandler
  │
  │  直接按 method 分发，无会话、无协议协商
  │  通知也返回响应体（违反 JSON-RPC）
  ▼
工具函数（TOOL_DISPATCH）
  │
  ├─ get_play_history  读 score 字段 → 播放次数恒为 0
  ├─ get_song_details  >50 首静默截断
  ├─ get_liked_songs   只返回裸 ID
  └─ reorder_...       不校验曲目集合 → 会弄乱歌单
  ▼
网易云 API

CORS: Access-Control-Allow-Origin: *   ← 任意网页可驱动用户账号
前端: 无
测试: 无
```

### 改造后（`HEAD`）

```
MCP 客户端                                                浏览器
  │                                                          │
  │ POST /mcp   GET /mcp   DELETE /mcp          GET / , /ui  │
  │ 或 GET /sse + POST /message                            │
  │ 或 stdio (--transport stdio)                           │
  ▼                                                          ▼
server.py :: MCPHandler                        _serve_frontend()
  │                                              │
  │ ① Origin 校验（默认仅放行 localhost）          │ 读 frontend/index.html
  │ ② 路径校验：仅 /mcp 与 /message                │
  │ ③ 读取并排空 body（防 keep-alive 串包）         │
  │ ④ 解析 JSON-RPC（-32700）                     │
  ▼                                              │
会话层 _Session                                   │
  │  Mcp-Session-Id 下发 / 校验 / 24h TTL        │
  │  按会话加锁串行化                             │
  ▼                                              │
协议协商 SUPPORTED_PROTOCOL_VERSIONS              │
  │  2025-06-18 / 2025-03-26 / 2024-11-05 / 2024-10-07
  ▼                                              │
handle_request() → 通知 202 / 未知方法 -32601      │
  ▼                                              │
工具层 TOOL_DISPATCH（18 个，带 annotations）      │
  │                                              │
  ├─ get_play_history  读 playCount → 真实次数 ✔  │
  ├─ get_song_details  服务端分批 50/批 ✔         │
  ├─ get_liked_songs   分批解析歌名歌手 ✔         │
  └─ reorder_...       校验完整曲目集合 ✔         │
  ▼                                              │
网易云 API  ←──────────────────────────────────────┘
                                   前端以 JSON-RPC 直连 POST /mcp
                                   （localhost Origin 放行，无后端代理）

CORS: 仅回显被允许的 Origin，并带 Vary: Origin
测试: 42 个离线单测 + 传输检查 + 匿名冒烟
```

## 二、关键行为差异

| 维度 | 改造前 | 改造后 |
|---|---|---|
| MCP 端点 | 任意路径都可 POST（含 `/`） | 仅 `/mcp`（与 legacy `/message`） |
| 会话 | 无 | `Mcp-Session-Id` + 24h TTL |
| 协议协商 | 无（硬编码 `2025-03-26`） | 四版本协商 |
| stdio 传输 | 不支持 | `--transport stdio` |
| 通知处理 | 返回响应体 | `202 Accepted` 无体 |
| 浏览器跨域 | `*` 全放行 | 默认仅 localhost |
| `get_play_history` 次数 | **恒为 0**（误读 `score`） | 真实 `playCount` |
| `get_song_details` | >50 首截断 | 服务端分批 |
| `reorder` 安全性 | 会弄乱歌单 | 先校验完整集合 |
| 前端 | 无 | 单文件控制台（1569 行） |
| 测试 | 无 | 42 单测全过 |

## 三、功能对比（演示用）

| 能力 | 改造前 | 改造后 |
|---|---|---|
| 在浏览器里用 | ❌ 无界面，只有裸接口 | ✅ `http://127.0.0.1:3456/` |
| 看歌单/红心/排行 | 需 MCP 客户端 | ✅ 网页直接看 |
| 连播 | ❌ | ✅ 播放全部 + 队列 |
| 跳转听歌 | ❌ | ✅ 网页播放器 / 本地客户端 |
| 听歌洞察 | ❌ 只有原始数据 | ✅ AI 用户侧写（三档窗口） |
| 搜索引导 | ❌ 空白页 | ✅ 按听歌记录推荐快捷搜索 |
| 部署 | 手动 `python server.py` | ✅ `start.cmd` / Docker |

## 四、复现验证命令

```bash
# 1. 上游基线
git checkout 7a9344b
python server/mcp-server/server.py
curl -X POST http://127.0.0.1:3456/health -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
#   观察：任意路径都接受 POST、无 Mcp-Session-Id 响应头、get_play_history 次数为 0

# 2. 本次改造
git checkout main
python server/mcp-server/server.py
curl http://127.0.0.1:3456/health
#   期望：{"status":"ok",...,"tools":18,"logged_in":true}
curl -X POST http://127.0.0.1:3456/mcp \
     -H 'Content-Type: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' -i
#   期望：200 + Mcp-Session-Id 响应头 + protocolVersion 回显
# 3. 单测
python -m unittest discover -s tests
#   期望：Ran 42 tests ... OK
```