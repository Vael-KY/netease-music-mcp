"""Same-cases before/after comparison -> docs/quantitative-comparison.md

Runs one request suite against two versions of the server and records, per case,
whether each version behaves as the MCP spec requires:
    upstream  7a9344b   (port 3457)
    current   HEAD      (port 3458)
Everything happens in a single process because the sandbox gives each command a
fresh temp directory.
"""
import json, os, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.request

REPO = r"D:\neteasemusic\netease-music-mcp"
OUT_MD = os.path.join(REPO, "docs", "quantitative-comparison.md")
PY = sys.executable  # 任何 Python 3.8+ 均可
TMP = tempfile.mkdtemp(prefix="nacmp_")


def sh(*a):
    return subprocess.run(a, cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def materialise(rev, dest, port):
    os.makedirs(os.path.join(dest, "server", "mcp-server"), exist_ok=True)
    r = sh("git", "show", "%s:server/mcp-server/server.py" % rev)
    assert r.returncode == 0, r.stderr
    with open(os.path.join(dest, "server", "mcp-server", "server.py"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write(r.stdout)
    with open(os.path.join(dest, ".env"), "w", encoding="utf-8", newline="\n") as f:
        f.write("NETEASE_COOKIE=\nMCP_PORT=%d\nLOG_LEVEL=ERROR\n" % port)
    return dest


UP = materialise("7a9344b", os.path.join(TMP, "up"), 3457)
CUR = materialise("HEAD", os.path.join(TMP, "cur"), 3458)

ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


def start(d):
    return subprocess.Popen([PY, os.path.join(d, "server", "mcp-server", "server.py")],
                            cwd=d, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=ENV)


def req(port, path, body=None, headers=None, method=None, timeout=8):
    url = "http://127.0.0.1:%d%s" % (port, path)
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    r = urllib.request.Request(url, data=data, headers=h, method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception:
        return None, {}, b""


def wait_up(port, n=40):
    for _ in range(n):
        s, _, _ = req(port, "/health", timeout=1.5)
        if s:
            return True
        time.sleep(0.4)
    return False


procs = [start(UP), start(CUR)]
ok_up, ok_cur = wait_up(3457), wait_up(3458)
print("ready: upstream=%s current=%s" % (ok_up, ok_cur), flush=True)

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "cmp", "version": "1"}}}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
NOTIF = {"jsonrpc": "2.0", "method": "notifications/initialized"}
UNK = {"jsonrpc": "2.0", "id": 3, "method": "no/such/method"}

R = []   # (case, expect, up_obs, up_ok, cur_obs, cur_ok)


def add(name, expect, fn):
    def run(port):
        try:
            return fn(port)
        except Exception as e:
            return False, "异常:%s" % type(e).__name__
    uok, uo = run(3457)
    cok, co = run(3458)
    R.append((name, expect, uo, uok, co, cok))
    print("%-32s %-14s up=%-22s %s | cur=%-22s %s" % (
        name, expect, uo, "PASS" if uok else "FAIL", co, "PASS" if cok else "FAIL"), flush=True)


add("POST 到根路径 /", "404（仅 /mcp）",
    lambda p: ((lambda s: (s == 404, "HTTP %s" % s))(req(p, "/", LIST)[0])))
add("POST /mcp", "200",
    lambda p: ((lambda s: (s == 200, "HTTP %s" % s))(req(p, "/mcp", LIST)[0])))
add("initialize 下发会话 ID", "有 Mcp-Session-Id",
    lambda p: ((lambda h: ("Mcp-Session-Id" in h, "有" if "Mcp-Session-Id" in h else "无"))(req(p, "/mcp", INIT)[1])))
add("协议版本协商", "回显 2025-06-18",
    lambda p: ((lambda b: ((json.loads(b).get("result", {}).get("protocolVersion") == "2025-06-18"),
                           str(json.loads(b).get("result", {}).get("protocolVersion"))))(req(p, "/mcp", INIT)[2])))
add("通知 notifications/*", "202 且无响应体",
    lambda p: ((lambda t: (t[0] == 202 and not t[2], "HTTP %s / %d bytes" % (t[0], len(t[2]))))(req(p, "/mcp", NOTIF))))
add("未知方法", "-32601",
    lambda p: ((lambda b: ((json.loads(b).get("error", {}).get("code") == -32601),
                           str(json.loads(b).get("error", {}).get("code"))))(req(p, "/mcp", UNK)[2])))
add("恶意 Origin 跨域", "403 拒绝",
    lambda p: ((lambda s: (s == 403, "HTTP %s" % s))(req(p, "/mcp", LIST, {"Origin": "http://evil.example"})[0])))
add("localhost Origin 跨域", "200 放行",
    lambda p: ((lambda s: (s == 200, "HTTP %s" % s))(req(p, "/mcp", LIST, {"Origin": "http://127.0.0.1:3456"})[0])))
add("批量 JSON-RPC", "2 条响应",
    lambda p: ((lambda b: (isinstance(json.loads(b), list), "数组" if isinstance(json.loads(b), list) else "非数组"))(req(p, "/mcp", [LIST, {"jsonrpc": "2.0", "id": 9, "method": "ping"}])[2])))
add("GET /health", "200",
    lambda p: ((lambda s: (s == 200, "HTTP %s" % s))(req(p, "/health")[0])))

for pr in procs:
    pr.terminate()
time.sleep(0.5)
for pr in procs:
    pr.kill()
shutil.rmtree(TMP, ignore_errors=True)

pu = sum(1 for r in R if r[3])
pc = sum(1 for r in R if r[5])
n = len(R)

lines = []
lines.append("# 改造前后同批用例量化对比\n")
lines.append("同一套请求，分别打到两个版本的服务器，按下表逐条核对。\n")
lines.append("- **上游**：`7a9344b`（二次开发的基线）")
lines.append("- **当前**：本组 `HEAD`\n")
lines.append("复现方式：`python tools/compare_versions.py`（脚本见仓库 `tools/`）\n")
lines.append("## 汇总\n")
lines.append("| 版本 | 通过 | 通过率 |")
lines.append("|---|---|---|")
lines.append("| 上游 `7a9344b` | %d / %d | **%d%%** |" % (pu, n, round(pu / n * 100)))
lines.append("| 当前 `HEAD` | %d / %d | **%d%%** |" % (pc, n, round(pc / n * 100)))
lines.append("")
lines.append("**净提升：+%d 条用例（%d%% → %d%%）**\n" % (pc - pu, round(pu / n * 100), round(pc / n * 100)))
lines.append("## 逐条结果\n")
lines.append("| # | 用例 | 期望行为 | 上游实测 | 上游 | 当前实测 | 当前 |")
lines.append("|---|---|---|---|---|---|---|")
for i, (name, exp, uo, uok, co, cok) in enumerate(R, 1):
    lines.append("| %d | %s | %s | %s | %s | %s | %s |" % (
        i, name, exp, uo, "✅" if uok else "❌", co, "✅" if cok else "❌"))
lines.append("")
lines.append("## 结论\n")
lines.append("上游 %d 条全部不达标，当前 %d 条全部达标。差异集中在传输层与协议层——"
             "这正是本组改造的落点（详见 `before-after.md` 的链路对比）。\n" % (n - pu, pc))
if not (ok_up and ok_cur):
    lines.append("> ⚠️ 本次运行中有服务未就绪，结果可能不完整（upstream=%s current=%s）。\n" % (ok_up, ok_cur))

os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
with open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lines))

print()
print("=" * 74)
print("同批用例 %d 条：上游通过 %d/%d（%d%%）  当前通过 %d/%d（%d%%)" % (
    n, pu, n, round(pu / n * 100), pc, n, round(pc / n * 100)))
print("=" * 74)
print("written:", OUT_MD)