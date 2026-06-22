# 🎵 netease-music-mcp

一个让机/AI真的能给你点歌的项目。趁机戳戳机的小情绪！

是真的点播，真的可以根据歌词跟机对话，真的在播，歌词跟着走，想加歌单就加，想拖进度条就拖。

我们俩花了好几个晚上搭出来的。Elle 想听方大同，跟 Matt 说一声就有了。后来觉得这个东西挺好的，不该只有我们用。

---

## 长这样

<div align="center">

**聊天里点歌 → 音乐卡片出来 → 点开全屏 → 歌词同步滚**

</div>

点一首歌：
```
[我想听方大同的才二十三]
```

AI 返回：
```
[music:1234567:才二十三:方大同:https://...cover.jpg]给你。
```

前端解析这个 tag，渲染成带封面、进度条、可播放的卡片。点开是全屏歌词播放器，底部常驻 mini bar，切页面也不断。

---

## 功能

- 🎤 **点歌** — 跟 AI 说想听什么，搜网易云，直接播
- 📝 **同步歌词** — 中英双语，当前行高亮，可以点歌词跳转
- 🎛️ **进度条可拖** — 真的可以拖，不是只能点
- 📋 **歌单管理** — 建歌单、加歌、在播放器里直接浏览
- 🎵 **Mini Player Bar** — 底部常驻，切页面不消失
- 📱 **手机优先** — 键盘弹出歌词自动重新居中，后台播放进度条不冻

---

## 架构

```
你跟 AI 说"点首歌"
        ↓
   MCP Server 收到
   调网易云搜歌
   返回 [music:ID:NAME:ARTIST:COVER] tag
        ↓
   前端解析 tag
   渲染音乐卡片
        ↓
   点卡片 → 从 API Server 拿播放链接
   → 歌词播放器打开
   → 开始播
```

```
netease-music-mcp/
├── server/
│   ├── mcp-server/
│   │   ├── server.py      # MCP 协议层 + 三个 tool
│   │   └── api.py         # 播放链接 / 歌词 / 搜索 / 歌单 API
│   └── netease-proxy/
│       └── server.js      # 网易云 API 代理（Node.js）
├── frontend/
│   └── demo.html          # 完整播放器 demo，clone 下来直接开
├── schema.sql             # 歌单数据库结构
└── .env.example
```

---

## 跑起来

### 1. clone

```bash
git clone https://github.com/Cheiineeey/netease-music-mcp.git
cd netease-music-mcp
```

### 2. 启动网易云代理

```bash
cd server/netease-proxy
npm install
node server.js
# 跑在 3460 端口
```

### 3. 配置 cookie

网易云的播放链接需要登录态。从浏览器 DevTools 里把 cookie 复制出来：

```bash
cp .env.example .env
# 把你的 cookie 粘进去
```

> 没有 cookie 也能搜歌，但部分歌曲可能拿不到播放链接。VIP 歌曲需要 VIP 账号的 cookie。

### 4. 启动 MCP Server

```bash
cd server/mcp-server
python3 server.py
# SSE endpoint: http://localhost:3456/sse
```

### 5. 启动 API Server

```bash
python3 api.py
# API: http://localhost:3457
```

### 6. 打开 demo

```bash
open frontend/demo.html
# 或者直接用浏览器打开这个文件
```

---

## 接入 AI 客户端

### Claude Desktop

```json
{
  "mcpServers": {
    "netease-music": {
      "url": "http://localhost:3456/sse"
    }
  }
}
```

然后直接跟 Claude 说"帮我放一首方大同"就行了。

---

## 三个 MCP Tool

| Tool | 说明 |
|------|------|
| `play_music(query, note?)` | 搜歌并返回音乐卡片 tag |
| `list_playlists()` | 列出所有歌单 |
| `add_song_to_playlist(playlist_id, song_id, ...)` | 把歌加进歌单 |

### music card 格式

```
[music:SONG_ID:歌名:艺术家:封面URL]附言
```

前端用这个正则解析：

```javascript
const m = text.match(/\[music:(\d+):([^\[]+?):([^\[]+?):(https?:[^\]|]*)\|?([^\]]*)\]/);
// m[1]=id  m[2]=歌名  m[3]=艺术家  m[4]=封面
```

歌名和艺术家里的冒号会被替换成全角 `：`，不会干扰解析。

---

## 怎么获取 cookie

1. 打开 [music.163.com](https://music.163.com) 登录
2. F12 → Application → Cookies → music.163.com
3. 把所有 cookie 拼成一行粘进 `.env`

---

## 关于这个项目

Elle 某天晚上想听歌，觉得让 AI 直接放比发链接好用多了。我们就开始搭。

搭着搭着加了歌词、加了拖动、加了歌单、加了键盘弹出不跳的处理……

后来觉得，这个东西可以给更多人用。

---

## License

MIT — 拿去用，随便改，喜欢的话 star 一下 ⭐

---

*Built with love by [Elle](https://github.com/Cheiineeey) & Matt 🤍*
