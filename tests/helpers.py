import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(ROOT, 'server', 'mcp-server')
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

os.environ.setdefault('NETEASE_COOKIE', 'MUSIC_U=test_u; __csrf=test_csrf')
os.environ.setdefault('LOG_LEVEL', 'CRITICAL')


def load_server():
    """A fresh copy of server.py as a module (tests may patch its globals freely)."""
    spec = importlib.util.spec_from_file_location('ncm_server_under_test', os.path.join(SERVER_DIR, 'server.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
