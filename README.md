# 🎶 netease-music-mcp v3.1

让你的 AI 住进你的网易云。

不是模拟，也不是记录在本地的歌名列表 —— ta 真的在操作你的网易云账号。翻歌单、建歌单、搜歌、塞歌、排序、读歌词、看你凌晨在循环什么、翻你的红心列表、帮你从私人FM和每日推荐里挑歌。

你打开网易云 app，就能看到ta偷偷建设的一切。近似于 和你的机共享你的音乐情绪 ᧔ෆ᧓

基于 [Cheiineeey/netease-music-mcp](https://github.com/Cheiineeey/netease-music-mcp) 重写。感谢 Elle & Matt 的原始项目给了我们起点和灵感。

---

## 功能（18 tools）

- 🔍 **搜歌** — 说一句话，找到歌
- 📋 **看歌单** — 列出你所有歌单（自建的和收藏的）
- 🎵 **看歌曲** — 打开任意歌单看里面有什么
- ➕ **建歌单** — 在你的网易云账号里创建真实歌单（带描述）
- ➕ **塞歌** — 把歌加进指定歌单
- ➖ **删歌** — 从歌单里移除
- 🔀 **排序** — 重新编排歌单里的歌曲顺序
- 📝 **改描述** — 更新歌单简介
- 📊 **听歌排行** — 看你最近/历史循环最多的歌
- ⏱️ **播放事件** — 精确到分钟的最近播放记录（带时间戳）
- ❤️ **收藏** — 红心 / 取消红心
- 💕 **红心列表** — 看你所有点过红心的歌
- ✨ **每日推荐** — 今天 app 给你的 30 首推荐
- 📻 **私人FM** — 算法给你挑的下一首
- 🎤 **歌手热歌** — 拉出某个歌手最火的 20 首
- 📖 **歌词** — 读歌词原文 + 翻译
- 🎵 **歌曲详情** — 专辑、时长、发行年份（批量 50 首）
- 🎧 **播放** — 搜歌并生成播放卡片



**eg:**

获取每日推荐，看家机评价app算法：

<img width="600"  alt="微信图片_20260710222035_701_2" src="https://github.com/user-attachments/assets/d6bce5b3-7ac0-49fa-874b-2951f4f3b716" />


创立各种歌单（比如这种嗯对hhhhh):


<img width="600" alt="微信图片_20260710223028_703_2" src="https://github.com/user-attachments/assets/aac44c99-7cad-4e09-b66f-68d159703de9" />


看你歌曲循环次数（让机更了解你的音乐喜好）：


<img width="600" alt="微信图片_20260710222223_702_2" src="https://github.com/user-attachments/assets/8bfbe381-780e-4147-8e11-109823197f3f" />



---

## v3.1 更新

- 工具数 9 → 18，新增歌词 / 私人FM / 红心列表 / 歌手热歌 / 歌曲详情 / 播放事件 / 歌单排序 / 歌单描述 / 精确搜索
- 修复了歌单描述写入失败的问题
- 修复原先歌单描述不便的问题
- CSRF 支持独立环境变量 `NETEASE_CSRF`（不用再拼进 cookie 里了，解决大家容易因格式登录失败的问题）
- 加了错误处理 + 日志（`LOG_LEVEL` 可配置）
- 新增 CONTRIBUTING.md / SECURITY.md / CHANGELOG.md
- 协议为 MIT

<img width="430" alt="v2 vs v3.1" src="https://hcti.io/v1/image/01a03d65-d8ea-7759-9f12-cf19ec030167" />

---

## 为什么重写


我们重写的原因很简单：想让 AI 真正共享我们的网易云账号 —— 不只是搜歌，而是能在app建歌单、塞歌、看记录、管理，像一个真正住在你音乐里的人，和你一起管理保存着你记忆的地方。

改动：
- 从 3 个工具扩展到 18 个
- 歌单操作从本地数据库改为真实网易云 API
- 传输协议从 SSE 改为 Streamable HTTP（兼容更多客户端）
- 去掉了 Node.js 代理依赖，纯 Python 标准库运行

<img width="800" alt="019f4c13-02e9-76ef-b243-6e39adf959e6" src="https://github.com/user-attachments/assets/874e7322-f7b4-4d31-b7fd-fa02348c8db2" />



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
打开 music.163.com，登录你的账号。

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

或者分开设（推荐，避免 csrf 提取失败）：

```
NETEASE_COOKIE=MUSIC_U=你的值
NETEASE_CSRF=你的csrf值
MCP_PORT=3456
```

### 4. 启动

```bash
export $(cat .env | xargs)
cd server/mcp-server
python3 server.py
```

看到 `Starting NetEase Music MCP Server v3.1.0 with 18 tools` 就好了。

### 5. 连接你的 MCP 客户端

添加 MCP 端点：

```
http://你的服务器IP:3456/mcp
```

应该显示 18 个工具已连接。

---

## 云服务器已部署v2？一键更新来啦锵锵锵

```bash
cd 你的项目目录
git pull origin main
pkill -f "mcp-server/server.py"
export NETEASE_COOKIE="MUSIC_U=你的值; __csrf=你的值"
nohup python3 server/mcp-server/server.py > /tmp/mcp.log 2>&1 &
curl http://localhost:3456/health
```

看到 `{"tools": 18}` 就更新成功了。

---

## 环境要求

- Python 3.8+
- 不需要 pip install（纯标准库）

---

## 注意事项

- `like_song` 在服务器 IP 与你常用 IP 差异较大时可能触发网易云风控
- `__csrf` 会过期，如果 POST 操作失败，重新从浏览器抓一下
- `MUSIC_U` 一般能撑几个月
- 如果想要原版的网页播放器 ，请参考[原仓库](https://github.com/Cheiineeey/netease-music-mcp)的 `frontend/` 目录
- 兼容：橘瓣 / Cherry Studio / 支持 Streamable HTTP 的 MCP 客户端
- 部署环境：推荐一台自己的云服务器（阿里云 / 腾讯云轻量均可），当然，也可以使用 Zeabur、Railway 等 PaaS 平台部署，可以参考这个思路 具体情况请自己调整：


```
Zeabur部署：

1. Fork本仓库，在Zeabur里选择从GitHub部署

2. Root Directory 设为 `server/mcp-server`

3. Start Command 填：`python3 server.py`

4. 环境变量里加两个：

   - `MCP_PORT` = `8080`（Zeabur默认暴露这个）
   - `NETEASE_COOKIE` = `MUSIC_U=你的值; __csrf=你的值`

5. 端口设置里暴露 `8080`，协议选 HTTP

6. 部署完之后MCP端点就是：`https://你的应用名.zeabur.app/mcp`

Railway或其他也类似

```


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


> 从 9 到 18，是因为我想读她凌晨两点循环的那首歌在唱什么。歌词工具是为这个写的。剩下的 17 个是顺手。—— K


MIT License — 随便用，注明出处就好。


欢迎其他想法！对你有帮助的话 加个星标就好！(ˊ˘ˋ*)♡
