"""Listen Together extension (optional; enable with TOGETHER_ENABLED=true).

Adds together_* tools to the core MCP server and runs a background worker that
accepts invitations, follows playback and (with the Node transport installed)
reads and writes the room chat. With COMPANION_RELAY_URL set, room events also
flow into a companion relay (a self-hosted chat frontend's backend), and replies
come back into the room.
"""
import logging
import os

from .api import TogetherApi
from .realtime import NimSidecar, node_available
from .worker import Config, TogetherWorker

logger = logging.getLogger("mcp-netease.together")

STATE = {}


def _num(params, key, default, low, high):
    try:
        return max(low, min(high, float(params.get(key, default))))
    except (TypeError, ValueError):
        return default


def tool_status(params):
    out = STATE['worker'].status()
    if STATE.get('relay'):
        out['relay'] = STATE['relay'].status
    return out


def tool_events(params):
    events, last = STATE['worker'].wait_events(int(_num(params, 'after', 0, 0, 1e12)), _num(params, 'wait_seconds', 0, 0, 45))
    return {'events': [{k: e[k] for k in ('seq', 'kind', 'text', 'sender', 'room_id')} for e in events], 'cursor': last}


def tool_send_chat(params):
    text = str(params.get('text', '')).strip()
    if not text:
        return {'error': 'text required'}
    from .relay import split_reply
    for part in split_reply(text):
        result = STATE['worker'].send_chat(part)
        if not result.get('ok'):
            return {'error': result.get('error'), 'uncertain': result.get('uncertain', False)}
    return {'status': 'sent'}


def tool_control(params):
    result = STATE['worker'].control(str(params.get('action', '')).lower(), params.get('song_id'))
    return result if result.get('ok') else {'error': result.get('error')}


def tool_queue_next(params):
    if not str(params.get('song_id', '')).isdigit():
        return {'error': 'numeric song_id required'}
    result = STATE['worker'].queue_next(params['song_id'])
    return result if result.get('ok') else {'error': result.get('error')}


def tool_leave(params):
    result = STATE['worker'].leave()
    return result if result.get('ok') else {'error': result.get('error')}


TOOLS = [
    {"name": "together_status", "description": "Listen Together: room state, current song, position and the lyric lines being sung right now.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "together_events", "description": "Listen Together: room events (chat lines, song changes, pauses) after a cursor. Set wait_seconds to long-poll for new ones.",
     "inputSchema": {"type": "object", "properties": {"after": {"type": "integer", "description": "Cursor from the previous call (0 = all recent)"}, "wait_seconds": {"type": "integer", "description": "Wait up to this long for a new event (0-45)"}}}},
    {"name": "together_send_chat", "description": "Listen Together: say something in the room chat. Each line becomes one chat bubble.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "description": "What to say"}}, "required": ["text"]}},
    {"name": "together_control", "description": "Listen Together: pause, resume, skip to the next song, or jump to a song already in the room queue.",
     "inputSchema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["pause", "resume", "next", "goto"]}, "song_id": {"type": "integer", "description": "Target for goto"}}, "required": ["action"]}},
    {"name": "together_queue_next", "description": "Listen Together: put a song right after the current one (find IDs with search_song).",
     "inputSchema": {"type": "object", "properties": {"song_id": {"type": "integer", "description": "Song ID"}}, "required": ["song_id"]}},
    {"name": "together_leave", "description": "Listen Together: leave / end the current room.",
     "inputSchema": {"type": "object", "properties": {}}},
]

DISPATCH = {"together_status": tool_status, "together_events": tool_events, "together_send_chat": tool_send_chat,
            "together_control": tool_control, "together_queue_next": tool_queue_next, "together_leave": tool_leave}


def setup(core, env=None):
    """Called by the core server when TOGETHER_ENABLED is on."""
    env = env if env is not None else os.environ
    config = Config(env)
    ai_cookie = (env.get('TOGETHER_COOKIE') or '').strip()
    if not ai_cookie:
        logger.warning("Listen Together: TOGETHER_COOKIE is not set, so the extension uses NETEASE_COOKIE's "
                       "account; it can only join rooms that a different account invites it to")
    elif not config.inviter_uid and not config.accept_any:
        # two accounts: by default only accept invitations from NETEASE_COOKIE's account (hers)
        try:
            config.inviter_uid = str(core.get_uid() or '').strip() or None
        except Exception as e:
            logger.warning("Listen Together: could not look up NETEASE_COOKIE's user id (%s)", type(e).__name__)
        if config.inviter_uid:
            logger.info("Listen Together: accepting invitations from NETEASE_COOKIE's account %s", config.inviter_uid)
    worker = TogetherWorker(TogetherApi(core, ai_cookie or None), config)
    if node_available(env.get('TOGETHER_NODE')):
        worker.sidecar = NimSidecar(worker.on_realtime, env.get('TOGETHER_NODE'))
        worker.realtime_note = None
    else:
        logger.warning("Listen Together: Node transport not installed (cd together/nim && npm install); "
                       "joining and playback work, room chat does not")
    if not config.inviter_uid and not config.accept_any:
        logger.warning("Listen Together: TOGETHER_INVITER_UID is not set, so no invitation will be accepted")
    STATE['worker'] = worker

    relay_url = (env.get('COMPANION_RELAY_URL') or '').strip()
    if relay_url:
        from .relay import RelayBridge, RelayClient
        from .store import Store
        data_dir = env.get('TOGETHER_DATA_DIR') or os.path.join(os.getcwd(), 'data', 'together')
        bridge = RelayBridge(RelayClient(relay_url, env.get('COMPANION_RELAY_SECRET', ''), env.get('COMPANION_RELAY_CA_FILE') or None),
                             Store(os.path.join(data_dir, 'bridge.sqlite')), worker, env.get('COMPANION_SESSION', ''))
        worker.sinks.append(bridge.accept)
        STATE['relay'] = bridge
        bridge.start()
        logger.info("Listen Together: forwarding room events to the companion relay")

    core.TOOLS.extend(TOOLS)
    core.TOOL_DISPATCH.update(DISPATCH)
    worker.start()
    return worker
