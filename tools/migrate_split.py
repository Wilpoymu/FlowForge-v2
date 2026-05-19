"""Migration: split flow_client.py → flow_client/ package.
Run from project root: python tools/migrate_split.py"""
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(BASE, 'engine')
OLD = os.path.join(ENGINE, 'flow_client.py')
PKG = os.path.join(ENGINE, 'flow_client')
BACKUP = os.path.join(ENGINE, 'flow_client.py.bak')

os.makedirs(PKG, exist_ok=True)

with open(OLD, 'r', encoding='utf-8') as f:
    lines = f.readlines()

def L(s, e=None):
    """Lines from s to e (1-indexed, inclusive)."""
    if e is None:
        return ''.join(lines[s-1:])
    return ''.join(lines[s-1:e])

# ── _core.py ─────────────────────────────────────────────
# Imports + constants + utils + error classes + config + shared globals + helpers
core = (
    '"""Core: imports, constants, utilities, error classes, shared state."""\n'
    + L(1, 17)        # imports + URL/API constants
    + '\n' + L(18, 43)  # _log, _browser_headers, _flow_account_hash, etc.
    + '\n' + L(45, 53)  # Error classes (including pass lines)
    + '\n' + L(53, 67)  # BRIDGE_PORT, WS_PORT, set_projects_base_dir, bridge state vars
    + '\n' + L(68, 81)  # _generate_queue, locks, event, _throttle, health fns
    + '\n' + L(146, 151) # _ws_clients, _http_seen, _HTTP_SEEN_TTL
    + '\n' + L(952, 978) # _close_all_recaptcha_sessions, _parse_cookie_string, save_image_*
    + '\n# _THROTTLE omitted (in _core)\n'
)

# ── _auth.py ─────────────────────────────────────────────
auth = (
    '"""Auto-auth: browser token registration and retrieval."""\n'
    'from ._core import _log\n\n'
    + L(152, 173)      # _auto_tokens dicts + helpers (ends at end of get_auto_email)
)

# ── _projects.py ─────────────────────────────────────────
proj = (
    '"""Project management: scan, create, update, list projects."""\n'
    'from ._core import _log, _projects_base_dir, os, json, datetime, timezone\n\n'
    + L(237, 432)      # Project management section (all helpers + _batch_project)
    + '\n_batch_project = {}  # batch_id -> project_name\n'
)

# ── _generation.py ──────────────────────────────────────
gen = (
    '"""Generation: FlowClientInstance + batch_generate."""\n'
    'from ._core import (_log, _browser_headers, _flow_account_hash,\n'
    '    BRIDGE_PORT, WS_PORT, FLOW_API_KEY, RECAPTCHA_SITE_KEY,\n'
    '    _TOKEN_MAX_AGE_S, _generate_queue, _generate_queue_lock,\n'
    '    _generate_results, _generate_results_lock, _generate_results_event,\n'
    '    _throttle_until, _throttle_lock, FlowRecaptchaError, FlowAuthError,\n'
    '    FlowGenerationError, FLOW_SESSION_URL, FLOW_UPLOAD_URL,\n'
    '    FLOW_GENERATE_URL_TPL, FLOW_CREDITS_URL, save_image_from_url,\n'
    '    save_base64_image, _ws_clients, _ws_clients_lock, _http_seen,\n'
    '    _http_seen_lock, _HTTP_SEEN_TTL)\n'
    'from ._auth import get_auto_token, get_auto_email\n'
    'from ._projects import _batch_project\n\n'
    + L(979, 1152)     # FlowClientInstance class (ends before batch_generate)
    + '\n' + L(1153)   # batch_generate  
)

# ── _bridge.py ──────────────────────────────────────────
bridge = (
    '"""Bridge: HTTP/WS servers, SSE, routing, endpoints."""\n'
    'from ._core import (_log, _browser_headers, _flow_account_hash,\n'
    '    BRIDGE_PORT, WS_PORT, _TOKEN_MAX_AGE_S, _bridge_server_started,\n'
    '    _ws_server_started, _bridge_bind_ok, _ws_bind_ok, _bridge_bind_error,\n'
    '    _ws_bind_error, _generate_queue, _generate_queue_lock,\n'
    '    _generate_results, _generate_results_lock, _generate_results_event,\n'
    '    _throttle_until, _throttle_lock, _ws_clients, _ws_clients_lock,\n'
    '    _http_seen, _http_seen_lock, _HTTP_SEEN_TTL, FlowRecaptchaError,\n'
    '    os, json, time, threading, queue, parse_qs, urlparse,\n'
    '    datetime, timezone, FlowGenerationError, save_image_from_url)\n'
    'from ._auth import _register_auto_auth, _auto_tokens, _auto_tokens_lock, _auto_emails\n'
    'from . import _projects\n'
    'from . import _generation\n\n'
    + L(82, 145)       # _kill_processes_on_port, repair_bridge, clear_pending_state
    + '\n' + L(172, 236) # SSE infrastructure + batch state updaters
    + '\n' + L(433, 951) # _BridgeHandler, _cors_headers, _ws_push, _ws_server, _FlowBridgeHandler, _start_bridge_server, get_connected_accounts, _bridge_generate
)

# ── __init__.py ──────────────────────────────────────────
init = '''"""FlowForge v2 — public API. Backward-compatible with old flow_client module."""
from ._core import *

# Auth
from ._auth import *

# Projects
from ._projects import (
    _scan_project_folder, _list_projects, _save_project,
    _create_project, _get_project, _update_project, _batch_project
)

# Generation
from ._generation import (
    FlowClientInstance, batch_generate
)

# Bridge (startup + health)
from ._bridge import (
    _start_bridge_server, _start_ws_server,
    is_bridge_healthy, get_bridge_status, repair_bridge,
    get_connected_accounts, _bridge_bind_ok, _bridge_bind_error,
    _ws_bind_ok, _ws_bind_error, clear_pending_state, _kill_processes_on_port,
    _sse_clients, _sse_lock, _sse_register, _sse_unregister, _sse_broadcast,
    _batch_state, _batch_state_lock,
    _update_batch_progress, _update_batch_status, _update_batch_result, _batch_complete,
    _BridgeHandler, _FlowBridgeHandler, _cors_headers,
    _ws_push_to_client, _ws_drain_queue_for
)
'''

# ── Write all files ──────────────────────────────────────
files = {
    '_core.py': core,
    '_auth.py': auth,
    '_projects.py': proj,
    '_generation.py': gen,
    '_bridge.py': bridge,
    '__init__.py': init,
}

for name, content in files.items():
    path = os.path.join(PKG, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'  {name}: {len(content.splitlines())} lines')

# ── Verify syntax ────────────────────────────────────────
import py_compile, sys
errors = 0
for name in files:
    path = os.path.join(PKG, name)
    try:
        py_compile.compile(path, doraise=True)
    except py_compile.PyCompileError as e:
        print(f'  SYNTAX ERROR in {name}: {e}')
        errors += 1

if errors == 0:
    print('\nAll 6 files syntax-valid. Ready for testing.')
    # Backup original
    import shutil
    shutil.copy2(OLD, BACKUP)
    print(f'Backup saved to {BACKUP}')
else:
    print(f'\n{errors} file(s) have syntax errors.')
    sys.exit(1)
