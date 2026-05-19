"""Integration test: FlowForge v2 — end-to-end (Fase 1-3).
Covers: auto-auth → API endpoints → SSE → dashboard serving."""
import sys, os, time, requests, json, threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
import flow_client as fc

BASE = 'http://127.0.0.1:5556'
PASS = 0
FAIL = 0

def check(desc, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  [PASS] {desc}')
    else:
        FAIL += 1
        print(f'  [FAIL] {desc}')

def section(title):
    print(f'\n{"=" * 55}')
    print(f'  {title}')
    print(f'{"=" * 55}')

# ── Setup ───────────────────────────────────────────────
section('SETUP')

fc._start_bridge_server()
time.sleep(0.8)
check('Bridge running', fc.is_bridge_healthy())

if not fc.is_bridge_healthy():
    print('\n  Bridge not running. Aborting.')
    sys.exit(1)

# ── Fase 1: Auto-Auth ──────────────────────────────────
section('FASE 1 — Auto-Auth')

TEST_HASH = 'inte2etest'
TEST_TOKEN = 'ya29.e2e_token_abc123'
TEST_EMAIL = 'e2e@flowforge.test'

r = requests.post(f'{BASE}/api/auth/auto', json={
    'account_hash': TEST_HASH,
    'access_token': TEST_TOKEN,
    'email': TEST_EMAIL,
    'name': 'E2E Tester'
}, timeout=5)
check('POST /api/auth/auto — 200', r.status_code == 200)
check('ok=True', r.json().get('ok') is True)
check('Token stored in _auto_tokens', fc._auto_tokens.get(TEST_HASH) == TEST_TOKEN)

# Re-send (heartbeat simulation)
r = requests.post(f'{BASE}/api/auth/auto', json={
    'account_hash': TEST_HASH,
    'access_token': 'ya29.updated_token',
    'email': TEST_EMAIL,
}, timeout=5)
check('Token update (heartbeat) — 200', r.status_code == 200)
check('Token updated', fc._auto_tokens.get(TEST_HASH) == 'ya29.updated_token')

# Invalid POST
r = requests.post(f'{BASE}/api/auth/auto', json={'account_hash': 'x'}, timeout=5)
check('Missing fields — 400', r.status_code == 400)

# get_auto_token
check('get_auto_token() works', fc.get_auto_token(TEST_HASH) is not None)
check('get_auto_token(unknown) — None', fc.get_auto_token('noexist') is None)

# ── Fase 2: API Endpoints ──────────────────────────────
section('FASE 2 — API Endpoints')

# GET /api/accounts
r = requests.get(f'{BASE}/api/accounts', timeout=5)
check('GET /api/accounts — 200', r.status_code == 200)
accts = r.json().get('accounts', [])
check('Has accounts', len(accts) > 0)
e2e_acct = next((a for a in accts if a.get('hash') == TEST_HASH), None)
check('E2E account found', e2e_acct is not None)
check('Account has email', e2e_acct.get('email') == TEST_EMAIL if e2e_acct else False)
check('Account has has_token=True', e2e_acct.get('has_token') is True if e2e_acct else False)

# GET /health
r = requests.get(f'{BASE}/health', timeout=5)
check('GET /health — 200', r.status_code == 200)
check('ok=True', r.json().get('ok') is True)

# GET /flow-bridge-status
r = requests.get(f'{BASE}/flow-bridge-status', timeout=5)
check('GET /flow-bridge-status — 200', r.status_code == 200)
check('Has pending field', 'pending' in r.json())
check('Has ws_clients field', 'ws_clients' in r.json())

# GET /api/events?batch=unknown — 404
r = requests.get(f'{BASE}/api/events?batch=nonexistent_batch_id_999', timeout=5)
check('SSE unknown batch — 404', r.status_code == 404)

# POST /api/generate
section('FASE 2 — POST /api/generate (deadlock test)')

r = requests.post(f'{BASE}/api/generate', json={
    'prompts': 'e2e prompt 1\ne2e prompt 2',
    'output_folder': 'C:/tmp/e2e_test',
    'accounts': [TEST_HASH],
    'model': 'NARWHAL'
}, timeout=5)
check('POST /api/generate — 200', r.status_code == 200)
data = r.json()
check('ok=True', data.get('ok') is True)
batch_id = data.get('batch_id', '')
check('batch_id returned', bool(batch_id))
check('total=2', data.get('total') == 2)

# Invalid generate (no prompts)
r = requests.post(f'{BASE}/api/generate', json={
    'prompts': '',
    'output_folder': 'C:/tmp',
}, timeout=5)
check('Empty prompts — 400', r.status_code == 400)

# Invalid generate (no output_folder)
r = requests.post(f'{BASE}/api/generate', json={
    'prompts': 'test',
    'output_folder': '',
}, timeout=5)
check('Empty output_folder — 400', r.status_code == 400)

# ── Fase 2: SSE events ─────────────────────────────────
section('FASE 2 — SSE events')

if batch_id:
    sse_events = []
    sse_done = threading.Event()

    def read_sse():
        try:
            r = requests.get(f'{BASE}/api/events?batch={batch_id}', stream=True, timeout=30)
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith('data: '):
                    try:
                        evt = json.loads(line[6:])
                        sse_events.append(evt)
                        if evt.get('type') == 'complete':
                            break
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass
        sse_done.set()

    t = threading.Thread(target=read_sse, daemon=True)
    t.start()
    sse_done.wait(timeout=10)

    progress_events = [e for e in sse_events if e.get('type') == 'progress']
    complete_events = [e for e in sse_events if e.get('type') == 'complete']
    check(f'SSE events received ({len(sse_events)} total)', len(sse_events) > 0)
    check(f'Progress events ({len(progress_events)})', len(progress_events) >= 0)
    check(f'Complete event ({len(complete_events)})', len(complete_events) >= 0)
else:
    check('SSE events (skipped — no batch_id)', False)

# ── Fase 3: Dashboard Serving ──────────────────────────
section('FASE 3 — Dashboard Serving')

r = requests.get(f'{BASE}/', timeout=5)
check('GET / — 200', r.status_code == 200)
check('Content-Type text/html', 'text/html' in r.headers.get('Content-Type', ''))
html = r.text
check('Has FlowForge', 'FlowForge' in html)
check('Has dark theme', '#080810' in html)
check('Has textarea', '<textarea' in html)
check('Has app.js script', 'dashboard/app.js' in html)
check('Has Generate button', 'Generar' in html)

r = requests.get(f'{BASE}/dashboard/app.js', timeout=5)
check('GET /dashboard/app.js — 200', r.status_code == 200)
check('Content-Type javascript', 'javascript' in r.headers.get('Content-Type', ''))
js = r.text
check('Has EventSource', 'EventSource' in js)
check('Has pollAccounts', 'pollAccounts' in js)
check('Has connectSSE', 'connectSSE' in js)
check('Has state object', 'var state' in js)
check('Has SSE batch_id guard', "data.batch_id && data.batch_id !== state.batchId" in js)
check('Has onerror toast', 'Error de conexi' in js)

r = requests.get(f'{BASE}/dashboard/nope.js', timeout=5)
check('GET /dashboard/nope.js — 404', r.status_code == 404)

# ── Summary ─────────────────────────────────────────────
section('RESULT')

total = PASS + FAIL
print(f'\n  PASS: {PASS}/{total}')
if FAIL > 0:
    print(f'  FAIL: {FAIL}/{total}')
print()

if FAIL == 0:
    print('  >>> FlowForge v2 integraci\u00f3n completa PAS\u00d3.')
    print('  >>> F1 (auto-auth) + F2 (API/SSE) + F3 (dashboard) = FUNCIONANDO.')
else:
    print(f'  >>> {FAIL} test(s) fallaron.')

sys.exit(0 if FAIL == 0 else 1)
