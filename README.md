# 🎵 NetEase Music MCP Server

Play music from NetEase Cloud Music through any MCP-compatible AI client (Claude Desktop, custom chat UIs, etc.)

Search songs, manage playlists, stream with synced lyrics — all through natural language.

## Features

- **🎤 Play Music** — Search and play any song on NetEase Cloud Music via MCP tool call
- **📝 Synced Lyrics** — Real-time lyrics display with Chinese translation, draggable progress bar
- **📋 Playlist Management** — Create playlists, add songs, browse and play from playlists
- **🎵 Music Card** — Beautiful card UI with album art, artist, and inline playback controls
- **🎧 Mini Player Bar** — Persistent bottom bar showing current track, tap to expand
- **📱 Mobile-First** — Touch-friendly, keyboard-aware, background playback

## Screenshots

> See `docs/` folder for annotated screenshots showing all features

## Architecture

```
┌─────────────────────┐     ┌──────────────────┐
│  MCP Client         │────▶│  MCP Server      │
│  (Claude, etc.)     │     │  (server.py)     │
└─────────────────────┘     │  - play_music    │
                            │  - list_playlists│
                            │  - add_song      │
                            └──────────────────┘
                                    │
┌─────────────────────┐     ┌──────┴───────────┐
│  Frontend Player    │────▶│  Music API       │
│  (demo.html)        │     │  (api.py)        │
│  - Music Card       │     │  - /music/url    │
│  - Lyric Player     │     │  - /music/lyric  │
│  - Mini Bar         │     │  - /music/proxy  │
│  - Playlist Browser │     │  - /playlists    │
└─────────────────────┘     └──────────────────┘
                                    │
                            ┌──────┴───────────┐
                            │  NetEase Proxy   │
                            │  (Node.js)       │
                            │  Port 3460       │
                            └──────────────────┘
```

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/YOUR_USERNAME/netease-music-mcp.git
cd netease-music-mcp
```

### 2. Set up NetEase Proxy

```bash
cd server/netease-proxy
npm install
node server.js
# Runs on port 3460
```

### 3. Configure Cookie

Copy your NetEase Cloud Music cookie from browser DevTools:

```bash
cp .env.example .env
# Edit .env and paste your cookie
```

### 4. Start MCP Server

```bash
cd server/mcp-server
python3 server.py
# MCP SSE endpoint: http://localhost:3456/sse
```

### 5. Start API Server

```bash
python3 api.py
# API: http://localhost:3457
```

### 6. Open Frontend

```bash
# Serve frontend/demo.html or open in browser
open frontend/demo.html
```

## MCP Client Configuration

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "netease-music": {
      "url": "http://localhost:3456/sse"
    }
  }
}
```

### Custom MCP Client

Connect to `http://YOUR_SERVER:3456/sse` as an SSE MCP server.

## Music Card Format

The `play_music` tool returns a tagged string that frontends can parse and render:

```
[music:SONG_ID:SONG_NAME:ARTIST:COVER_URL]optional note
```

Example:
```
[music:1234567:才二十三:方大同:https://p1.music.126.net/xxx.jpg]送给你的歌
```

Parse with regex:
```javascript
const match = text.match(/\[music:(\d+):([^\[]+?):([^\[]+?):(https?:[^\]|]*)\|?([^\]]*)\]/);
// match[1] = id, match[2] = name, match[3] = artist, match[4] = cover URL
```

## Cookie Guide

1. Open [music.163.com](https://music.163.com) in your browser
2. Log in to your account
3. Open DevTools → Application → Cookies
4. Copy all cookies as a single string
5. Paste into `.env` or `netease_cookie.txt`

> **Note:** VIP songs require a VIP account cookie. Free songs work without login.

## Tech Stack

- **MCP Server:** Python 3, no dependencies
- **API Server:** Python 3, no dependencies
- **NetEase Proxy:** Node.js (for song URL resolution)
- **Frontend:** Vanilla HTML/CSS/JS, no build step
- **Database:** SQLite (playlists)

## License

MIT

## Credits

Built with love by [Elle](https://github.com/Cheiineeey) & 韩屿 🤍
