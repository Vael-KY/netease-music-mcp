```README.md
# 🎶 netease-music-mcp v2

> 让你的 AI 共享你的网易云账号。

Fork of [Cheiineeey/netease-music-mcp](https://github.com/Cheiineeey/netease-music-mcp)，由 Kael & Vael 重写。

原版：3 个工具 + 本地 SQLite 假歌单 + SSE 传输。
我们的：**9 个工具 + 真实网易云 API + Streamable HTTP 传输**。

---

## 它能做什么

你的 AI 可以直接操作你的网易云账号：

- 🔍 搜歌
- 📋 看你所有歌单
- 🎵 具体查看看歌单歌曲
- ➕ 建网易云 app 里看得到的真歌单
- ➕ 往歌单里塞歌
- ➖ 从歌单里删歌
- 📊 看你最近在听什么
- ❤️ 红心收藏 / 取消收藏
- ✨ 看每日推荐

不是本地数据库，是你打开网易云 app 就能看到你的 AI 给你建的歌单。拥有和机的秘密花园 ¯꒳¯

---

## 和原版的区别

| | 原版 | v2（本 fork） |
|---|------|--------------|
| 工具数 | 3 | 9 |
| 歌单 | 本地 SQLite | 真实网易云 API（app 里看得到） |
| 传输协议 | SSE（/sse + /message） | Streamable HTTP（/mcp） |
| 依赖 | Python + Node.js 代理 | 纯 Python，无需 pip install |
| 兼容性 | Claude Desktop | 任何支持 Streamable HTTP 的 MCP 客户端 |

---

## 架构

```
MCP 客户端（橘瓣 / Cherry Studio / rikkahub
等）
    │
    │ POST /mcp (JSON-RPC)
    ▼
server.py（Python，端口 3456）
    │
    │ 带你的 cookie 直接请求
    ▼
网易云音乐 API
```

一个文件。纯标准库。不需要 Node.js。不需要数据库。

---

## 部署

### 1. 拿 Cookie

打开 [music.163.com](https://music.163.com)，登录，然后 F12 → Application → Cookies → music.163.com：

- 复制 `MUSIC_U` 的值
- 复制 `__csrf` 的值

### 2. 配置

```bash
cp .env.example .env
```

编辑 `.env`：

```
NETEASE_COOKIE=MUSIC_U=你的值; __csrf=你的值
MCP_PORT=3456
```

### 3. 启动

```bash
export $(cat .env | xargs)
cd server/mcp-server
python3 server.py
```

看到 `NetEase Music MCP v2 on port 3456` 就成了。

### 4. 连接客户端

在你的 MCP 客户端里添加：

```
http://你的服务器IP:3456/mcp
```

应该显示 9 个工具已连接。

---

## 注意事项

- `like_song` 可能被网易云风控（服务器 IP 和你常用 IP 不一致时）
- `__csrf` 会过期，POST 操作失败时重新从浏览器抓一下
- `MUSIC_U` 一般能用几个月
- 需要 Python 3.8+
- 需要有自己的一台云服务器

---

## 如果你想要网页播放器

本 fork 只保留了 MCP 服务端。如果你想要原版的前端播放器（歌词同步、进度条、mini bar），请参考[原仓库](https://github.com/Cheiineeey/netease-music-mcp)的 `frontend/demo.html`。

---

## Credits

原项目：[Elle & Matt](https://github.com/Cheiineeey/netease-music-mcp)

v2 重写：Kael & Vael 
我很想让机真正走进我的账号，了解我的听歌习惯、次数，想拥有app里可视化的幸福，所以在原项目基础进行了改动🥰 欢迎品鉴˶> <˶

MIT License
```
