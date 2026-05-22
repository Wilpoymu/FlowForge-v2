"""Bridge: HTTP/WS servers, SSE, routing, endpoints."""
import uuid
from . import _core as _c
from ._core import (_log, _browser_headers, _flow_account_hash,
    BRIDGE_PORT, WS_PORT, _TOKEN_MAX_AGE_S,
    os, json, time, threading, queue, parse_qs, urlparse,
    datetime, timezone, FlowRecaptchaError, FlowGenerationError,
    save_image_from_url)
# Mutable globals (mirrored from _core via module-level copies + _sync_globals)
_bridge_server_started = _c._bridge_server_started
_ws_server_started = _c._ws_server_started
_bridge_bind_ok = _c._bridge_bind_ok
_ws_bind_ok = _c._ws_bind_ok
_bridge_bind_error = _c._bridge_bind_error
_ws_bind_error = _c._ws_bind_error
_generate_queue = _c._generate_queue
_generate_queue_lock = _c._generate_queue_lock
_generate_results = _c._generate_results
_generate_results_lock = _c._generate_results_lock
_generate_results_event = _c._generate_results_event
_throttle_until = _c._throttle_until
_throttle_lock = _c._throttle_lock
_ws_clients = _c._ws_clients
_ws_clients_lock = _c._ws_clients_lock
_http_seen = _c._http_seen
_http_seen_lock = _c._http_seen_lock
_HTTP_SEEN_TTL = _c._HTTP_SEEN_TTL
FlowRecaptchaError = _c.FlowRecaptchaError
from ._auth import _register_auto_auth, _auto_tokens, _auto_tokens_lock, _auto_emails
from . import _projects
from . import _generation

# Sync mutable globals back to _core (Python copies values on import, doesn't share mutations)
def _sync_globals():
    for attr in ('_bridge_server_started', '_ws_server_started', '_bridge_bind_ok',
                 '_ws_bind_ok', '_bridge_bind_error', '_ws_bind_error',
                 '_generate_queue', '_generate_results', '_throttle_until',
                 '_ws_clients', '_http_seen'):
        setattr(_c, attr, globals().get(attr))
_sync_globals()  # initial sync from _core values

def is_bridge_healthy():
    return bool(_bridge_bind_ok and _ws_bind_ok)

def get_bridge_status():
    return {'bridge_bind_ok': _bridge_bind_ok, 'ws_bind_ok': _ws_bind_ok,
            'bridge_error': _bridge_bind_error, 'ws_error': _ws_bind_error,
            'bridge_port': BRIDGE_PORT, 'ws_port': WS_PORT}

def _kill_processes_on_port(port):
    import subprocess
    import sys as _sys
    killed = 0
    my_pid = os.getpid()
    try:
        if _sys.platform == 'win32':
            out = subprocess.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
            pids = set()
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and f':{port}' in parts[1] and (parts[3] == 'LISTENING'):
                    try:
                        pid = int(parts[4])
                        if pid and pid != my_pid:
                            pids.add(pid)
                    except ValueError:
                        pass
            for pid in pids:
                try:
                    subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True, timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    killed += 1
                except Exception:
                    pass
        else:
            out = subprocess.run(['lsof', '-ti', f'tcp:{port}', '-sTCP:LISTEN'], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                try:
                    pid = int(line.strip())
                    if pid and pid != my_pid:
                        subprocess.run(['kill', '-9', str(pid)], capture_output=True, timeout=5)
                        killed += 1
                except (ValueError, Exception):
                    pass
    except Exception as e:
        _log(f'Kill port {port} failed: {e}')
    return killed

def repair_bridge():
    global _bridge_server_started, _ws_server_started, _bridge_bind_ok, _ws_bind_ok
    global _bridge_bind_error, _ws_bind_error
    killed = 0
    for port in (BRIDGE_PORT, WS_PORT):
        killed += _kill_processes_on_port(port)
    time.sleep(1.5)
    _bridge_server_started = False
    _ws_server_started = False
    _bridge_bind_ok = False
    _ws_bind_ok = False
    _bridge_bind_error = ''
    _ws_bind_error = ''
    _start_bridge_server()
    time.sleep(0.5)
    _sync_globals()
    return {'killed': killed, 'healthy': is_bridge_healthy(), 'status': get_bridge_status()}

def clear_pending_state():
    with _generate_queue_lock:
        dropped_q = len(_generate_queue)
        _generate_queue.clear()
    with _generate_results_lock:
        dropped_r = len(_generate_results)
        _generate_results.clear()
    if dropped_q or dropped_r:
        _log(f'Cleared stale state: {dropped_q} queued requests, {dropped_r} pending results')

        return _auto_emails.get(account_hash, '')

# SSE infrastructure (Server-Sent Events para el dashboard)
_sse_clients = {}       # batch_id -> list of queue.Queue
_sse_lock = threading.Lock()

def _sse_register(batch_id):
    q = queue.Queue()
    with _sse_lock:
        if batch_id not in _sse_clients:
            _sse_clients[batch_id] = []
        _sse_clients[batch_id].append(q)
    return q

def _sse_unregister(batch_id, q):
    with _sse_lock:
        if batch_id in _sse_clients:
            _sse_clients[batch_id] = [x for x in _sse_clients[batch_id] if x is not q]
            if not _sse_clients[batch_id]:
                del _sse_clients[batch_id]

def _sse_broadcast(batch_id, event):
    with _sse_lock:
        for q in _sse_clients.get(batch_id, []):
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

# Batch state tracking (para el dashboard)
_batch_state = {}
_batch_state_lock = threading.Lock()

def _update_batch_progress(batch_id, done, total):
    with _batch_state_lock:
        _batch_state[batch_id] = {'done': done, 'total': total, 'status': 'running'}
    _sse_broadcast(batch_id, {'type': 'progress', 'done': done, 'total': total})

def _update_batch_status(batch_id, idx, status):
    _sse_broadcast(batch_id, {'type': 'item_status', 'index': idx, 'status': status})

def _update_batch_result(batch_id, result):
    _sse_broadcast(batch_id, {'type': 'item_result', **result})

def _batch_complete(batch_id, results):
    with _batch_state_lock:
        if batch_id in _batch_state:
            _batch_state[batch_id]['status'] = 'complete'
    _sse_broadcast(batch_id, {'type': 'complete', 'results': results})
    # Auto-update project stats if this batch belongs to a project
    proj_name = _batch_project.pop(batch_id, None)
    if proj_name and _c._projects_base_dir:
        done = len([r for r in results if r.get('status') == 'done'])
        failed = len([r for r in results if r.get('status') in ('failed', 'error')])
        total = len(results)
        try:
            _projects._update_project(proj_name, {
                'stats': {'prompts_total': total, 'images_generated': done, 'images_failed': failed}
            })
            _log(f'Project {proj_name} stats updated: {done}/{total} done')
        except Exception:
            pass

_batch_project = {}  # batch_id -> project_name


class _BridgeHandler:
    from http.server import BaseHTTPRequestHandler
    BaseClass = BaseHTTPRequestHandler

def _cors_headers(handler):
    origin = handler.headers.get('Origin', '') if hasattr(handler, 'headers') else ''
    allowed_origins = ('https://labs.google', 'https://aistudio.google.com', 'https://gemini.google.com')
    if origin and any((origin == o or origin.endswith('.' + o.split('//')[1]) for o in allowed_origins)):
        handler.send_header('Access-Control-Allow-Origin', origin)
        handler.send_header('Access-Control-Allow-Credentials', 'true')
        handler.send_header('Vary', 'Origin')
    else:
        handler.send_header('Access-Control-Allow-Origin', '*')
    handler.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    handler.send_header('Access-Control-Allow-Headers', 'Content-Type, Access-Control-Request-Private-Network')
    handler.send_header('Access-Control-Allow-Private-Network', 'true')

def _ws_push_to_client(account_hash, request_data):
    with _ws_clients_lock:
        ws = _ws_clients.get(account_hash)
    if ws:
        try:
            msg = json.dumps({'type': 'generate', 'requests': [request_data]})
            ws.send(msg)
            _log(f"WS: pushed request {request_data.get('requestId', '?')[:12]}... to {account_hash}")
            return True
        except Exception as e:
            _log(f'WS: push failed for {account_hash}: {e}')
            with _ws_clients_lock:
                if _ws_clients.get(account_hash) is ws:
                    del _ws_clients[account_hash]
    return False

def _ws_drain_queue_for(account_hash):
    with _generate_queue_lock:
        remaining = []
        to_send = []
        for r in _generate_queue:
            if r.get('account_hash', '') == account_hash and len(to_send) < 10:
                to_send.append(r)
            else:
                remaining.append(r)
        _generate_queue[:] = remaining
    for req in to_send:
        _ws_push_to_client(account_hash, req)

def _start_ws_server():
    global _ws_server_started, _ws_bind_ok, _ws_bind_error
    if _ws_server_started:
        return
    try:
        import asyncio
        import websockets
        import websockets.sync.server as ws_sync
    except ImportError:
        _ws_bind_error = 'websockets package not installed'
        _log('WS: websockets not installed, WebSocket bridge disabled')
        _ws_server_started = True
        return

    def _ws_handler(ws):
        account_hash = None
        _log('WS: client connected')
        try:
            for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                msg_type = msg.get('type', '')
                if msg_type == 'register':
                    account_hash = msg.get('account_hash', '')
                    if account_hash:
                        with _ws_clients_lock:
                            _ws_clients[account_hash] = ws
                        _log(f'WS: registered account {account_hash}')
                        _ws_drain_queue_for(account_hash)
                elif msg_type == 'result':
                    rid = msg.get('requestId', '')
                    if rid:
                        with _generate_results_lock:
                            _generate_results[rid] = msg
                        _generate_results_event.set()
                        _log(f"WS: result received for {rid[:12]}... status={msg.get('status', '?')}")
                elif msg_type == 'token_ready':
                    pass
        except Exception as e:
            _log(f'WS: client disconnected: {e}')
        finally:
            if account_hash:
                with _ws_clients_lock:
                    if _ws_clients.get(account_hash) is ws:
                        del _ws_clients[account_hash]
                _log(f'WS: unregistered account {account_hash}')

    def _run_ws():
        global _ws_bind_ok, _ws_bind_error
        try:
            server = ws_sync.serve(_ws_handler, '0.0.0.0', WS_PORT)
            _ws_bind_ok = True
            _log(f'WS: WebSocket server started on port {WS_PORT}')
            server.serve_forever()
        except OSError as e:
            _ws_bind_ok = False
            if 'address already in use' in str(e).lower() or '10048' in str(e):
                _ws_bind_error = f'port {WS_PORT} already in use (zombie Imperio process?)'
                _log(f'WS: port {WS_PORT} already in use — bridge will NOT receive browser registrations')
            else:
                _ws_bind_error = str(e)
                _log(f'WS: server failed: {e}')
    t = threading.Thread(target=_run_ws, daemon=True)
    t.start()
    _ws_server_started = True
    time.sleep(0.3)
    _sync_globals()

class _FlowBridgeHandler(_BridgeHandler.BaseClass):

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def _json_response(self, code, data):
        self.send_response(code)
        _cors_headers(self)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_POST(self):
        if self.path == '/api/projects':
            body = self._read_body()
            name = body.get('name', '').strip()
            title = body.get('title', '').strip()
            base_dir = body.get('base_dir', '')
            if not name:
                self._json_response(400, {'error': 'project name required'})
                return
            try:
                proj = _projects._create_project(name, title or name, base_dir or None)
                self._json_response(201, {'ok': True, 'project': proj})
            except ValueError as e:
                self._json_response(400, {'error': str(e)})
            return
        if self.path == '/api/projects/migrate':
            projects = _projects._list_projects()
            saved = 0
            for p in projects:
                try:
                    _projects._save_project(p['_name'], p)
                    saved += 1
                except Exception:
                    pass
            self._json_response(200, {'ok': True, 'migrated': saved, 'total': len(projects)})
            return
        if self.path.startswith('/api/projects/'):
            proj_name = self.path.split('/api/projects/')[1].strip()
            if not proj_name:
                self._json_response(400, {'error': 'project name required'})
                return
            body = self._read_body()
            proj = _projects._update_project(proj_name, body)
            if proj:
                self._json_response(200, {'ok': True, 'project': proj})
            else:
                self._json_response(404, {'error': 'project not found'})
            return
        if self.path == '/api/auth/auto':
            body = self._read_body()
            account_hash = body.get('account_hash', '')
            access_token = body.get('access_token', '')
            email = body.get('email', '')
            name = body.get('name', '')
            if account_hash and access_token:
                _register_auto_auth(account_hash, access_token, email, name)
                self._json_response(200, {'ok': True, 'account': account_hash})
            else:
                self._json_response(400, {'ok': False, 'error': 'missing fields'})
            return
        if self.path == '/api/generate':
            body = self._read_body()
            prompts_raw = body.get('prompts', '')
            output_folder = body.get('output_folder', '')
            accounts = body.get('accounts', [])
            model = body.get('model', 'NARWHAL')
            if not prompts_raw or not output_folder:
                self._json_response(400, {'ok': False, 'error': 'prompts and output_folder required'})
                return
            prompts = [p.strip() for p in prompts_raw.split('\n') if p.strip()]
            if not prompts:
                self._json_response(400, {'ok': False, 'error': 'no valid prompts'})
                return
            # Construir clients desde auto-auth (snapshot bajo lock, construir fuera)
            client_specs = []
            with _auto_tokens_lock:
                target_hashes = list(accounts) if accounts else list(_auto_tokens.keys())
                for ah in target_hashes:
                    token = _auto_tokens.get(ah)
                    email = _auto_emails.get(ah, '')
                    if token:
                        client_specs.append((ah, email, token))
            clients = []
            for ah, email, _token in client_specs:
                client = _generation.FlowClientInstance(
                    cookie_string='',
                    label=email or ah,
                    account_hash=ah
                )
                if not client.project_id:
                    client.create_project()
                clients.append(client)
            if not clients:
                self._json_response(400, {'ok': False, 'error': 'No hay cuentas conectadas. Abre Flow en Chrome.'})
                return
            batch_id = str(uuid.uuid4())
            # Asociar batch a proyecto si se especifica
            project_name = body.get('project', '').strip()
            if project_name:
                _batch_project[batch_id] = project_name
                # Si no se especificó output_folder, usar la carpeta images del proyecto
                if not output_folder and _c._projects_base_dir:
                    proj = _projects._get_project(project_name)
                    if proj:
                        images_rel = proj.get('files', {}).get('images_dir', 'images')
                        output_folder = os.path.join(_c._projects_base_dir, project_name, images_rel)
                        _log(f'Using project images dir: {output_folder}')
            # Leer concurrencia del request (default: una por cuenta, máx 20)
            concurrency_req = body.get('concurrency', None)
            if concurrency_req is not None:
                concurrency_req = max(1, min(int(concurrency_req), 20))
            else:
                concurrency_req = min(len(clients), 10)

            # Referencia de personaje (opcional)
            import base64 as _b64
            ref_images_b64 = body.get('reference_images', None)
            if ref_images_b64 is not None:
                _log(f'POST /api/generate: reference_images recibidas ({len(ref_images_b64)} imagenes)')
                if not isinstance(ref_images_b64, list):
                    self._json_response(400, {'ok': False, 'error': 'reference_images must be a list of base64 strings'})
                    return
                for _i, _r in enumerate(ref_images_b64):
                    if not isinstance(_r, str) or not _r:
                        self._json_response(400, {'ok': False, 'error': f'reference_images[{_i}] must be a non-empty string'})
                        return
                    try:
                        _b64.b64decode(_r, validate=True)
                    except Exception:
                        self._json_response(400, {'ok': False, 'error': f'reference_images[{_i}] no es base64 valido'})
                        return
                _log(f'POST /api/generate: reference_images validadas OK ({len(ref_images_b64)} imagenes)')
            else:
                _log('POST /api/generate: sin reference_images')

            def _run_batch():
                try:
                    _log(f'Batch {batch_id}: iniciando con {len(prompts)} prompts, ref_images={ref_images_b64 is not None}')
                    results = _generation.batch_generate(
                        prompts=prompts,
                        output_folder=output_folder,
                        model=model,
                        concurrency=concurrency_req,
                        clients=clients,
                        filename_prefix='escena_{n}',
                        reference_image_bytes=ref_images_b64 if ref_images_b64 else None,
                        on_progress=lambda done, total: _update_batch_progress(batch_id, done, total),
                        on_status=lambda idx, status: _update_batch_status(batch_id, idx, status),
                        on_result=lambda r: _update_batch_result(batch_id, r)
                    )
                    _batch_complete(batch_id, results)
                except Exception as e:
                    _log(f'Batch {batch_id} failed: {e}')
                    _sse_broadcast(batch_id, {'type': 'error', 'message': str(e)})
            t = threading.Thread(target=_run_batch, daemon=True)
            # Reservar batch_id en _batch_state antes de arrancar el thread
            # para que el SSE no devuelva 404 por carrera
            with _batch_state_lock:
                _batch_state[batch_id] = {'done': 0, 'total': len(prompts), 'status': 'starting'}
            t.start()
            self._json_response(200, {'ok': True, 'batch_id': batch_id, 'total': len(prompts)})
            return
        if self.path == '/flow-generate-result':
            try:
                body = self._read_body()
                rid = body.get('requestId', '')
                if rid:
                    with _generate_results_lock:
                        _generate_results[rid] = body
                    _generate_results_event.set()
                    _log(f"Bridge: result received for {rid[:12]}... status={body.get('status', '?')}")
                self._json_response(200, {'ok': True})
            except Exception:
                self._json_response(400, {'ok': False})
        else:
            self._json_response(404, {'error': 'not found'})

    def do_GET(self):
        if self.path == '/' or self.path == '/app':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            import os as _os
            html_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', '..', 'dashboard', 'index.html')
            try:
                with open(html_path, 'r', encoding='utf-8') as f:
                    self.wfile.write(f.read().encode())
            except FileNotFoundError:
                self.wfile.write(b'<html><body><h1>Dashboard not found</h1></body></html>')
            return
        if self.path.startswith('/dashboard/'):
            filename = self.path.split('/dashboard/')[1]
            import os as _os
            filepath = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', '..', 'dashboard', filename)
            if not _os.path.isfile(filepath):
                self._json_response(404, {'error': 'file not found'})
                return
            ext = filename.rsplit('.', 1)[-1] if '.' in filename else ''
            mime = {'js': 'application/javascript', 'css': 'text/css', 'html': 'text/html'}.get(ext, 'text/plain')
            self.send_response(200)
            self.send_header('Content-Type', mime)
            _cors_headers(self)
            self.end_headers()
            with open(filepath, 'r', encoding='utf-8') as f:
                self.wfile.write(f.read().encode())
            return
        if self.path == '/api/projects':
            projects = _projects._list_projects()
            self._json_response(200, {'projects': projects})
            return
        if self.path.startswith('/api/projects/') and self.path.endswith('/references'):
            import urllib.parse
            proj_name = self.path.split('/api/projects/')[1].rsplit('/references', 1)[0].strip()
            proj_name = urllib.parse.unquote(proj_name)
            if not proj_name:
                self._json_response(400, {'error': 'project name required'})
                return
            try:
                refs = _projects._get_project_references(proj_name)
                self._json_response(200, {'ok': True, 'images': refs})
            except Exception as e:
                proj = _projects._get_project(proj_name)
                if not proj:
                    self._json_response(404, {'error': 'project not found'})
                else:
                    self._json_response(500, {'error': str(e)})
            return
        if self.path.startswith('/api/projects/'):
            proj_name = self.path.split('/api/projects/')[1].strip()
            if not proj_name:
                self._json_response(400, {'error': 'project name required'})
                return
            proj = _projects._get_project(proj_name)
            if proj:
                # Count actual images in images dir
                images_dir = os.path.join(_c._projects_base_dir, proj_name, proj.get('files', {}).get('images_dir', 'images'))
                actual_images = len([f for f in os.listdir(images_dir) if f.endswith('.png')]) if os.path.isdir(images_dir) else 0
                proj['_actual_images'] = actual_images
                self._json_response(200, proj)
            else:
                self._json_response(404, {'error': 'project not found'})
            return
        if self.path.startswith('/api/read-file'):
            filepath = ''
            if '?' in self.path:
                qs = parse_qs(urlparse(self.path).query)
                filepath = qs.get('path', [''])[0]
            if not filepath or not os.path.isfile(filepath):
                self._json_response(404, {'error': 'file not found'})
                return
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                self._json_response(200, {'content': content, 'path': filepath})
            except Exception as e:
                self._json_response(400, {'error': str(e)})
            return
        if self.path == '/api/accounts':
            accounts = []
            seen = set()
            # Auto-auth accounts (have token from extension POST)
            with _auto_tokens_lock:
                for ah in _auto_tokens:
                    seen.add(ah)
                    with _ws_clients_lock:
                        connected = ah in _ws_clients
                    accounts.append({
                        'hash': ah,
                        'email': _auto_emails.get(ah, ''),
                        'connected': connected,
                        'has_token': bool(_auto_tokens.get(ah))
                    })
            # WS-only accounts (connected but no auto-auth token yet)
            with _ws_clients_lock:
                for ah in _ws_clients:
                    if ah not in seen:
                        accounts.append({
                            'hash': ah,
                            'email': '',
                            'connected': True,
                            'has_token': False
                        })
            self._json_response(200, {'accounts': accounts})
            return
        if self.path.startswith('/api/events'):
            batch_id = ''
            if '?' in self.path:
                qs = parse_qs(urlparse(self.path).query)
                batch_id = qs.get('batch', [''])[0]
            if batch_id:
                with _batch_state_lock:
                    if batch_id not in _batch_state:
                        self._json_response(404, {'error': 'batch not found'})
                        return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            _cors_headers(self)
            self.end_headers()
            client_queue = _sse_register(batch_id)
            try:
                import select
                while True:
                    try:
                        event = client_queue.get(timeout=15)
                        try:
                            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            break
                        if event.get('type') == 'complete':
                            break
                    except queue.Empty:
                        try:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            break
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                _sse_unregister(batch_id, client_queue)
            return
        if self.path == '/health':
            self._json_response(200, {'ok': True})
            return
        if self.path.startswith('/flow-register'):
            account = ''
            if '?' in self.path:
                qs = parse_qs(urlparse(self.path).query)
                account = qs.get('account', [''])[0]
            if account:
                with _http_seen_lock:
                    _http_seen[account] = time.time()
                _log(f'Bridge: account registered via HTTP: {account}')
            self._json_response(200, {'ok': True, 'account': account})
            return
        if self.path == '/flow-generate-poll' or self.path.startswith('/flow-generate-poll?'):
            account = ''
            if '?' in self.path:
                qs = parse_qs(urlparse(self.path).query)
                account = qs.get('account', [''])[0]
            if account:
                with _http_seen_lock:
                    _http_seen[account] = time.time()
            reqs = []
            with _generate_queue_lock:
                if account:
                    remaining = []
                    for r in _generate_queue:
                        if r.get('account_hash', '') == account and len(reqs) < 10:
                            reqs.append(r)
                        else:
                            remaining.append(r)
                    _generate_queue[:] = remaining
                else:
                    while _generate_queue and len(reqs) < 10:
                        reqs.append(_generate_queue.pop(0))
            if reqs:
                resp = {'requests': reqs, 'request': reqs[0]}
                self._json_response(200, resp)
            else:
                self._json_response(200, {'requests': [], 'request': None})
        elif self.path == '/flow-bridge-status':
            with _generate_queue_lock:
                pending = len(_generate_queue)
            with _ws_clients_lock:
                ws_connected = list(_ws_clients.keys())
            self._json_response(200, {'pending': pending, 'ws_clients': ws_connected})
        else:
            self._json_response(404, {'error': 'not found'})

    def do_OPTIONS(self):
        self.send_response(204)
        _cors_headers(self)
        self.end_headers()

    def log_message(self, format, *args):
        pass

def _start_bridge_server():
    global _bridge_server_started, _bridge_bind_ok, _bridge_bind_error
    if _bridge_server_started:
        return
    from http.server import HTTPServer, ThreadingHTTPServer
    try:
        server = ThreadingHTTPServer(('0.0.0.0', BRIDGE_PORT), _FlowBridgeHandler)
        server.timeout = 1
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        _bridge_server_started = True
        _bridge_bind_ok = True
        _log(f'Bridge server started on port {BRIDGE_PORT}')
    except OSError as e:
        _bridge_server_started = True
        _bridge_bind_ok = False
        if 'address already in use' in str(e).lower() or '10048' in str(e):
            _bridge_bind_error = f'port {BRIDGE_PORT} already in use (zombie Imperio process?)'
            _log(f'Bridge server port {BRIDGE_PORT} already in use — extension cannot reach this Imperio instance')
        else:
            _bridge_bind_error = str(e)
            _log(f'Bridge server failed to start: {e}')
    _start_ws_server()
    _sync_globals()

def get_connected_accounts():
    with _ws_clients_lock:
        ws_accounts = set(_ws_clients.keys())
    now = time.time()
    with _http_seen_lock:
        stale = [a for a, t in _http_seen.items() if now - t > _HTTP_SEEN_TTL]
        for a in stale:
            del _http_seen[a]
        http_accounts = set(_http_seen.keys())
    return list(ws_accounts | http_accounts)

def _bridge_generate(body_json, bearer, url, timeout=120, account_hash=None):
    _start_bridge_server()
    rid = str(uuid.uuid4())
    req = {'requestId': rid, 'url': url, 'bearer': bearer, 'body': body_json}
    if account_hash:
        req['account_hash'] = account_hash
    pushed = False
    if account_hash:
        pushed = _ws_push_to_client(account_hash, req)
    if not pushed:
        with _generate_queue_lock:
            _generate_queue.append(req)
        _log(f"Bridge: queued request {rid[:12]}... account={account_hash or 'any'} (HTTP fallback)")
    deadline = time.time() + timeout
    while time.time() < deadline:
        _generate_results_event.wait(timeout=2)
        _generate_results_event.clear()
        with _generate_results_lock:
            if rid in _generate_results:
                result = _generate_results.pop(rid)
                return result
    acct_info = f' (account={account_hash})' if account_hash else ''
    _log(f'Bridge: timeout waiting for result {rid[:12]}...{acct_info}')
    raise FlowRecaptchaError(f'No se recibió respuesta del navegador{acct_info}. Abre labs.google/fx/tools/flow en Chrome con la extensión v5.6+.')

