"""MCP over HTTP: a real server on a random local port."""
import http.client
import json
import threading
import unittest

from helpers import load_server


class TransportTest(unittest.TestCase):
    token = ''

    @classmethod
    def setUpClass(cls):
        cls.s = load_server()
        cls.s.AUTH_TOKEN = cls.token
        cls.s.ALLOWED_ORIGINS = {'https://chat.example.com'}
        cls.s.TOOL_DISPATCH['echo'] = lambda params: {'echo': params.get('x')}
        cls.server = cls.s.ThreadedHTTPServer(('127.0.0.1', 0), cls.s.MCPHandler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        h = {'Content-Type': 'application/json'}
        if self.token:
            h['Authorization'] = 'Bearer ' + self.token
        h.update(headers or {})
        conn.request(method, path, body=None if body is None else json.dumps(body), headers=h)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        return resp.status, dict(resp.getheaders()), (json.loads(raw) if raw else None)

    def rpc(self, method, params=None, req_id=1, headers=None):
        msg = {'jsonrpc': '2.0', 'method': method, 'params': params or {}}
        if req_id is not None:
            msg['id'] = req_id
        return self.request('POST', '/mcp', msg, headers)

    def test_root_and_unknown_paths_are_404(self):
        self.assertEqual(self.request('POST', '/', {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'})[0], 404)
        self.assertEqual(self.request('GET', '/nope')[0], 404)

    def test_initialize_issues_session_and_negotiates_version(self):
        status, headers, body = self.rpc('initialize', {'protocolVersion': '2025-06-18'})
        self.assertEqual(status, 200)
        self.assertTrue(headers.get('Mcp-Session-Id'))
        self.assertEqual(body['result']['protocolVersion'], '2025-06-18')
        _, _, body = self.rpc('initialize', {'protocolVersion': '1999-01-01'})
        self.assertEqual(body['result']['protocolVersion'], self.s.LATEST_PROTOCOL_VERSION)

    def test_session_lifecycle(self):
        sid = self.rpc('initialize', {'protocolVersion': '2025-06-18'})[1]['Mcp-Session-Id']
        self.assertEqual(self.rpc('ping', headers={'Mcp-Session-Id': sid})[0], 200)
        self.assertEqual(self.request('DELETE', '/mcp', headers={'Mcp-Session-Id': sid})[0], 204)
        self.assertEqual(self.rpc('ping', headers={'Mcp-Session-Id': sid})[0], 404)

    def test_notification_gets_202(self):
        status, _, body = self.rpc('notifications/initialized', req_id=None)
        self.assertEqual((status, body), (202, None))

    def test_batch(self):
        batch = [{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
                 {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                 {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'echo', 'arguments': {'x': 'hi'}}}]
        status, _, body = self.request('POST', '/mcp', batch)
        self.assertEqual(status, 200)
        self.assertEqual([r['id'] for r in body], [1, 2])
        self.assertEqual(json.loads(body[1]['result']['content'][0]['text']), {'echo': 'hi'})

    def test_errors(self):
        self.assertEqual(self.rpc('tools/call', {'name': 'nope'})[2]['error']['code'], -32602)
        self.assertEqual(self.rpc('no/such/method')[2]['error']['code'], -32601)
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        conn.request('POST', '/mcp', body='{oops', headers={'Content-Type': 'application/json',
                                                            'Authorization': 'Bearer ' + self.token})
        resp = conn.getresponse()
        self.assertEqual((resp.status, json.loads(resp.read())['error']['code']), (400, -32700))
        self.assertEqual(self.rpc('ping', headers={'MCP-Protocol-Version': '1999-01-01'})[0], 400)

    def test_get_mcp_is_405(self):
        self.assertEqual(self.request('GET', '/mcp')[0], 405)

    def test_origin_checks(self):
        self.assertEqual(self.rpc('ping', headers={'Origin': 'https://evil.example'})[0], 403)
        status, headers, _ = self.rpc('ping', headers={'Origin': 'http://localhost:5173'})
        self.assertEqual((status, headers.get('Access-Control-Allow-Origin')), (200, 'http://localhost:5173'))
        self.assertEqual(self.rpc('ping', headers={'Origin': 'https://chat.example.com'})[0], 200)

    def test_health_is_open(self):
        status, _, body = self.request('GET', '/health', headers={'Authorization': ''})
        self.assertEqual((status, body['status']), (200, 'ok'))

    def test_legacy_sse(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        conn.request('GET', '/sse', headers={'Authorization': 'Bearer ' + self.token})
        stream = conn.getresponse()
        self.assertEqual(stream.status, 200)
        stream.readline()
        endpoint = stream.readline().decode().split('data: ', 1)[1].strip()
        self.assertTrue(endpoint.startswith('/message?sessionId='))
        stream.readline()
        status, _, _ = self.request('POST', endpoint, {'jsonrpc': '2.0', 'id': 7, 'method': 'ping'})
        self.assertEqual(status, 202)
        self.assertEqual(stream.readline().decode().strip(), 'event: message')
        msg = json.loads(stream.readline().decode().split('data: ', 1)[1])
        self.assertEqual((msg['id'], msg['result']), (7, {}))
        conn.close()


class AuthTransportTest(TransportTest):
    token = 's3cret-token'

    def test_missing_or_wrong_token_is_401(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        conn.request('POST', '/mcp', body='{}', headers={'Content-Type': 'application/json'})
        self.assertEqual(conn.getresponse().status, 401)
        self.assertEqual(self.rpc('ping', headers={'Authorization': 'Bearer wrong'})[0], 401)


if __name__ == '__main__':
    unittest.main()
