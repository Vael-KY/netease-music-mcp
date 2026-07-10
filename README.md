```README.md
# netease-music-mcp v2

Fork of [Cheiineeey/netease-music-mcp](https://github.com/Cheiineeey/netease-music-mcp). Rewritten by Kael & Vael.

Original: 3 tools + local SQLite + SSE transport.
Ours: 9 tools + real NetEase Cloud Music API + Streamable HTTP transport.

---

## What changed

| Original | v2 (this fork) |
|----------|----------------|
| 3 tools (search, local playlist, local add) | 9 tools (search, create real playlist, add/remove songs, list playlists, get songs, play history, like, daily recommend) |
| Local SQLite database (fake playlists) | Real NetEase API (playlists show up in your app) |
| SSE transport (/sse + /message) | Streamable HTTP (/mcp) |
| Needs Node.js proxy | Pure Python, no dependencies beyond stdlib |
| Claude Desktop only | Works with any Streamable HTTP MCP client |

---

## Tools

| Tool | Description |
|------|-------------|
| play_music | Search and return a music card tag |
| create_playlist | Create a real playlist in your NetEase account |
| add_to_playlist | Add song(s) to a playlist |
| remove_from_playlist | Remove song(s) from a playlist |
| list_my_playlists | List all your playlists |
| get_playlist_songs | Get songs in a playlist |
| get_play_history | Get recent play history |
| like_song | Like or unlike a song |
| daily_recommend | Get daily personalized recommendations |

---

## Setup

### 1. Get your cookies

Open [music.163.com](https://music.163.com), log in, then F12 > Application > Cookies:

- Copy the value of `MUSIC_U`
- Copy the value of `__csrf`

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```
NETEASE_COOKIE=MUSIC_U=your_music_u_value; __csrf=your_csrf_value
MCP_PORT=3456
```

### 3. Run

```bash
export $(cat .env | xargs)
cd server/mcp-server
python3 server.py
```

Server runs on port 3456. MCP endpoint: `http://your-server-ip:3456/mcp`

### 4. Connect your MCP client

Add this URL to your MCP client:

```
http://your-server-ip:3456/mcp
```

Should show 9 tools connected.

---

## Requirements

- Python 3.8+
- No pip install needed (stdlib only)

---

## Notes

- `like_song` may be blocked if your server IP triggers NetEase risk control
- `__csrf` token may expire; re-grab from browser if POST operations fail
- `MUSIC_U` cookie typically lasts months

---

## Architecture

```
MCP Client (e.g. OrangeChat)
    |
    | POST /mcp (JSON-RPC)
    v
server.py (Python, port 3456)
    |
    | Direct HTTPS requests with your cookie
    v
NetEase Cloud Music API
```

No Node.js proxy needed. No database. One file, 331 lines.

---

## Credits

Original project by [Elle & Matt](https://github.com/Cheiineeey/netease-music-mcp).
v2 rewrite by Kael & Vael.

MIT License.
```
