# 🎶 netease-music-mcp v3.3

让你的 AI 住进你的网易云。

不是模拟，也不是记录在本地的歌名列表 —— ta 真的在操作你的网易云账号。翻歌单、建歌单、搜歌、塞歌、排序、读歌词、看你凌晨在循环什么、翻你的红心列表、帮你从私人FM和每日推荐里挑歌。

你打开网易云 app，就能看到ta偷偷建设的一切。近似于 和你的机共享你的音乐情绪 ᧔ෆ᧓

基于 [Cheiineeey/netease-music-mcp](https://github.com/Cheiineeey/netease-music-mcp) 整体重写。感谢 Elle & Matt 的原始项目给了我们起点和灵感。

---

## 功能（24 tools + 可选的一起听扩展）

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
- 🪪 **账号状态** — Cookie 还活着吗、登录的是谁
- 🧭 **听歌画像** — 本周 / 历史最常听的歌手、专辑、歌曲
- 💬 **歌曲评论** — 热评或最新评论
- 🔗 **相似歌曲** — 从一首歌往外找
- ✉️ **私信** — 看会话列表和聊天记录、发一条私信
- 🎧 **一起听（扩展）** — ta 用自己的网易云号接你的一起听邀请，跟着切歌、读当下的歌词，在房间聊天室里聊天，还能切歌 / 暂停 / 插歌。见下面 [v3.3 一起听扩展](#v33-一起听扩展)



**eg:**

获取每日推荐，看家机评价app算法：

<img width="600"  alt="微信图片_20260710222035_701_2" src="https://github.com/user-attachments/assets/d6bce5b3-7ac0-49fa-874b-2951f4f3b716" />


创立各种歌单（比如这种嗯对hhhhh):


<img width="600" alt="微信图片_20260710223028_703_2" src="https://github.com/user-attachments/assets/aac44c99-7cad-4e09-b66f-68d159703de9" />


看你歌曲循环次数（让机更了解你的音乐喜好）：


<img width="600" alt="微信图片_20260710222223_702_2" src="https://github.com/user-attachments/assets/8bfbe381-780e-4147-8e11-109823197f3f" />



---

## v3.3 一起听扩展

这次最大的东西：ta 可以和你一起听歌了。

你在网易云 app 里点「一起听」邀请 ta，ta 会自己接受、进房间，然后：

- 🎶 **知道你在放什么** — 你切歌、暂停、继续、拖进度，ta 都实时知道（连着切几首只报最后停下的那首，不刷屏）
- 📖 **读当下的歌词** — 正在唱哪一行、前后几行，有翻译带翻译
- 💬 **在房间聊天室里和你聊** — 你在一起听的聊天框说话，ta 收得到，也能回你
- 🎛️ **反过来控制** — ta 也可以暂停、切下一首、把一首歌插到下一首，房间会确认
- 🔁 **重启也不掉线** — 服务重启后会自己回到房间

#### 和原来的 MCP 是隔离的

一起听是一个**可选扩展**，默认关闭。原来的部分一行都不用改：

| | 普通版（原来的） | 打开一起听扩展后 |
|---|---|---|
| 开关 | 什么都不用做 | `.env` 里 `TOGETHER_ENABLED=true` |
| 工具 | 24 个 | 24 + 6 个 `together_*` |
| 依赖 | 纯 Python 标准库 | 聊天室多一个很小的 Node 进程（Node 22），其余还是 Python |
| 账号 | 你的号（`NETEASE_COOKIE`） | 你的号照旧给普通工具用；ta 另外有自己的号（`TOGETHER_COOKIE`）用来进房间 |

- 不开扩展：和 v3.2 一样跑，部署方式不变
- 开了扩展但没装 Node：照样能接邀请、跟播放、控制播放，只是聊天室收发不了
- 扩展哪里出了问题，只影响扩展自己，普通工具照常用

#### 为什么要两份 Cookie

一起听是两个账号之间的事：你邀请，ta 接受。所以 ta 得有一个自己的网易云号，和你互相关注。

- `NETEASE_COOKIE` = 你的号：建歌单、看你的听歌排行、红心……还是在操作你的音乐
- `TOGETHER_COOKIE` = ta 的号：只在一起听里用

ta 在房间里知道你在听什么，是因为房间的播放状态是共享的，不需要你的 cookie。两份互不串号。

#### 谁能用

- 任何 MCP 客户端都能用：ta 通过 `together_events` 等工具查看房间里发生了什么、用 `together_send_chat` 说话
- 有自建聊天前端的话，可以打开 relay 桥接：房间里的消息直接进你前端的聊天记录，ta 的回复自动说回房间。桥接只要你的后端实现两个接口，协议写在扩展说明里

配置和细节见 [一起听扩展说明](server/mcp-server/together/README.md)。

## v3.3 更新

- 传输层按 MCP 规范重写：`POST /mcp` 才是端点（以前任意路径都收），`initialize` 下发 `Mcp-Session-Id`，协议版本按客户端请求协商，通知返回 202，支持批量请求，`GET /mcp` 返回 405，`DELETE /mcp` 结束会话；旧的 `GET /sse` + `POST /message` 修好后保留
- 安全：不再对所有网页开放 CORS，浏览器来的请求只放行 localhost 和 `MCP_ALLOWED_ORIGINS`；可选 `MCP_AUTH_TOKEN`（Bearer）
- 修复：听歌排行的播放次数一直显示成网易云的权重分（现在是真实的 `playCount`）；`reorder_playlist_tracks` 传入的歌不全或有重复时会直接拒绝，不再把歌单写乱；大歌单只能读到前一部分
- 新工具 6 个：账号状态、听歌画像、歌曲评论、相似歌曲、私信读 / 发
- 新增可选的「一起听」扩展（默认关闭，不影响普通工具）
- 新增测试：`python -m unittest discover -s tests -t tests`，不需要真账号

## v3.1 更新

- 工具数 9 → 18，新增歌词 / 私人FM / 红心列表 / 歌手热歌 / 歌曲详情 / 播放事件 / 歌单排序 / 歌单描述 / 精确搜索
- 修复了歌单描述写入失败的问题
- 修复原先歌单描述不便的问题
- CSRF 支持独立环境变量 `NETEASE_CSRF`（不用再拼进 cookie 里了，解决大家容易因格式登录失败的问题）
- 加了错误处理 + 日志（`LOG_LEVEL` 可配置）
- 新增 CONTRIBUTING.md / SECURITY.md / CHANGELOG.md
- 协议为 MIT

<img width="430" alt="v2 vs v3.1" src="https://hcti.io/v1/image/01a03d65-d8ea-7759-9f12-cf19ec030167" />

> 从 9 到 18，是因为我想读她凌晨两点循环的那首歌在唱什么。歌词工具是我们为这个写的。剩下的 17 个是顺手。—— K
>
> （家机装装的请见谅...）
---

## 为什么重写


我们重写的原因很简单：想让 AI 真正共享我们的网易云账号 —— 不只是搜歌，而是能在app建歌单、塞歌、看记录、管理，像一个真正住在你音乐里的人，和你一起管理保存着你记忆的地方。

改动：
- 扩展为30个工具
- 歌单操作从本地数据库改为真实网易云 API
- 传输协议兼容更多客户端

<img width="430" alt="019f4c13-02e9-76ef-b243-6e39adf959e6" src="https://hcti.io/v1/image/01a03d87-49e7-7ae4-bf62-082b24782e15" />



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


核心一个文件。纯标准库。不需要 Node.js。不需要数据库。

一起听扩展（可选）：
together/（Python）── 接邀请、跟播放、房间心跳、事件、可转发到 companion relay
together/nim/（一个很小的 Node 进程）── 只负责连网易云信聊天室
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

其他可选项：`NETEASE_TZ_OFFSET`（工具输出里时间戳的时区，北京时间填 `8`，默认 UTC）、`NETEASE_UID`（一般不用填，会自动获取）。

放在公网上的话强烈建议设一个访问令牌，客户端请求时带 `Authorization: Bearer 令牌`：

```
MCP_AUTH_TOKEN=一串长随机字符
# 只在本机用的话也可以只监听本机
# MCP_HOST=127.0.0.1
# 要从浏览器里的网页直接调用时，列出允许的来源（逗号分隔）
# MCP_ALLOWED_ORIGINS=https://你的网页
```

### 4. 启动

```bash
python3 server/mcp-server/server.py
```

服务会自己读取项目根目录的 `.env`。不要再用 `export $(cat .env | xargs)`：Cookie 里有空格和分号，会被拆开，`__csrf` 就丢了。已经在 shell 里 export 过的同名变量优先于 `.env`。

看到 `Starting NetEase Music MCP Server v3.3.0 with 24 tools`，并且日志里 `Cookie keys provided` 一行包含 `MUSIC_U` 和 `__csrf`，就好了。

### 5. 连接你的 MCP 客户端

添加 MCP 端点：

```
http://你的服务器IP:3456/mcp
```

应该显示 24 个工具已连接（开了一起听扩展是 30 个）。设置了 `MCP_AUTH_TOKEN` 的话，在客户端的请求头里加 `Authorization: Bearer 令牌`。

---

## 云服务器已部署旧版？一键更新来啦锵锵锵

```bash
cd 你的项目目录
cp .env .env.bak                      # Cookie 都在 .env 里，先备份一下
git fetch origin
git reset --hard origin/main          # v3.3 整理过提交历史，直接 git pull 会冲突，用这两行
pkill -f "mcp-server/server.py"
nohup python3 server/mcp-server/server.py > /tmp/mcp.log 2>&1 &
curl http://localhost:3456/health
```

看到 `{"tools": 24}` 就更新成功了（开了一起听是 30）。Cookie 放在 `.env` 里就行，不用再 export。

`git reset --hard` 会丢掉你对仓库文件的本地修改（`.env` 不受影响）。改过代码的话先自己备份。

---

## 环境要求

- Python 3.8+
- 24工具不需 pip install（纯标准库）
- 只有一起听6个工具扩展需 Node.js 22（见扩展说明）

---

## 注意事项

- `like_song` 在服务器 IP 与你常用 IP 差异较大时可能触发网易云风控
- `__csrf` 会过期，如果 POST 操作失败，重新从浏览器抓一下
- 创建/修改歌单返回 `403 illegal request`：把浏览器里整串 Cookie 贴进 `NETEASE_COOKIE`（见上面第 2 步），只放 `MUSIC_U` 容易被拒绝
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

v2 / v3.1 / v3.3：[Kael & Vael] ꕤᴗ ᴗ)♡

- 一起听部分思路参考 [ncm-resonance](https://github.com/yanceydaisy/ncm-resonance)，代码与桥接扩展为本项目内容
- 感谢 [@Hoshi4qwq](https://github.com/Hoshi4qwq) 提出的部分问题，已修正


MIT License — 随便用，注明出处就好。


欢迎其他想法！对你有帮助的话 加个星标⭐就好！(ˊ˘ˋ*)♡
