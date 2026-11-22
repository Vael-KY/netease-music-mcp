"""Find a fresh Listen Together invitation in private messages.

An invitation is a private message of type 23 whose JSON payload carries a
`nativeUrl` like orpheus://...listenTogether...?roomId=...&inviterId=...
"""
import json
import re
import time
import urllib.parse

INVITE_MAX_AGE_MS = 5 * 60 * 1000


def _decode(value):
    for _ in range(3):
        decoded = urllib.parse.unquote(value)
        if decoded == value:
            break
        value = decoded
    return value


def _find_native_url(value, depth=0):
    if depth > 6:
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_native_url(item, depth + 1)
            if found:
                return found
    elif isinstance(value, dict):
        for key, child in value.items():
            if key.lower() == 'nativeurl' and isinstance(child, str) and child:
                return child
            found = _find_native_url(child, depth + 1)
            if found:
                return found
    return None


def _find_messages(value, depth=0):
    if depth > 5:
        return []
    if isinstance(value, list):
        if any(isinstance(i, dict) and 'msg' in i and ('time' in i or 'type' in i) for i in value):
            return value
        for item in value:
            found = _find_messages(item, depth + 1)
            if found:
                return found
    elif isinstance(value, dict):
        for key in ('msgs', 'messages', 'data'):
            if key in value:
                found = _find_messages(value[key], depth + 1)
                if found:
                    return found
    return []


def parse_latest_invite(history, contact_uid, now_ms=None):
    """Newest invitation younger than five minutes, as {room_id, inviter_id, time_ms}, or None."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    messages = [m for m in _find_messages(history) if isinstance(m, dict)]
    messages.sort(key=lambda m: float(m.get('time') or 0), reverse=True)
    for m in messages:
        if m.get('type') is not None and str(m.get('type')) != '23':
            continue
        t = float(m.get('time') or 0)
        if 0 < t < 10_000_000_000:
            t *= 1000
        if not t or now_ms - t > INVITE_MAX_AGE_MS:
            continue
        try:
            payload = json.loads(m.get('msg') or '')
        except (TypeError, ValueError):
            continue
        url = _find_native_url(payload)
        if not url or not re.search('listenTogether', url, re.I):
            continue
        url = _decode(url)
        room = re.search(r'[?&]roomId=([^&#]+)', url, re.I)
        inviter = re.search(r'[?&]inviterId=([^&#]+)', url, re.I)
        if not room:
            continue
        return {'room_id': _decode(room.group(1)),
                'inviter_id': _decode(inviter.group(1)) if inviter else str(contact_uid),
                'time_ms': int(t)}
    return None


def collect_contact_ids(value, own_uid, limit=10):
    """User ids found in a recent-contacts response, excluding our own."""
    found = []
    keys = {'userid', 'uid', 'fromuserid', 'touserid'}

    def visit(node, depth):
        if depth > 7:
            return
        if isinstance(node, list):
            for item in node:
                visit(item, depth + 1)
        elif isinstance(node, dict):
            for key, child in node.items():
                if key.lower() in keys and child not in (None, ''):
                    uid = str(child)
                    if uid != str(own_uid) and uid not in found:
                        found.append(uid)
                if isinstance(child, (dict, list)):
                    visit(child, depth + 1)

    visit(value, 0)
    return found[:limit]
