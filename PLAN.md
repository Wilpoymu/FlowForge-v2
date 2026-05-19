# FlowForge v2 — Plan de Migración y Mejora

## Contexto

Se parte de la base de código de **Batchy (Imperio Studio)**, una app Python + extensión Chrome que automatiza la generación de imágenes en Google Flow. El motor (`engine/flow_client.py`) es sólido y probado. El objetivo es:

1. **Eliminar la copia manual de cookies** — la extensión ya puede obtener el token automáticamente
2. **Reemplazar la UI de Imperio Studio** — crear un dashboard web limpio, auto-contenido en el mismo Python
3. **Preparar para N cuentas simultáneas** — el motor ya lo soporta, solo falta la UI que lo refleje

---

## Arquitectura Actual (qué hay)

```
┌──────────────────────┐     ┌─────────────────────────┐
│  Extensión Chrome    │     │  Python (engine/)        │
│                      │     │                          │
│  flow_content.js ────┼──WS─┼▶ Bridge HTTP :5556      │
│  (ISOLATED world)    │◀── ─┤  Bridge WS   :5557      │
│       │              │     │                          │
│       │ postMessage  │     │  FlowClientInstance      │
│       ▼              │     │  ├─ necesita cookie 😞   │
│  flow_token_gen.js   │     │  ├─ batch_generate()     │
│  (MAIN world)        │     │  └─ ThreadPoolExecutor   │
│  ├─ grecaptcha pool  │     │                          │
│  ├─ fetch() API Flow │     │  _FlowBridgeHandler      │
│  └─ session auth ✓   │     │  ├─ /flow-generate-poll  │
└──────────────────────┘     │  ├─ /flow-generate-result│
                             │  └─ /health              │
                             └─────────────────────────┘
```

### Lo que funciona (NO TOCAR)
- `flow_token_gen.js`: reCAPTCHA pool, inyección de token, fetch a Flow API
- `flow_content.js`: WebSocket bridge, HTTP polling fallback
- `engine/flow_client.py`:
  - `FlowClientInstance`: auth, create_project, generate_image
  - `batch_generate()`: queue, concurrency, retry, throttling, cancel
  - `_FlowBridgeHandler`: HTTP bridge server
  - `_ws_handler`: WebSocket bridge server
  - Late-join dinámico de cuentas

### Lo que se modifica
- `flow_token_gen.js`: +15 líneas para auto-auth
- `engine/flow_client.py`: +80 líneas (endpoints API + modificaciones menores)
- Se crea `dashboard/index.html`: ~300 líneas (UI completa)
- Se crea `dashboard/app.js`: ~150 líneas (lógica de UI)

---

## Fase 1: Auto-Auth (Extension → Python)

**Objetivo**: Que la extensión mande el token automáticamente al abrir Flow, sin que el usuario copie nada.

### 1.1 Modificar `extension/flow_token_gen.js`

En la función existente que hace `fetch("/fx/api/auth/session")` (línea ~150), agregar después de obtener el hash:

```javascript
// Después de window.postMessage({type: "FLOW_ACCOUNT_HASH", hash: hash}, "*");
// Agregar:
fetch("http://127.0.0.1:5556/api/auth/auto", {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({
    account_hash: hash,
    access_token: data.access_token,
    email: user.email || "",
    name: user.name || ""
  })
}).catch(function(e) {
  console.log("[FlowForge] Auto-auth failed (server not running?): " + e.message);
});
```

### 1.2 Agregar endpoint en `engine/flow_client.py`

Agregar en la clase `_FlowBridgeHandler`, método `do_POST`:

```python
# --- NUEVO: Auto-Auth endpoint ---
if self.path == '/api/auth/auto':
    body = self._read_body()
    account_hash = body.get('account_hash', '')
    access_token = body.get('access_token', '')
    email = body.get('email', '')
    if account_hash and access_token:
        _register_auto_auth(account_hash, access_token, email)
        self._json_response(200, {'ok': True, 'account': account_hash})
    else:
        self._json_response(400, {'ok': False, 'error': 'missing fields'})
    return
```

Y agregar las funciones/variables globales (fuera de la clase, junto con `_generate_queue`):

```python
# Auto-auth tokens (poblados por la extensión)
_auto_tokens = {}          # account_hash -> access_token
_auto_tokens_lock = threading.Lock()
_auto_emails = {}          # account_hash -> email

def _register_auto_auth(account_hash, access_token, email):
    with _auto_tokens_lock:
        _auto_tokens[account_hash] = access_token
        _auto_emails[account_hash] = email
    _log(f'Auto-auth registrado: {account_hash} ({email})')

def get_auto_token(account_hash):
    with _auto_tokens_lock:
        return _auto_tokens.get(account_hash)

def get_auto_email(account_hash):
    with _auto_tokens_lock:
        return _auto_emails.get(account_hash, '')
```

### 1.3 Modificar `FlowClientInstance.__init__`

Hacer que `cookie_string` sea opcional. Si no se provee cookie, buscar el token del bridge:

```python
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
    
    # Si no hay cookie pero hay account_hash, usar auto-auth
    if not self.cookie_string and self.account_hash:
        token = get_auto_token(self.account_hash)
        if token:
            self._auth_token = token
            self._auth_expiry = datetime.now(timezone.utc)  # la extensión refresca
            self.user_email = get_auto_email(self.account_hash)
            _log(self._tag(f'Auto-auth OK via extension. User: {self.user_email}'))
```

Y modificar `get_auth_token()` para que si ya tenemos token de auto-auth, lo devuelva sin intentar refresh con cookie:

```python
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
        
        if self.cookie_string:
            return self.refresh_auth_token()
        
        raise FlowAuthError('No auth available — abre Flow en Chrome con la extensión')
```

---

## Fase 2: API del Dashboard (Python Bridge)

**Objetivo**: Agregar endpoints REST para que el dashboard HTML pueda encolar trabajos y ver progreso.

### 2.1 Agregar en `_FlowBridgeHandler.do_POST`

```python
if self.path == '/api/generate':
    body = self._read_body()
    prompts_raw = body.get('prompts', '')
    output_folder = body.get('output_folder', '')
    accounts = body.get('accounts', [])  # lista de account_hashes
    model = body.get('model', 'NARWHAL')
    
    if not prompts_raw or not output_folder:
        self._json_response(400, {'ok': False, 'error': 'prompts and output_folder required'})
        return
    
    prompts = [p.strip() for p in prompts_raw.split('\n') if p.strip()]
    if not prompts:
        self._json_response(400, {'ok': False, 'error': 'no valid prompts'})
        return
    
    # Construir clients desde auto-auth
    clients = []
    for ah in (accounts or _auto_tokens.keys()):
        token = get_auto_token(ah)
        email = get_auto_email(ah)
        if token:
            client = FlowClientInstance(
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
    
    # Disparar generación en thread separado
    def run_batch():
        results = batch_generate(
            prompts=prompts,
            output_folder=output_folder,
            model=model,
            concurrency=min(len(clients), 10),
            clients=clients,
            filename_prefix='escena_{n}',
            on_progress=lambda done, total: _update_batch_progress(batch_id, done, total),
            on_status=lambda idx, status: _update_batch_status(batch_id, idx, status),
            on_result=lambda r: _update_batch_result(batch_id, r)
        )
        _batch_complete(batch_id, results)
    
    thread = threading.Thread(target=run_batch, daemon=True)
    thread.start()
    
    self._json_response(200, {'ok': True, 'batch_id': batch_id, 'total': len(prompts)})
    return
```

### 2.2 Agregar en `_FlowBridgeHandler.do_GET`

```python
if self.path == '/api/accounts':
    accounts = []
    with _auto_tokens_lock:
        for ah, token in _auto_tokens.items():
            connected = False
            with _ws_clients_lock:
                connected = ah in _ws_clients
            accounts.append({
                'hash': ah,
                'email': _auto_emails.get(ah, ''),
                'connected': connected,
                'has_token': bool(token)
            })
    self._json_response(200, {'accounts': accounts})
    return

if self.path.startswith('/api/events'):
    # SSE endpoint — stream en vivo
    self.send_response(200)
    self.send_header('Content-Type', 'text/event-stream')
    self.send_header('Cache-Control', 'no-cache')
    self.send_header('Connection', 'keep-alive')
    _cors_headers(self)
    self.end_headers()
    
    batch_id = ''
    if '?' in self.path:
        from urllib.parse import parse_qs, urlparse
        batch_id = parse_qs(urlparse(self.path).query).get('batch', [''])[0]
    
    client_queue = _sse_register(batch_id)
    try:
        while True:
            try:
                event = client_queue.get(timeout=15)
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()
                if event.get('type') == 'complete':
                    break
            except queue.Empty:
                self.wfile.write(": keepalive\n\n".encode())
                self.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        _sse_unregister(batch_id, client_queue)
    return
```

### 2.3 Agregar sistema SSE (Server-Sent Events)

Fuera de la clase, junto con las otras globales:

```python
import queue

_sse_clients = {}       # batch_id -> list of queues
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

def _sse_broadcast(batch_id, event):
    with _sse_lock:
        for q in _sse_clients.get(batch_id, []):
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

# Estado del batch para el dashboard
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
    _sse_broadcast(batch_id, {'type': 'complete', 'results': results})
```

---

## Fase 3: Dashboard UI

**Objetivo**: Una sola página HTML servida por Python, con diseño limpio y moderna.

### 3.1 Crear `dashboard/index.html`

Single-page app con:
- Header con logo "FlowForge" y contador de cuentas conectadas
- Cards de cuentas (verde = conectada, gris = esperando)
- Área de batch input (textarea multilínea)
- Selector de directorio de salida (File System Access API)
- Botón "Generar"
- Barra de progreso con contador X/N
- Lista de resultados en tiempo real (íconos: ✅ completado, 🔄 generando, ⏳ en cola, ❌ error)

### 3.2 Crear `dashboard/app.js`

Lógica:
- Conexión SSE a `/api/events`
- Polling a `/api/accounts` cada 5s para actualizar cards
- POST a `/api/generate` con prompts + output_folder
- Renderizado dinámico de resultados

### 3.3 Servir el dashboard desde Python

Agregar en `_FlowBridgeHandler.do_GET`:

```python
if self.path == '/' or self.path == '/app':
    self.send_response(200)
    self.send_header('Content-Type', 'text/html; charset=utf-8')
    self.end_headers()
    import os as _os
    html_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'dashboard', 'index.html')
    with open(html_path, 'r', encoding='utf-8') as f:
        self.wfile.write(f.read().encode())
    return

if self.path.startswith('/dashboard/'):
    filename = self.path.split('/dashboard/')[1]
    ext = filename.rsplit('.', 1)[-1] if '.' in filename else ''
    mime = {'js': 'application/javascript', 'css': 'text/css', 'html': 'text/html'}.get(ext, 'text/plain')
    self.send_response(200)
    self.send_header('Content-Type', mime)
    self.end_headers()
    import os as _os
    filepath = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'dashboard', filename)
    with open(filepath, 'r', encoding='utf-8') as f:
        self.wfile.write(f.read().encode())
    return
```

---

## Fase 4: Manifest de la Extensión (Limpiar)

### 4.1 Modificar `extension/manifest.json`

Eliminar todos los content scripts que no sean de Flow (whisk, grok, gemini, qwen, dubvoice). Dejar solo:

```json
{
  "manifest_version": 3,
  "name": "FlowForge Bridge",
  "version": "1.0",
  "description": "Auto-auth y bridge para FlowForge",
  "permissions": ["activeTab", "storage"],
  "host_permissions": [
    "https://labs.google/*",
    "https://aisandbox-pa.googleapis.com/*",
    "http://127.0.0.1:*/*"
  ],
  "content_scripts": [
    {
      "matches": ["https://labs.google/fx/*/tools/flow*", "https://labs.google/fx/tools/flow*"],
      "js": ["flow_content.js"],
      "run_at": "document_idle"
    },
    {
      "matches": ["https://labs.google/fx/*/tools/flow*", "https://labs.google/fx/tools/flow*"],
      "js": ["flow_token_gen.js"],
      "run_at": "document_idle",
      "world": "MAIN"
    }
  ],
  "icons": {
    "16": "icon16.png",
    "48": "icon48.png",
    "128": "icon128.png"
  }
}
```

---

## Resumen de Archivos a Modificar / Crear

| Archivo | Acción | Cambios |
|---------|--------|---------|
| `extension/flow_token_gen.js` | MODIFICAR | +15 líneas: auto-auth POST |
| `extension/manifest.json` | MODIFICAR | Limpiar a solo Flow |
| `engine/flow_client.py` | MODIFICAR | +150 líneas: auto-auth, API endpoints, SSE, dashboard serving |
| `dashboard/index.html` | CREAR | ~400 líneas: UI completa |
| `dashboard/app.js` | CREAR | ~200 líneas: lógica de UI |

---

## Instrucciones para el Agente

1. **LEE primero** `engine/flow_client.py` completo (851 líneas). Entendé la estructura antes de tocar nada.
2. **LEE** `extension/flow_token_gen.js` y `extension/flow_content.js`.
3. **Implementá las fases en orden** (1 → 2 → 3 → 4). Cada fase es independiente y testeable.
4. **NO modifiques** la lógica de generación (`generate_image`, `batch_generate`, `_bridge_generate`).
5. **NO modifiques** `flow_content.js` (el bridge ya funciona).
6. **NO modifiques** el sistema de reCAPTCHA en `flow_token_gen.js`.
7. **El dashboard** debe ser un solo HTML auto-contenido (CSS inline o `<style>`, JS inline o `<script src="app.js">`).
8. **Diseño del dashboard**:
   - Tema oscuro (#0d0d1a fondo, #1a1a2e cards, #ff4444 accent)
   - Sin dependencias externas (no CDN, no frameworks)
   - Responsive (mínimo 800px, óptimo 1200px+)
   - Usar File System Access API para selector de directorio
   - Las cards de cuentas deben actualizarse cada 5s
   - El progreso se actualiza vía SSE en tiempo real
9. **Testeá que el archivo Python siga siendo sintácticamente válido** (`python -c "import ast; ast.parse(open('engine/flow_client.py').read())"`).

---

## Criterio de Aceptación

- [ ] Abrir Flow en Chrome → el dashboard muestra la cuenta como conectada automáticamente
- [ ] Pegar múltiples prompts → se encolan y generan en orden
- [ ] El progreso se actualiza en vivo (X/N completadas)
- [ ] Las imágenes se guardan en el directorio seleccionado como `escena_1.png`, `escena_2.png`, ...
- [ ] Funciona con 1 cuenta (mínimo)
- [ ] Funciona con 2+ cuentas en paralelo (si hay múltiples perfiles Chrome abiertos)
- [ ] No se requiere copiar ninguna cookie manualmente
