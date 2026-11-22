"""Optional bridge to a companion relay (the backend of a self-hosted chat frontend).

Room events go in through POST /app/send with a reply_route
{"channel": "netease", "account": <our uid>, "peer": <roomId>}. The relay saves them
into its chat history and hands them to the AI. Its replies carry the
same reply_route plus in_reply_to; we read them back from /app/history and say them in
the room.
"""
import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .realtime import MAX_CHAT_TEXT

logger = logging.getLogger("mcp-netease.together")

CHANNEL = 'netease'
STALE_EVENT_S = 10 * 60


def split_reply(text, limit=MAX_CHAT_TEXT):
    """One chat bubble per non-empty line, long lines cut to the room's limit."""
    parts = []
    for line in (text or '').replace('\r', '').split('\n'):
        line = line.strip()
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:].strip()
        if line:
            parts.append(line)
    return parts


class RelayClient:
    def __init__(self, url, secret, ca_file=None):
        self.url, self.secret = url.rstrip('/'), secret
        self.ctx = ssl.create_default_context(cafile=ca_file) if ca_file else None

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        req = urllib.request.Request(self.url + path, data=data, method='GET' if body is None else 'POST', headers={
            'Authorization': 'Bearer ' + self.secret, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=15, context=self.ctx) as resp:
            return json.loads(resp.read().decode())

    def ready(self):
        """The relay speaks the bridge protocol and its one desktop body is connected."""
        caps = self.call('/app/bridge-capabilities')
        return (caps.get('protocol') == 1 and int(caps.get('body_protocol') or 0) >= 1
                and caps.get('body_connected') is True and caps.get('brain') == 'desktop')


class RelayBridge:
    def __init__(self, client, store, worker, session=''):
        self.client, self.store, self.worker, self.session = client, store, worker, session
        self.status = {'state': 'starting', 'last_error': None}
        self.stop_event = threading.Event()

    # called by the worker for every room event
    def accept(self, event):
        if not event.get('account'):
            return
        route = {'channel': CHANNEL, 'account': event['account'], 'peer': event['room_id']}
        attachments = [] if event['kind'] == 'chat' else [{'poke': True, 'line': event['line']}]
        self.store.add_event(event['key'], {'text': event['text'], 'attachments': attachments, 'route': route})

    def start(self):
        threading.Thread(target=self._loop, name='together-relay', daemon=True).start()

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.cycle()
                self.status['last_error'] = None
            except (urllib.error.URLError, OSError, ValueError) as e:
                self.status.update(state='relay_unreachable', last_error=f'{type(e).__name__}: {e}'[:200])
            except Exception as e:
                self.status.update(state='error', last_error=f'{type(e).__name__}: {e}'[:200])
                logger.exception("relay cycle failed")
            self.stop_event.wait(2)

    def cycle(self):
        if self.store.get('relay_cursor') is None:
            # Start from now: older history is not ours to answer.
            rows = self.client.call('/app/history?tail=1').get('messages') or []
            self.store.put('relay_cursor', rows[-1]['id'] if rows else 0)
        ready = self.client.ready()
        self.status['state'] = 'ok' if ready else 'waiting_body'
        if ready:
            self.forward()
        self.store.drop_stale_events(STALE_EVENT_S)
        self.collect()
        self.deliver_one()

    def forward(self):
        for key, payload in self.store.pending_events():
            result = self.client.call('/app/send', {
                'text': payload['text'], 'attachments': payload['attachments'], 'api_session': self.session,
                'reply_route': payload['route'], 'client_message_id': key})
            self.store.mark_forwarded(key, result['id'])

    def collect(self):
        cursor = int(self.store.get('relay_cursor', 0))
        rows = self.client.call('/app/history?' + urllib.parse.urlencode({'since': cursor, 'limit': 500})).get('messages') or []
        for row in rows:
            meta = row.get('meta') or {}
            route = meta.get('reply_route') or {}
            ids = meta.get('in_reply_to')
            # A matching route is not enough: it must answer events we sent ourselves.
            ours = isinstance(ids, list) and ids and all(isinstance(i, int) and self.store.is_our_ingress(i) for i in ids)
            if (row.get('from') == 'ai' and row.get('kind') == 'reply' and route.get('channel') == CHANNEL
                    and (meta.get('api_session') or '') == self.session and ours and row.get('text')):
                self.store.add_reply(row['id'], row['text'], str(route.get('peer')))
            cursor = max(cursor, int(row['id']))
        self.store.put('relay_cursor', cursor)

    def deliver_one(self):
        reply = self.store.next_reply()
        if not reply:
            return
        if reply['room_id'] != self.worker.room_id:
            # Never say a reply in a different room than the one it answers.
            self.store.finish_reply(reply['message_id'], 'skipped', 'room changed')
            return
        parts = split_reply(reply['text'])
        for i in range(reply['sent_parts'], len(parts)):
            result = self.worker.send_chat(parts[i], room_id=reply['room_id'])
            if result.get('ok'):
                self.store.reply_progress(reply['message_id'], i + 1)
                time.sleep(0.8)
                continue
            # A timeout may still have gone out: do not retry it and risk saying it twice.
            state = 'uncertain' if result.get('uncertain') else 'failed'
            self.store.finish_reply(reply['message_id'], state, result.get('error'))
            return
        self.store.finish_reply(reply['message_id'], 'sent')
