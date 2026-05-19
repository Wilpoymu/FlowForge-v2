"""Generation: FlowClientInstance + batch_generate."""
import os, base64, time, uuid, random, json, threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from ._core import (_log, _browser_headers, _flow_account_hash,
    BRIDGE_PORT, WS_PORT, FLOW_API_KEY, RECAPTCHA_SITE_KEY,
    _TOKEN_MAX_AGE_S, _generate_queue, _generate_queue_lock,
    _generate_results, _generate_results_lock, _generate_results_event,
    _throttle_until, _throttle_lock, FlowRecaptchaError, FlowAuthError,
    FlowGenerationError, FLOW_SESSION_URL, FLOW_UPLOAD_URL,
    FLOW_GENERATE_URL_TPL, FLOW_CREDITS_URL, save_image_from_url,
    save_base64_image, _ws_clients, _ws_clients_lock, _http_seen,
    _http_seen_lock, _HTTP_SEEN_TTL, _close_all_recaptcha_sessions)
from ._auth import get_auto_token, get_auto_email
from ._projects import _batch_project

class FlowClientInstance:

    def __init__(self, cookie_string='', label='', account_hash=None):
        self.cookie_string = cookie_string.strip() if cookie_string else ''
        self.label = label
        self._auth_token = None
        self._auth_expiry = None
        self._auth_lock = threading.Lock()
        self.project_id = None
        self.reference_ids = []
        self._recaptcha_tokens = []
        self._recaptcha_lock = threading.Lock()
        self.account_hash = account_hash
        self.user_email = None

        # Si no hay cookie pero hay account_hash, usar auto-auth de la extensión
        if not self.cookie_string and self.account_hash:
            token = get_auto_token(self.account_hash)
            if token:
                self._auth_token = token
                self._auth_expiry = datetime.now(timezone.utc)  # la extensión mantiene el token fresco
                self.user_email = get_auto_email(self.account_hash)
                _log(self._tag(f'Auto-auth OK via extension. User: {self.user_email}'))

    def _tag(self, msg):
        if self.label:
            return f'[{self.label}] {msg}'
        return msg

    def refresh_auth_token(self):
        if not self.cookie_string:
            raise FlowAuthError('No auth available — abre Flow en Chrome con la extensión')
        _log(self._tag('Refreshing Flow auth token...'))
        resp = requests.get(FLOW_SESSION_URL, headers={'cookie': self.cookie_string, 'Content-Type': 'application/json'}, timeout=30)
        if resp.status_code != 200:
            _log(self._tag(f'Auth failed: {resp.status_code} - {resp.text[:200]}'))
            raise FlowAuthError(f'Flow auth falló: {resp.status_code}')
        data = resp.json()
        if 'error' in data:
            raise FlowAuthError(f"Flow cookie expirada: {data.get('error')}")
        self._auth_token = data.get('access_token')
        if not self._auth_token:
            raise FlowAuthError('No se obtuvo access_token de Flow')
        expires = data.get('expires')
        if expires:
            self._auth_expiry = datetime.fromisoformat(expires.replace('Z', '+00:00'))
        else:
            self._auth_expiry = datetime.now(timezone.utc)
        user = data.get('user', {})
        user_name = user.get('name', 'Unknown')
        user_email = user.get('email', '')
        identity = user_email or user_name
        self.user_email = identity
        self.account_hash = _flow_account_hash(identity)
        _log(self._tag(f'Auth OK. User: {user_name} hash={self.account_hash}'))
        return self._auth_token

    def get_auth_token(self):
        with self._auth_lock:
            # Si tenemos token de auto-auth (sin cookie), usarlo directo
            if not self.cookie_string and self.account_hash:
                token = get_auto_token(self.account_hash)
                if token:
                    self._auth_token = token
                    return token

            if self._auth_token and self._auth_expiry:
                now = datetime.now(timezone.utc)
                expiry = self._auth_expiry if self._auth_expiry.tzinfo else self._auth_expiry.replace(tzinfo=timezone.utc)
                if now < expiry:
                    return self._auth_token
            return self.refresh_auth_token()

    def test_auth(self):
        try:
            token = self.get_auth_token()
            return {'ok': True, 'token_preview': token[:20] + '...' if token else ''}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def _bearer_headers(self):
        token = self.get_auth_token()
        h = _browser_headers()
        h['Authorization'] = f'Bearer {token}'
        return h

    def check_credits(self):
        resp = requests.get(f'{FLOW_CREDITS_URL}?key={FLOW_API_KEY}', headers=self._bearer_headers(), timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data.get('credits', 0)
        return -1

    def create_project(self):
        pid = str(uuid.uuid4())
        self.project_id = pid
        _log(self._tag(f'Created project: {pid}'))
        return pid

    def upload_reference(self, image_bytes_b64, project_id=None, filename='reference.jpg'):
        pid = project_id or self.project_id
        if not pid:
            pid = self.create_project()
        _log(self._tag(f'Uploading reference image to project {pid}...'))
        body = {'clientContext': {'projectId': pid, 'tool': 'PINHOLE'}, 'imageBytes': image_bytes_b64, 'isUserUploaded': True, 'isHidden': False, 'mimeType': 'image/jpeg', 'fileName': filename}
        headers = self._bearer_headers()
        resp = requests.post(FLOW_UPLOAD_URL, headers=headers, json=body, timeout=60)
        if resp.status_code != 200:
            _log(self._tag(f'Upload failed: {resp.status_code} - {resp.text[:300]}'))
            raise FlowGenerationError(f'Error al subir imagen de referencia: {resp.status_code}')
        data = resp.json()
        media = data.get('media', {})
        name = media.get('name')
        if not name:
            raise FlowGenerationError(f'No se obtuvo media name de la respuesta de upload')
        _log(self._tag(f'Reference uploaded: {name}'))
        self.reference_ids.append(name)
        return name

    def prefetch_recaptcha_tokens(self, count=1, timeout=60):
        from ._bridge import _start_bridge_server
        _start_bridge_server()
        _log(self._tag('Bridge server ready (browser-generate mode)'))
        return []

    def generate_image(self, prompt, aspect_ratio='IMAGE_ASPECT_RATIO_LANDSCAPE', reference_ids=None, seed=None, recaptcha_token=None, model='NARWHAL', project_id=None):
        pid = project_id or self.project_id
        if not pid:
            pid = self.create_project()
        session_id = ';' + str(int(time.time() * 1000))
        batch_id = str(uuid.uuid4())
        if seed is None:
            import random
            seed = random.randint(1, 999999)
        request_body = {'clientContext': {'projectId': pid, 'tool': 'PINHOLE', 'sessionId': session_id}, 'imageModelName': model, 'imageAspectRatio': aspect_ratio, 'structuredPrompt': {'parts': [{'text': prompt}]}, 'seed': seed}
        refs = reference_ids or self.reference_ids
        if refs:
            request_body['imageInputs'] = [{'imageInputType': 'IMAGE_INPUT_TYPE_REFERENCE', 'name': ref_id} for ref_id in refs]
        body = {'clientContext': {'projectId': pid, 'tool': 'PINHOLE', 'sessionId': session_id}, 'mediaGenerationContext': {'batchId': batch_id}, 'useNewMedia': True, 'requests': [request_body]}
        url = FLOW_GENERATE_URL_TPL.format(project_id=pid)
        bearer = self.get_auth_token()
        _log(self._tag(f"Generating image via browser bridge: '{prompt[:60]}...' model={model}"))
        body_json = json.dumps(body)
        from ._bridge import _bridge_generate
        result = _bridge_generate(body_json, bearer, url, timeout=120, account_hash=self.account_hash)
        if result.get('error'):
            raise FlowGenerationError(f"Error del navegador: {result['error']}")
        status = result.get('status', 0)
        resp_body = result.get('body', '')
        _log(self._tag(f'Browser API response: status={status} body={resp_body[:500]}'))
        if status == 403:
            try:
                error_data = json.loads(resp_body) if resp_body else {}
            except Exception:
                error_data = {}
            error_msg = error_data.get('error', {}).get('message', '')
            if 'reCAPTCHA' in error_msg:
                raise FlowRecaptchaError(f'reCAPTCHA rechazado: {error_msg}')
            raise FlowGenerationError(f'Acceso denegado: {error_msg}')
        if status != 200:
            raise FlowGenerationError(f'Error al generar imagen: {status} - {resp_body[:200]}')
        resp_data = json.loads(resp_body)
        data = resp_data
        media_list = data.get('media', [])
        if not media_list:
            raise FlowGenerationError('No se recibieron imágenes en la respuesta')
        result = media_list[0]
        image_data = result.get('image', {})
        gen_data = image_data.get('generatedImage', {})
        fife_url = gen_data.get('fifeUrl')
        media_name = result.get('name')
        gen_prompt = gen_data.get('prompt', prompt)
        dimensions = image_data.get('dimensions', {})
        _log(self._tag(f"Image generated: {media_name} ({dimensions.get('width', '?')}x{dimensions.get('height', '?')})"))
        return {'name': media_name, 'fife_url': fife_url, 'prompt': gen_prompt, 'seed': gen_data.get('seed'), 'model': gen_data.get('modelNameType', model), 'width': dimensions.get('width'), 'height': dimensions.get('height'), 'media_generation_id': gen_data.get('mediaGenerationId')}


def batch_generate(prompts, output_folder, filename_prefix='flow_{n}', aspect_ratio='IMAGE_ASPECT_RATIO_LANDSCAPE', reference_ids=None, model='NARWHAL', on_progress=None, on_status=None, is_cancelled=None, max_retries=2, concurrency=1, clients=None, on_result=None, index_map=None, reference_image_bytes=None):
    global _throttle_until
    os.makedirs(output_folder, exist_ok=True)
    total = len(prompts)
    effective_concurrency = max(1, min(concurrency, 10))
    _log(f'batch_generate: {total} prompts, concurrency={effective_concurrency}, clients={(len(clients) if clients else 0)}')
    from ._bridge import _start_bridge_server, clear_pending_state, get_connected_accounts
    with _throttle_lock:
        _throttle_until = 0
    clear_pending_state()
    cancel_event = threading.Event()
    all_clients = list(clients) if clients else []
    multi_account = len(all_clients) > 1
    _active_clients_lock = threading.Lock()
    _active_clients = list(all_clients)
    _last_rejoin_log = [0.0]

    def _get_client(idx):
        if not all_clients:
            return None
        if not multi_account:
            return all_clients[idx % len(all_clients)]
        try:
            connected_now = set(get_connected_accounts())
        except Exception:
            connected_now = set()
        with _active_clients_lock:
            current_hashes = {c.account_hash for c in _active_clients}
            rejoined = []
            for c in all_clients:
                if c.account_hash in connected_now and c.account_hash not in current_hashes:
                    _active_clients.append(c)
                    rejoined.append(c.label)
            if rejoined and time.time() - _last_rejoin_log[0] > 5:
                _last_rejoin_log[0] = time.time()
                _log(f'Rejoined late-connecting accounts: {rejoined}. Active now: {[c.label for c in _active_clients]}')
                if on_status:
                    on_status(-1, f"✅ {', '.join(rejoined)} se conectó tarde, ya recibe trabajo")
            pool = _active_clients if _active_clients else all_clients
            return pool[idx % len(pool)]
    _start_bridge_server()
    if on_status:
        on_status(-1, 'Esperando extensiones de Flow...')
    _log(f'Starting bridge for {(len(clients) if clients else 0)} clients...')
    seen_clients = set()
    for i in range(min(len(clients) if clients else 1, total)):
        if is_cancelled and is_cancelled():
            cancel_event.set()
            break
        cli = _get_client(i)
        if cli and cli not in seen_clients:
            seen_clients.add(cli)
            if not cli.account_hash:
                try:
                    cli.get_auth_token()
                except Exception as e:
                    _log(f'Pre-auth failed for {cli.label}: {e}')
            if not cli.project_id:
                cli.create_project()
            if reference_image_bytes and (not cli.reference_ids):
                for ref_b64 in reference_image_bytes:
                    cli.upload_reference(ref_b64)
            try:
                cli.prefetch_recaptcha_tokens(count=1, timeout=15)
                if on_status:
                    on_status(-1, f'reCAPTCHA bridge lista ({cli.label})')
            except Exception as e:
                _log(f'Bridge prefetch: {e}')
                if on_status:
                    on_status(-1, f'Esperando tokens... abre Flow en tu navegador')
    if cancel_event.is_set():
        return []
    if clients and len(clients) > 1:
        expected_hashes = {cli.account_hash: cli for cli in clients if cli.account_hash}
        wait_deadline = time.time() + 30
        while time.time() < wait_deadline:
            if is_cancelled and is_cancelled():
                return []
            connected = get_connected_accounts()
            missing = [h for h in expected_hashes if h not in connected]
            if not missing:
                _log(f'All {len(expected_hashes)} extensions connected: {connected}')
                if on_status:
                    on_status(-1, f'Todas las extensiones conectadas ({len(expected_hashes)} cuentas)')
                break
            connected_labels = [expected_hashes[h].label for h in expected_hashes if h in connected]
            missing_labels = [expected_hashes[h].label for h in missing]
            if on_status:
                on_status(-1, f"Esperando extensión de: {', '.join(missing_labels)}... (conectadas: {', '.join(connected_labels) or 'ninguna'})")
            time.sleep(2)
        else:
            connected = get_connected_accounts()
            missing = [h for h in expected_hashes if h not in connected]
            if missing:
                missing_labels = [expected_hashes[h].label for h in missing]
                connected_labels = [expected_hashes[h].label for h in expected_hashes if h in connected]
                _log(f'WARNING: Extensions not connected after 60s: {missing_labels}. Connected: {connected_labels}')
                _log(f'Starting batch with {connected_labels}; will auto-include {missing_labels} as soon as they connect')
                if on_status:
                    on_status(-1, f"⚠️ {', '.join(missing_labels)} aún sin conectar. Empezamos con {', '.join(connected_labels) or '0 cuentas'}; las demás se irán sumando solas.")
                with _active_clients_lock:
                    _active_clients[:] = [cli for cli in all_clients if cli.account_hash in connected]
                if not _active_clients:
                    _log('ERROR: No extensions connected at all yet — keeping all clients in pool, will retry per-task')
                    if on_status:
                        on_status(-1, '❌ Ninguna extensión conectada todavía. Abre Flow en Chrome; reintentaremos automáticamente.')
                    with _active_clients_lock:
                        _active_clients[:] = list(all_clients)
    results = []
    completed_count = [0]
    results_lock = threading.Lock()

    def _worker(i, prompt):
        global _throttle_until
        if cancel_event.is_set():
            return {'index': i + 1, 'prompt': prompt, 'status': 'cancelled', 'error': 'Cancelled by user'}
        with _throttle_lock:
            wait_until = _throttle_until
        now = time.time()
        if wait_until > now:
            _log(f'Task {i + 1} throttled, waiting {wait_until - now:.0f}s...')
            while time.time() < wait_until and (not cancel_event.is_set()):
                time.sleep(1)
        cli = _get_client(i)
        if cli is None:
            return {'index': i + 1, 'prompt': prompt, 'status': 'error', 'error': 'No hay cliente Flow disponible'}
        tag = f'[{cli.label}] ' if cli.label else ''
        file_n = index_map[i] if index_map and i < len(index_map) else i + 1
        if '{n:' in filename_prefix or '{n}' in filename_prefix:
            try:
                filename = filename_prefix.format(n=file_n) + '.png'
            except Exception:
                filename = filename_prefix.replace('{n}', str(file_n)) + '.png'
        else:
            filename = f'{filename_prefix}{file_n}.png'
        output_path = os.path.join(output_folder, filename)
        if os.path.isfile(output_path) and os.path.getsize(output_path) > 100:
            return {'index': file_n, 'prompt': prompt, 'status': 'done', 'output_path': output_path}
        last_error = None
        for attempt in range(max_retries + 1):
            if cancel_event.is_set():
                break
            try:
                import random as _rnd
                seed = _rnd.randint(1, 999999) if attempt > 0 else 0
                refs = reference_ids or (cli.reference_ids if cli else [])
                pid = cli.project_id if cli else None
                gen_result = cli.generate_image(prompt=prompt, aspect_ratio=aspect_ratio, reference_ids=refs, seed=seed if seed > 0 else None, model=model, project_id=pid)
                fife_url = gen_result.get('fife_url')
                if fife_url:
                    save_image_from_url(fife_url, output_path)
                else:
                    raise FlowGenerationError('No se recibió URL de imagen')
                return {'index': file_n, 'prompt': prompt, 'status': 'done', 'output_path': output_path, 'media_id': gen_result.get('name')}
            except FlowRecaptchaError as e:
                last_error = e
                _log(f'reCAPTCHA error task {i + 1} (attempt {attempt + 1}): {e}')
                if attempt < max_retries:
                    wait_rc = 3 * (attempt + 1)
                    for _ in range(wait_rc):
                        if cancel_event.is_set():
                            break
                        time.sleep(1)
                    try:
                        cli.prefetch_recaptcha_tokens(count=1, timeout=30)
                    except Exception:
                        pass
            except FlowGenerationError as e:
                last_error = e
                err_str = str(e)
                is_429 = '429' in err_str or 'RESOURCE_EXHAUSTED' in err_str or 'THROTTLED' in err_str
                if is_429:
                    wait_time = min(15 * (attempt + 1), 60)
                    _log(f'Rate limited (429) task {i + 1} (attempt {attempt + 1}), waiting {wait_time}s...')
                    with _throttle_lock:
                        _throttle_until = max(_throttle_until, time.time() + wait_time)
                    if on_status:
                        try:
                            on_status(i, f'Rate limited, waiting {wait_time}s...')
                        except Exception:
                            pass
                else:
                    wait_time = 5 * (attempt + 1)
                    _log(f'Error task {i + 1} (attempt {attempt + 1}): {e}')
                if attempt < max_retries:
                    for _ in range(wait_time):
                        if cancel_event.is_set():
                            break
                        time.sleep(1)
            except Exception as e:
                last_error = e
                _log(f'Error task {i + 1} (attempt {attempt + 1}): {e}')
                if attempt < max_retries:
                    wait_time = 5 * (attempt + 1)
                    for _ in range(wait_time):
                        if cancel_event.is_set():
                            break
                        time.sleep(1)
        if cancel_event.is_set():
            return {'index': file_n, 'prompt': prompt, 'status': 'cancelled', 'error': 'Cancelled by user'}
        return {'index': file_n, 'prompt': prompt, 'status': 'failed', 'error': str(last_error)}
    try:
        if effective_concurrency <= 1:
            for i, prompt in enumerate(prompts):
                if is_cancelled and is_cancelled():
                    cancel_event.set()
                    results.append({'index': i + 1, 'prompt': prompt, 'status': 'cancelled', 'error': 'Cancelled by user'})
                    continue
                result = _worker(i, prompt)
                results.append(result)
                completed_count[0] += 1
                if on_progress:
                    on_progress(completed_count[0], total)
                if on_status:
                    on_status(i, result.get('status', 'done').capitalize())
                if on_result:
                    on_result(result)
        else:
            with ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
                futures = {}
                for i, prompt in enumerate(prompts):
                    if is_cancelled and is_cancelled():
                        cancel_event.set()
                    if cancel_event.is_set():
                        break
                    future = executor.submit(_worker, i, prompt)
                    futures[future] = i
                    time.sleep(0.5)
                for future in as_completed(futures):
                    if is_cancelled and is_cancelled() and (not cancel_event.is_set()):
                        cancel_event.set()
                    idx = futures[future]
                    try:
                        result = future.result()
                    except Exception as e:
                        file_n = index_map[idx] if index_map and idx < len(index_map) else idx + 1
                        result = {'index': file_n, 'prompt': prompts[idx], 'status': 'failed', 'error': str(e)}
                    with results_lock:
                        results.append(result)
                        completed_count[0] += 1
                    if on_progress:
                        on_progress(completed_count[0], total)
                    if on_status:
                        on_status(idx, result.get('status', 'done').capitalize())
                    if on_result:
                        on_result(result)
                submitted_indices = set(futures.values())
                for i in range(total):
                    if i not in submitted_indices:
                        file_n = index_map[i] if index_map and i < len(index_map) else i + 1
                        results.append({'index': file_n, 'prompt': prompts[i], 'status': 'cancelled', 'error': 'Cancelled by user'})
    except BaseException:
        cancel_event.set()
        _log(f'batch_generate interrupted — signalling workers to stop')
        raise
    _close_all_recaptcha_sessions()
    results.sort(key=lambda r: r.get('index', 0))
    done = len([r for r in results if r['status'] == 'done'])
    failed = len([r for r in results if r['status'] == 'failed'])
    cancelled = len([r for r in results if r['status'] == 'cancelled'])
    _log(f'Batch complete: {done} done, {failed} failed, {cancelled} cancelled')
    return results