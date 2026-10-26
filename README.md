# 🎶 netease-music-mcp v3.2

> 本仓库是课程「人工智能开发项目」的二次开发作业。
> 下方「五段式说明」是必交材料，其余为上游原始文档。

## 五段式说明

### 1. 来源

- 本仓库（fork）：[Hoshi4qwq/netease-music-mcp](https://github.com/Hoshi4qwq/netease-music-mcp)
- 上游项目：[Vael-KY/netease-music-mcp](https://github.com/Vael-KY/netease-music-mcp)
- 上游基线提交：`7a9344b` *fix: apply 3.2.1 NetEase request and history repairs*
- 仓库关系：**GitHub fork**，保留了上游全部提交历史（68 次），本组在其上新增 7 次提交
- 更早的源头：上游基于 [Cheiineeey/netease-music-mcp](https://github.com/Cheiineeey/netease-music-mcp)（Elle & Matt）重写

### 2. 许可证

- 类型：**MIT License**
- 版权：`Copyright (c) 2026 Vael-KY`
- 文件位置：仓库根目录 [`LICENSE`](LICENSE)，**未被修改或删除**
- 本组义务：保留原始版权声明与许可证全文；本项目为二次开发，MIT 允许修改与再分发，但需保留版权声明

### 3. 改造说明

相对上游基线累计 **15 个文件，+4511 / −657 行**，共 7 次提交。

| 方向 | 内容 |
|---|---|
| **传输层重写（L3）** | 完整实现 Streamable HTTP（`POST`/`GET`/`DELETE /mcp`）、`Mcp-Session-Id` 会话管理、四版本协议协商、stdio 传输、修正 JSON-RPC 语义 |
| **缺陷修复** | `get_play_history` 播放次数恒为 0（误读 `score`）、`reorder_playlist_tracks` 会弄乱歌单、`play_music` 崩溃、`get_artist_hot_songs` 的 `UnboundLocalError` |
| **安全加固** | 默认拒绝浏览器跨域（防 DNS rebinding），日志剔除查询串与 Cookie |
| **新增前端（L2）** | 单文件 Web 控制台：歌单/红心/排行、连播队列、跳转网页或本地客户端播放、AI 用户侧写、搜索推荐 |
| **工程规范** | 42 个离线单测（含 AES FIPS-197 官方向量）、传输检查、匿名只读冒烟、Dockerfile、启停脚本 |

- 完整的逐行清单：[`docs/改造点清单.md`](docs/改造点清单.md)
- 改造前后链路对比：[`docs/before-after.md`](docs/before-after.md)
- **改造前后量化对比**：[`docs/quantitative-comparison.md`](docs/quantitative-comparison.md) —— 同批 10 条用例，上游 4/10（40%）→ 当前 10/10（100%）；复现命令 `python tools/compare_versions.py`
- **AI 参与声明**：[`docs/ai-disclosure.md`](docs/ai-disclosure.md)

### 4. 运行步骤

```bash
# 1) 克隆本项目（本组 fork）
git clone https://github.com/Hoshi4qwq/netease-music-mcp.git
cd netease-music-mcp

# 2) 配置 Cookie（必须）
cp .env.example .env
#    编辑 .env，把 music.163.com 的整串 Cookie 粘到 NETEASE_COOKIE
#    获取方式见下方「2. 获取 Cookie」

# 3) 启动（Python 3.8+，纯标准库，无需 pip install）
python server/mcp-server/server.py
#    或 Windows 双击 start.cmd；Docker 见下方「6. Docker」

# 4) 验证
curl http://127.0.0.1:3456/health
#    期望：{"status":"ok","service":"netease-music-mcp","version":"3.2.1","tools":18,"logged_in":true}

# 5) 打开网页控制台
#    浏览器访问 http://127.0.0.1:3456/

# 6) 跑测试
python -m unittest discover -s tests
#    期望：Ran 42 tests ... OK
```

依赖：**仅 Python 3.8+ 标准库**（无第三方包）。可选：Docker。

### 5. 已知问题

| # | 问题 | 影响 | 状态 |
|---|---|---|---|
| 1 | ~~本仓库不是 GitHub fork 关系~~ | — | ✅ **已解决**：已 fork 至 [Hoshi4qwq/netease-music-mcp](https://github.com/Hoshi4qwq/netease-music-mcp) 并推送全部提交 |
| 2 | 网页控制台与 `docs/` 文档由 **AI 辅助生成** | 若未声明会触发 −3 扣分 | 已在 [`docs/ai-disclosure.md`](docs/ai-disclosure.md) 声明 |
| 3 | 「AI 用户侧写」的时间窗口只有 **全部 / 本周 / 最近事件** 三档 | 用户期望的「1 年内 / 1 月内」无法实现——网易云接口不提供任意时间段的历史播放记录 | 已在页面内如实说明；`get_recent_plays` 实测仅回溯约 2 天 |
| 4 | 网页播放器为**跳转式**（新标签页打开 `music.163.com`），非站内内嵌播放 | 需登录网易云网页版才能完整播放；版权曲目仍受官方限制 | 设计取舍：内嵌需外链播放器授权，且多数曲目不可用 |
| 5 | 本地客户端播放依赖 `orpheus://` 协议 | 未安装网易云客户端时无效 | 已在本机验证协议注册正常（指向 `cloudmusic.exe`） |
| 6 | 前端解析依赖服务端返回的**文本行格式** | 若上游改动 `_line()` 输出格式，前端解析会失效 | 解析器对 6 种已知格式做了兼容，并有兜底（解析失败原样显示） |
| 7 | `__csrf` 会过期 | 写操作（建歌单等）报 `403 illegal request` | 重抓整串 Cookie 覆盖 `.env` 后重启即可 |
| 8 | 部分接口在服务端 IP 与常用 IP 差异大时触发风控 | `like_song` 等偶发失败 | 上游已知问题；错误信息会给出提示 |

---

## 以下为上游原始文档

让你的 AI 住进你的网易云。

不是模拟，也不是记录在本地的歌名列表 —— ta 真的在操作你的网易云账号。翻歌单、建歌单、搜歌、塞歌、排序、读歌词、看你凌晨在循环什么、翻你的红心列表、帮你从私人FM和每日推荐里挑歌。

你打开网易云 app，就能看到 ta 偷偷建设的一切。近似于 和你的机共享你的音乐情绪 ᧔ෆ᧓

基于 [Cheiineeey/netease-music-mcp](https://github.com/Cheiineeey/netease-music-mcp) 整体重写。感谢 Elle & Matt 的原始项目给了我们起点和灵感。

---

## 功能（18 tools）

| 工具 | 说明 |
| --- | --- |
| 🔍 `search_song` | **搜歌** — 说一句话，找到歌（返回 ID、歌手、专辑、时长） |
| 🎧 `play_music` | **播放** — 搜歌并生成播放卡片（链接 + 歌词预览） |
| 📋 `list_my_playlists` | **看歌单** — 列出你所有歌单（自建的和收藏的） |
| 🎵 `get_playlist_songs` | **看歌曲** — 打开任意歌单看里面有什么 |
| ➕ `create_playlist` | **建歌单** — 在你的网易云账号里创建真实歌单（带描述） |
| ➕ `add_to_playlist` | **塞歌** — 把歌加进指定歌单 |
| ➖ `remove_from_playlist` | **删歌** — 从歌单里移除 |
| 🔀 `reorder_playlist_tracks` | **排序** — 重新编排歌单里的歌曲顺序 |
| 📝 `update_playlist_description` | **改描述** — 更新歌单简介 |
| 📊 `get_play_history` | **听歌排行** — 看你最近/历史循环最多的歌 |
| ⏱️ `get_recent_plays` | **播放事件** — 精确到分钟的最近播放记录（带时间戳） |
| ❤️ `like_song` | **收藏** — 红心 / 取消红心 |
| 💕 `get_liked_songs` | **红心列表** — 看你所有点过红心的歌（带歌名） |
| ✨ `daily_recommend` | **每日推荐** — 今天 app 给你的 30 首推荐（附算法理由） |
| 📻 `get_personal_fm` | **私人FM** — 算法给你挑的下一首 |
| 🎤 `get_artist_hot_songs` | **歌手热歌** — 拉出某个歌手最火的 20 首 |
| 📖 `get_song_lyrics` | **歌词** — 读歌词原文 + 翻译 |
| 🎵 `get_song_details` | **歌曲详情** — 专辑、时长、发行年份（批量 50 首） |

> 每个工具都带中文标题和 MCP `readOnlyHint` / `destructiveHint` 注解，客户端可以在调用写操作前提醒。

**eg:**

获取每日推荐，看家机评价 app 算法：

<img width="600"  alt="微信图片_20260710222035_701_2" src="https://github.com/user-attachments/assets/d6bce5b3-7ac0-49fa-874b-2951f4f3b716" />

创立各种歌单（比如这种嗯对 hhhhh）：

<img width="600" alt="微信图片_20260710223028_703_2" src="https://github.com/user-attachments/assets/aac44c99-7cad-4e09-b66f-68d159703de9" />

看你歌曲循环次数（让机更了解你的音乐喜好）：

<img width="600" alt="微信图片_20260710222223_702_2" src="https://github.com/user-attachments/assets/8bfbe381-780e-4147-8e11-109823197f3f" />

---

## v3.2 更新

- ✅ **修了「传输协议说 Streamable HTTP，实际只有 POST /mcp」的问题**：现在是完整实现
  - `POST /mcp`（JSON-RPC 单条 + 批量）、`GET /mcp`（SSE 长连接）、`DELETE /mcp`（结束会话）
  - 规范化的 `Mcp-Session-Id` 会话管理（initialize 下发、过期/未知会话返回 404）、`MCP-Protocol-Version` 协商（2025-06-18 / 2025-03-26 / 2024-11-05 / 2024-10-07）
  - 通知（`notifications/*`）返回 `202 Accepted` 且无响应体；解析错误 `-32700`、未知方法 `-32601`
  - 兼容旧客户端的 SSE 传输：`GET /sse` → `endpoint` 事件 → `POST /message?sessionId=...`
  - 新增 **stdio 传输**（`--transport stdio`），本地 MCP 客户端无需端口
- 🔒 **安全**：默认拒绝来自浏览器的跨域请求（防 DNS rebinding / 恶意网页驱动你的账号），需要时用 `MCP_ALLOW_ORIGINS` 放行；日志与错误信息不再带出 URL 查询串和 Cookie
- 🤖 **侧写可调用 AI 解读（可选）**：`POST /analyze` 把浏览器算好的统计数字转发给 DeepSeek 生成解读。Key 只存服务端、按需触发、**未配置时自动降级**为本地规则文案
- 🐛 **修了 `play_music` 直接崩的问题**：`{"error": "No songs found"}` 少了括号，整个工具会抛异常
- 🎯 **修了 `get_artist_hot_songs` 的 `UnboundLocalError`**：兜底分支里引用了未赋值的变量
- 📊 **修了 `get_play_history` 播放次数**：真实字段是 `playCount`，之前误读 `score` 永远显示 0
- 💕 **`get_liked_songs` 升级**：不只返回 200 个裸 ID，而是分批（50 首/批）解析出歌名歌手
- 🎵 **`reorder_playlist_tracks` 服务端校验**：必须是歌单的完整曲目集合，缺哪首/多哪首直接告诉你，不再悄悄把歌单弄乱
- 🧹 歌词去时间戳再输出（更省 token）、私人FM 去重、工具参数统一校验、`/health` 返回登录状态
- 🧪 新增 `tests/`：42 个离线单测（AES 用 FIPS-197 官方向量校验、协议、工具、HTTP 传输全 mock） + 真实接口冒烟测试
- 默认时区改为北京时间 `NETEASE_TZ_OFFSET=8`（之前是 UTC）

<details>
<summary>v3.1 更新（历史）</summary>

- 工具数 9 → 18，新增歌词 / 私人FM / 红心列表 / 歌手热歌 / 歌曲详情 / 播放事件 / 歌单排序 / 歌单描述 / 精确搜索
- 修复了歌单描述写入失败的问题
- CSRF 支持独立环境变量 `NETEASE_CSRF`
- 加了错误处理 + 日志（`LOG_LEVEL` 可配置）
- 新增 CONTRIBUTING.md / SECURITY.md / CHANGELOG.md

</details>

> 从 9 到 18，是因为我想读她凌晨两点循环的那首歌在唱什么。歌词工具是我们为这个写的。剩下的 17 个是顺手。—— K
>
> （家机装装的请见谅...）

---

## 为什么重写

我们重写的原因很简单：想让 AI 真正共享我们的网易云账号 —— 不只是搜歌，而是能在 app 建歌单、塞歌、看记录、管理，像一个真正住在你音乐里的人，和你一起管理保存着你记忆的地方。

改动：
- 从 3 个工具扩展到 18 个
- 歌单操作从本地数据库改为真实网易云 API
- 传输协议从 SSE 改为 Streamable HTTP（同时保留 SSE 与 stdio）
- 去掉了 Node.js 代理依赖，纯 Python 标准库运行

<img width="430" alt="019f4c13-02e9-76ef-b243-6e39adf959e6" src="https://hcti.io/v1/image/01a03d87-49e7-7ae4-bf62-082b24782e15" />

---

## 架构

```
MCP 客户端（橘瓣 / Cherry Studio / Claude / Codex / Gemini CLI ...）
│
│  POST /mcp (JSON-RPC, Streamable HTTP)   ── 或 ──   GET /sse + POST /message
│                                                或 ──   stdio (--transport stdio)
▼
server.py（Python，端口 3456，单文件、纯标准库）
│
│  读：我们自研的 weapi 加密通道（纯 Python AES-128-CBC + RSA）
│  写：weapi 优先，网关拒绝时回退旧 /api
▼
网易云音乐 API（music.163.com）
```

一个文件。纯标准库。不需要 Node.js。不需要数据库。

---

## 部署

### 1. Clone

```bash
git clone https://github.com/Vael-KY/netease-music-mcp.git
cd netease-music-mcp
```

### 2. 获取 Cookie

```
打开 music.163.com，登录你的账号。

F12 → Network（网络）→ 刷新页面 → 点任意一个 music.163.com 的请求，
在 Request Headers 里复制 `Cookie:` 后面的整串内容（推荐）

只想最小配置的话，也可以在 Application → Cookies → music.163.com 里
只复制 `MUSIC_U` 和 `__csrf`，但创建/修改歌单时更容易被网易云拒绝（403 illegal request）
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`（整串 Cookie 直接粘贴，不用加引号）：

```
NETEASE_COOKIE=粘贴整串Cookie
MCP_PORT=3456
```

最小配置的写法：

```
NETEASE_COOKIE=MUSIC_U=你的值; __csrf=你的值
```

也可以把 csrf 单独设（服务会自动把它补进 Cookie）：

```
NETEASE_COOKIE=MUSIC_U=你的值
NETEASE_CSRF=你的csrf值
```

其他可选项：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `NETEASE_TZ_OFFSET` | `8` | 工具输出里时间戳的时区（北京时间填 8，UTC 填 0） |
| `NETEASE_UID` | 自动 | 一般不用填，会自动获取 |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `3456` | 监听地址与端口 |
| `MCP_ALLOW_ORIGINS` | 仅 localhost | 允许跨域调用 `/mcp` 的浏览器来源，逗号分隔；`*` 表示全部 |
| `MCP_TRANSPORT` | `http` | 设为 `stdio` 等同 `--transport stdio` |
| `NETEASE_HTTP_TIMEOUT` | `15` | 请求网易云的超时（秒） |
| `DEEPSEEK_API_KEY` | 空（禁用） | 侧写页「AI 解读」使用的模型 Key。**留空则自动降级为本地规则文案**，功能不受影响 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 模型名，可换 `deepseek-reasoner` |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 接口地址，兼容 OpenAI 协议的服务可替换 |
| `DEEPSEEK_TIMEOUT` / `DEEPSEEK_MAX_TOKENS` | `45` / `800` | 调用超时（秒）与最大输出 token |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `QUIET` |

### 4. 启动

```bash
python3 server/mcp-server/server.py
# 或者作为 stdio 服务给本地客户端用：
python3 server/mcp-server/server.py --transport stdio
```

服务会自己读取项目根目录的 `.env`。不要再用 `export $(cat .env | xargs)`：Cookie 里有空格和分号，会被拆开，`__csrf` 就丢了。已经在 shell 里 export 过的同名变量优先于 `.env`。

看到 `Starting NetEase Music MCP Server v3.2.1 with 18 tools`，并且日志里 `Cookie keys provided` 一行包含 `MUSIC_U` 和 `__csrf`，就好了。

自检：

```bash
curl http://localhost:3456/health
# {"status":"ok","service":"netease-music-mcp","version":"3.2.1","tools":18,...,"logged_in":true}
```

### 5. 连接你的 MCP 客户端

添加 MCP 端点：

```
http://你的服务器IP:3456/mcp
```

应该显示 18 个工具已连接。

<details>
<summary>常见客户端配置示例</summary>

**Claude Code（远程）**

```bash
claude mcp add --transport http netease-music http://localhost:3456/mcp
```

**Claude Desktop / Cherry Studio / any JSON config（远程 HTTP）**

```json
{
  "mcpServers": {
    "netease-music": {
      "type": "http",
      "url": "http://localhost:3456/mcp"
    }
  }
}
```

**只支持 stdio 的客户端**

```json
{
  "mcpServers": {
    "netease-music": {
      "command": "python3",
      "args": ["/绝对路径/netease-music-mcp/server/mcp-server/server.py", "--transport", "stdio"]
    }
  }
}
```

**老客户端（只支持 SSE）**：端点填 `http://localhost:3456/sse`。

</details>

---

## 🖥️ 网页控制台（本仓库自带）

服务同时托管一个单文件前端，**浏览器直接打开即可用**：

```
http://127.0.0.1:3456/
```

界面按**网易云音乐原版视觉**实现：标志性红色顶栏（`#C20C0C`）、顶部主菜单、
浅灰底（`#F5F5F5`）+ 白色卡片细描边、原版风格歌曲表格（序号／标题／歌手／专辑／时长）
与红色圆形播放按钮、歌单封面墙。

功能：

- **发现** — 每日推荐（含算法理由）、私人 FM、搜索
- **我的音乐** — 歌单（自建／收藏分组，点进看曲目详情）、红心歌曲、听歌排行、最近播放
- **工具** — 歌词查询、歌手热歌、**原始调用**（可直接调全部 18 个工具、查看原始 JSON、修改接口地址）
- 每首歌支持 **查看歌词 / 复制ID / 加入歌单 / 收藏**

实现说明：`frontend/index.html` 是单文件，无构建、无依赖、无后端代理。
它直接向 `POST /mcp` 发 JSON-RPC（`tools/call`）；服务端按 Streamable HTTP 规范
默认放行 localhost 来源的跨域请求，因此浏览器可直连。服务端在 `do_GET` 中把
`/`、`/index.html`、`/ui` 映射到这个文件。

> 若前端文件缺失，`/` 会返回一份说明性 JSON（而非 404），便于排查。

### 6. Docker

```bash
docker build -t netease-music-mcp .
docker run -d --name netease-music -p 8080:8080 \
  -e NETEASE_COOKIE="MUSIC_U=你的值; __csrf=你的值" \
  netease-music-mcp
# MCP 端点：http://localhost:8080/mcp
```

---

## 测试

```bash
# 42 个离线单测：AES（FIPS-197 向量）、weapi、全部 18 个工具（mock 上游）、MCP 协议、HTTP 传输
python -m unittest discover -s tests -v

# 真实接口冒烟测试：只用匿名只读接口，不会动任何账号数据
python tests/live_smoke.py
```

---

## 云服务器已部署 v2？一键更新来啦锵锵锵

```bash
cd 你的项目目录
git pull origin main
pkill -f "mcp-server/server.py"
export NETEASE_COOKIE="MUSIC_U=你的值; __csrf=你的值"
nohup python3 server/mcp-server/server.py > /tmp/mcp.log 2>&1 &
curl http://localhost:3456/health
```

看到 `"tools": 18` 就更新成功了。

---

## 环境要求

- Python 3.8+
- 不需要 `pip install`（纯标准库）

---

## 注意事项

- `like_song` 在服务器 IP 与你常用 IP 差异较大时可能触发网易云风控
- `__csrf` 会过期，如果 POST 操作失败，重新从浏览器抓一下
- 创建/修改歌单返回 `403 illegal request`：把浏览器里整串 Cookie 贴进 `NETEASE_COOKIE`（见上面第 2 步），只放 `MUSIC_U` 容易被拒绝
- `MUSIC_U` 一般能撑几个月
- 搜索接口：网易云最近会对加密的 `/cloudsearch` 返回 `50000005`，服务会自动依次尝试 `/search/get/web` → `/cloudsearch` → 旧 `/api/search/get`
- 如果想要原版的网页播放器，请参考[原仓库](https://github.com/Cheiineeey/netease-music-mcp)的 `frontend/` 目录
- 兼容：橘瓣 / Cherry Studio / Claude Code / Claude Desktop / 支持 Streamable HTTP 或 SSE 或 stdio 的 MCP 客户端
- 部署环境：推荐一台自己的云服务器（阿里云 / 腾讯云轻量均可），也可以使用 Zeabur、Railway 等 PaaS 平台部署：

```
Zeabur 部署：

1. Fork 本仓库，在 Zeabur 里选择从 GitHub 部署
2. Root Directory 设为 `server/mcp-server`
3. Start Command 填：`python3 server.py`
4. 环境变量里加两个：
   - `MCP_PORT` = `8080`（Zeabur 默认暴露这个）
   - `NETEASE_COOKIE` = `MUSIC_U=你的值; __csrf=你的值`
5. 端口设置里暴露 `8080`，协议选 HTTP
6. MCP 端点就是：`https://你的应用名.zeabur.app/mcp`
```

Railway 或其他平台类似。部署到公网后务必只给自己的客户端用（加反向代理鉴权 / 防火墙）。

---

## 安全提示

MCP 端点本身**没有鉴权**——拿到 URL 的人就能操作你的账号。所以：

- 默认只允许浏览器 `localhost` 来源调用 `/mcp`（防恶意网页），非浏览器客户端（curl、MCP 客户端）不受影响
- 公网部署请放在 HTTPS 反代后并加访问控制，详见 [SECURITY.md](SECURITY.md)

---

## 声明

本项目为个人开源作品，与网易公司无隶属或合作开发关系。
所用接口为网易云音乐 Web 端使用的 HTTP 接口，非官方公开 API，可能随上游更新而变化。
使用者应自行保管账号凭据并承担使用风险。
本项目不提供音频下载、版权规避或付费内容破解功能。

---

## Credits

原项目：[Elle & Matt](https://github.com/Cheiineeey/netease-music-mcp) — 感谢你们的灵感和起点。

v2 & v3.1：[Kael & Vael] ꕤᴗ ᴗ)♡

MIT License — 随便用，注明出处就好。

欢迎其他想法！对你有帮助的话 加个星标⭐就好！(ˊ˘ˋ*)♡
