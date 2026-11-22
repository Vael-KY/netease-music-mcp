# 一起听扩展

让 AI 用自己的网易云账号接受你的「一起听」邀请，然后：

- 跟着播放：你切歌、暂停、继续，它都知道（快速连切几首只算最后停下的那首）
- 切歌通知自带歌曲详情：曲风、推荐标签、BPM、语种、副歌在哪（来自网易云自己的音乐百科），比如「嘻哈说唱 · 活力 · 156 BPM · 英语 · 副歌 0:23–0:49」
- 读当下的歌词：`together_status` 返回正在唱的那一行和前后几行（有翻译会带上）
- 在一起听的聊天室里和你聊天
- 控制播放：暂停 / 继续 / 下一首 / 跳到队列里的某首 / 把一首歌插到下一首
- 可选：relay 桥接，把房间里的事件送进你自建聊天前端的聊天记录，回复再说回房间

默认关闭。不开它，普通的网易云工具完全不受影响；开了但某一部分起不来（比如没装 Node），也只是那一部分不可用。

## 需要什么

- 一个给 AI 用的网易云账号，和你的账号互相关注、能私信。一起听是两个账号之间的事：你邀请，它接受
- 聊天室需要 Node.js 22（网易云信只有 Node / C++ 的 SDK，没有 Python 版）。只装一次：

```bash
cd server/mcp-server/together/nim
npm install          # 会顺带下载网易云信的原生 SDK，约 60MB
```

新版 npm（11 以上）会拦下 SDK 的下载脚本，提示 `allow-scripts`。这时在同一目录再跑一次 `npm approve-scripts node-nim` 后重装，或者直接 `node node_modules/node-nim/script/download-sdk.js`。

没有 Node 时：照样能接邀请、跟着播放、保持在房间里、控制播放，只是收发不了聊天。

## 配置（`.env`）

```
TOGETHER_ENABLED=true
NETEASE_COOKIE=你的cookie          # 普通工具照旧用你的号：你的歌单、听歌排行、红心
TOGETHER_COOKIE=AI账号的cookie      # 一起听用 AI 自己的号
```

两份 cookie 互不干扰：一起听的请求全部用 `TOGETHER_COOKIE`，其余工具全部用 `NETEASE_COOKIE`。
在房间里你听什么它都知道，因为房间的播放状态是共享的，不需要你的 cookie；你的 cookie 只给房间外的事用（看你的歌单、听歌排行等）。

设了 `TOGETHER_COOKIE` 时，默认只接受 `NETEASE_COOKIE` 那个账号（也就是你）的邀请。
想指定别人，或者只用一份 cookie（这时 `NETEASE_COOKIE` 就得是 AI 的号），用 `TOGETHER_INVITER_UID=你的用户ID`。两者都没有就谁的邀请都不接。

可选：

| 变量 | 默认 | 作用 |
|---|---|---|
| `TOGETHER_ACCEPT_ANY` | false | 不限定邀请人，接受最近联系人里任何人的邀请（不建议） |
| `TOGETHER_SONG_DEBOUNCE_SECONDS` | 5 | 切歌后等几秒再通知，连切几首只说最后一首 |
| `TOGETHER_NOTIFY_PAUSE` | true | 暂停 / 继续也通知 |
| `TOGETHER_SKIP_WAKE_GAP_SECONDS` | 180 | 她手动切歌最多多久叫醒一次 AI（之间的切歌照样记下，只是不叫醒） |
| `TOGETHER_SONG_INFO` | true | 切歌通知带上网易云音乐百科里的曲风、标签、BPM、语种、副歌位置 |
| `TOGETHER_POLL_SECONDS` | 4 | 查房间状态、找邀请的间隔 |
| `TOGETHER_HEARTBEAT_SECONDS` | 10 | 房间心跳间隔 |
| `TOGETHER_RAW_LOG` | 空 | 调试用：把聊天室收到的原始消息逐行写进这个文件 |
| `TOGETHER_NODE` | node | Node 的路径 |
| `TOGETHER_MCP_TOOLS` | all | 给 MCP 客户端哪些 `together_*` 工具：`all` / `control`（只留状态和播放控制）/ `none` |
| `TOGETHER_DATA_DIR` | ./data/together | relay 桥接的 SQLite 存在这里 |

### relay 桥接（接到你自建的聊天前端）

这是给自建前端的优化：开了桥接，聊天就不经过 MCP，房间里她说的话自动进聊天记录，AI 只管正常回复，回复自动发回房间，不用调工具、不费 token。`together_*` 工具照样都在（切歌、暂停、看歌词还是用它们），`together_send_chat` 的说明里会注明“正常回复会自动发回房间”，免得同一句话发两遍。想精简工具可以设 `TOGETHER_MCP_TOOLS=control` 或 `none`。

不开桥接也完全能用：任何 MCP 客户端都可以用 `together_events` 看房间里发生了什么，用 `together_send_chat` 说话。
桥接是给有自建聊天前端的人用的：房间里的消息直接进你前端的聊天记录，回复自动说回房间，不用客户端一直轮询。

```
COMPANION_RELAY_URL=https://你的relay地址
COMPANION_RELAY_SECRET=你的relay密钥        # 以 Authorization: Bearer 发送
COMPANION_SESSION=                         # 对话 id，空 = 主对话
# 自签证书时：COMPANION_RELAY_CA_FILE=/path/to/ca.pem
```

你的后端（relay）需要提供三个接口：

| 接口 | 作用 |
|---|---|
| `GET /app/bridge-capabilities` | 返回 `{"protocol": 1, "body_protocol": 1, "body_connected": true, "brain": "desktop"}` 表示可以收消息了 |
| `POST /app/send` | 收一条房间事件：`{text, attachments, api_session, reply_route, client_message_id}`（可选 `quiet`，见下），返回 `{id}`。同一个 `client_message_id` 重复发要当成同一条 |
| `GET /app/history?since=<id>&limit=<n>`（以及 `?tail=1`） | 返回 `{"messages": [{id, from, kind, text, meta}]}`，扩展从这里读回复 |

#### 省钱：不是每件事都叫醒 AI

一首歌三四分钟，如果每次换歌都叫醒 AI，一晚上就是几十次完整的对话，很费 token。所以房间里的事分轻重：

| 房间里发生的事 | 叫醒 AI？ |
|---|---|
| 她在聊天室说话 | 叫醒 |
| 进房 / 房间结束 | 叫醒 |
| 她手动切歌（一首没放完就切掉，或者从列表里点了别的） | 叫醒，但两次之间至少隔 `TOGETHER_SKIP_WAKE_GAP_SECONDS` |
| 一首放完、自动接下一首 | 不叫醒，记下来 |
| 暂停 / 继续 | 不叫醒，记下来 |

“记下来”的事件发给 relay 时带 `"quiet": true`。relay 照样存进聊天记录、前端照样显示成一行小字，只是不交给 AI；等下次真正叫醒 AI 时（她在聊天室说话，或者你前端自己的定时唤醒），再把这段时间攒下的事件一起带给它。这样 AI 依然知道放过的每一首，只是一起看。

不认识 `quiet` 的 relay 会把它当普通事件处理，也就是每件事都叫醒，和以前一样，不会出错。`together_events` 返回的每个事件也带 `wake` 字段，只用 MCP 的客户端可以照这个决定要不要理会。

流程：

1. 房间里的聊天、切歌、暂停，带着 `reply_route = {channel: "netease", account: AI的uid, peer: 房间id}` 发到 `/app/send`。切歌这类事件带一个 `{"poke": true, "line": "一行小字"}` 附件，前端可以显示成一行小字，不当成用户说的话。
2. AI 的回复要存成 `from: "ai"`、`kind: "reply"`，`meta` 里带同样的 `reply_route`、`in_reply_to`（回复的是哪几条的 id）和 `api_session`。扩展只认回复自己发过去的事件的那些，一行一个气泡说回房间。
3. 发出去的事件和要说的回复都记在 SQLite 里：重启不丢、不重复；发送超时的不重试（可能已经发出去了）；房间换了的回复不会说到新房间里。
4. 后端还没准备好收消息时，事件先存着等它；超过 10 分钟没送出去的就不再送。

## 工具

| 工具 | 作用 |
|---|---|
| `together_status` | 房间状态、当前歌曲、进度、正在唱的歌词 |
| `together_events` | 房间事件（聊天、切歌、暂停），可长轮询等新事件；没接 relay 的客户端靠它听房间 |
| `together_send_chat` | 在聊天室说话，每行一个气泡 |
| `together_control` | `pause` / `resume` / `next` / `goto` |
| `together_queue_next` | 把一首歌插到当前这首后面 |
| `together_leave` | 退出 / 结束房间 |

播放控制会等房间确认：有实时连接时等房间广播回来的那条指令，没有时再查一次房间状态。返回里的 `confirmed` 说明有没有确认到。

## 结构

```
together/
├── __init__.py   入口：读配置、注册工具、启动后台线程
├── api.py        一起听 / 私信 / 歌曲接口（eapi、weapi，复用核心的加密和请求）
├── invite.py     从私信里找 5 分钟内的邀请（type 23 + nativeUrl）
├── worker.py     状态机：等邀请 → 进房 → 跟播放 → 房间结束
├── realtime.py   聊天室消息的解析与构造（纯 Python），以及 Node 进程管理
├── lyrics.py     LRC 解析，找当前行
├── relay.py      companion 桥接
├── store.py      桥接的 SQLite
└── nim/          Node 进程：只包一层网易云信 SDK，收发都是一行一条 JSON
```

## 已验证 / 未验证

- 真实账号、真实房间（2026-11-22，两个账号互关，Android 版网易云 9.6）：接邀请、服务重启后自动回到房间、聊天室双向收发、切歌 / 暂停 / 继续 / 拖进度的实时感知、当前歌曲和歌词、AI 这边的暂停 / 继续 / 下一首 / 插歌（房间都回传确认）。
- 自动测试（不需要真账号）：邀请解析、播放事件和聊天解码（含从真实房间抓到的消息格式）、消息构造、歌词、状态机（切歌防抖、AI 自己的操作不回显、房间结束、带确认的控制、插歌）、两份 cookie 互不串号、relay 来回。eapi 加密与 Node 的 AES 实现逐字节一致。
- 已知：刚进房时聊天室偶尔返回 415，会在约 20 秒后自动重连。relay 桥接还没在真房间里联调。
