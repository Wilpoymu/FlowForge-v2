"""Core: imports, constants, utilities, error classes, shared state."""
import os
import base64
import time
import uuid
import threading
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import queue
from urllib.parse import parse_qs, urlparse
FLOW_SESSION_URL = 'https://labs.google/fx/api/auth/session'
FLOW_UPLOAD_URL = 'https://aisandbox-pa.googleapis.com/v1/flow/uploadImage'
FLOW_GENERATE_URL_TPL = 'https://aisandbox-pa.googleapis.com/v1/projects/{project_id}/flowMedia:batchGenerateImages'
FLOW_CREDITS_URL = 'https://aisandbox-pa.googleapis.com/v1/credits'
FLOW_API_KEY = os.environ.get('FLOW_API_KEY', '')
RECAPTCHA_SITE_KEY = os.environ.get('RECAPTCHA_SITE_KEY', '')

_debug_callback = None
_log_lock = threading.Lock()

def _flow_account_hash(value):
    h = 5381
    for c in value:
        h = (h << 5) + h + ord(c) & 4294967295
    return format(h, '08x')

def set_debug_callback(cb):
    global _debug_callback
    _debug_callback = cb

def _log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    line = f'[{ts}] [Flow] {msg}'
    print(line, flush=True)
    if _debug_callback:
        with _log_lock:
            try:
                _debug_callback(line)
            except Exception:
                pass

def _browser_headers():
    return {'Content-Type': 'text/plain;charset=UTF-8', 'Origin': 'https://labs.google', 'Referer': 'https://labs.google/', 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36', 'x-browser-channel': 'stable', 'x-browser-copyright': 'Copyright 2026 Google LLC. All Rights reserved.', 'x-browser-year': '2026', 'sec-ch-ua': '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"', 'sec-ch-ua-mobile': '?0', 'sec-ch-ua-platform': '"macOS"', 'sec-fetch-dest': 'empty', 'sec-fetch-mode': 'cors', 'sec-fetch-site': 'cross-site'}

class FlowRecaptchaError(RuntimeError):
    pass

class FlowAuthError(RuntimeError):
    pass

class FlowGenerationError(RuntimeError):
    pass
BRIDGE_PORT = 5556
WS_PORT = 5557
_projects_base_dir = ''  # Configurable: base directory for projects

def set_projects_base_dir(path):
    global _projects_base_dir
    _projects_base_dir = path
    _log(f'Projects base dir: {path}')
_TOKEN_MAX_AGE_S = 110
_bridge_server_started = False
_ws_server_started = False
_bridge_bind_ok = False
_ws_bind_ok = False
_bridge_bind_error = ''
_ws_bind_error = ''
_generate_queue = []
_generate_queue_lock = threading.Lock()
_generate_results = {}
_generate_results_lock = threading.Lock()
_generate_results_event = threading.Event()
_throttle_until = 0
_throttle_lock = threading.Lock()


_ws_clients = {}
_ws_clients_lock = threading.Lock()
_http_seen = {}
_http_seen_lock = threading.Lock()
_HTTP_SEEN_TTL = 30.0


def _close_all_recaptcha_sessions():
    pass

def _parse_cookie_string(cookie_str):
    result = {}
    if not cookie_str:
        return result
    for part in cookie_str.split(';'):
        part = part.strip()
        if '=' in part:
            name, value = part.split('=', 1)
            result[name.strip()] = value.strip()
    return result

def save_image_from_url(url, output_path, timeout=60):
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    with open(output_path, 'wb') as f:
        f.write(resp.content)
    return output_path

def save_base64_image(b64_data, output_path):
    img_data = base64.b64decode(b64_data)
    with open(output_path, 'wb') as f:
        f.write(img_data)
    return output_path


# _THROTTLE omitted (in _core)
