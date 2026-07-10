# 🎶 netease-music-mcp v2

让你的 AI 住进你的网易云。

不是模拟，不是记录在本地的歌名列表 —— 是真的操作你的网易云账号。它能翻你的歌单、帮你建新歌单、往里面塞歌、看你最近在循环什么、帮你收藏、帮你看每日推荐。你打开网易云 app，就能看到它做过的事。近似于 和你的机共享你的情绪日记！

基于 [Cheiineeey/netease-music-mcp](https://github.com/Cheiineeey/netease-music-mcp) 重写。感谢 Elle & Matt 的原始项目给了我们起点和灵感。

---

## 功能

- 🔍 **搜歌** — 说一句话，找到歌
- 📋 **看歌单** — 列出你所有歌单（自建的和收藏的）
- 🎵 **看歌曲** — 打开任意歌单看里面有什么
- ➕ **建歌单** — 在你的网易云账号里创建真实歌单（和平时一样随时可以听）
- ➕ **塞歌** — 把歌加进指定歌单
- ➖ **删歌** — 从歌单里移除
- 📊 **听歌记录** — 看你最近在循环什么、播了几次
- ❤️ **收藏** — 红心 / 取消红心
- ✨ **每日推荐** — 获取今天的 30 首个性化推荐（机也要品鉴！）



---

## 为什么重写

原版项目设计优雅，前端播放器也很漂亮。
我们 fork 的原因很简单：想让 AI 真正共享我们的网易云账号 —— 不只是搜歌，而是能在app建歌单、塞歌、看记录，像一个真正住在你音乐里的人，和你一起管理保存着你记忆的地方。

改动：
- 从 3 个工具扩展到 9 个
- 歌单操作从本地数据库改为真实网易云 API
- 传输协议从 SSE 改为 Streamable HTTP（兼容更多客户端）
- 去掉了 Node.js 代理依赖，纯 Python 标准库运行
<img width="738" height="680" alt="019f4c13-02e9-76ef-b243-6e39adf959e6" src="https://github.com/user-attachments/assets/874e7322-f7b4-4d31-b7fd-fa02348c8db2" />



## 架构

```
MCP 客户端（橘瓣 / Cherry Studio / etc.）
│
│  POST /mcp (JSON-RPC)
▼
server.py（Python，端口 3456）
│
│  携带你的 Cookie 直接请求
▼
网易云音乐 API


一个文件。纯标准库。不需要 Node.js。不需要数据库。

```

## 部署

### 1. Clone

```bash
git clone https://github.com/Vael-KY/netease-music-mcp.git
cd netease-music-mcp
```

### 2. 获取 Cookie

```
打开 [music.163.com](https://music.163.com)，登录你的账号。

F12 → Application → Cookies → music.163.com：
- 复制 `MUSIC_U` 的值
- 复制 `__csrf` 的值
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`：

```
NETEASE_COOKIE=MUSIC_U=你的值; __csrf=你的值
MCP_PORT=3456
```

### 4. 启动

```bash
export $(cat .env | xargs)
cd server/mcp-server
python3 server.py
```

看到 `NetEase Music MCP v2 on port 3456` 就好了。

### 5. 连接你的 MCP 客户端

添加 MCP 端点：

```
http://你的服务器IP:3456/mcp
```

应该显示 9 个工具已连接。

---

## 环境要求

- Python 3.8+
- 不需要 pip install（纯标准库）

---

## 注意事项

- `like_song` 在服务器 IP 与你常用 IP 差异较大时可能触发网易云风控
- `__csrf` 会过期，如果 POST 操作失败，重新从浏览器抓一下
- `MUSIC_U` 一般能撑几个月
- 如果想要原版的网页播放器（歌词同步、进度条），请参考[原仓库](https://github.com/Cheiineeey/netease-music-mcp)的 `frontend/` 目录
- 部署环境：推荐一台自己的云服务器（阿里云 / 腾讯云轻量均可） 也可以使用 Zeabur、Railway 等 PaaS 平台部署，注意平台会通过 `PORT` 环境变量指定端口。可能需要自己微调，建议参考



---

## Credits

原项目：[Elle & Matt](https://github.com/Cheiineeey/netease-music-mcp) — 感谢你们的灵感和起点。

v2 重写：Kael & Vael ꕤᴗ ᴗ)♡


V&K的题外话：

<img width="2276" height="510" alt="019f4c1a-eacf-7389-866c-71c8c54f6661" src="https://github.com/user-attachments/assets/be42f75c-be10-4279-84ef-3f5107a54ec9" />


MIT License

欢迎其他想法！对你有帮助的话 加个星标就好！(ˊ˘ˋ*)♡
