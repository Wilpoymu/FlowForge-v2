"""Smoke test for Fase 2 API endpoints — deadlock fix verification."""
import sys, os, time, requests, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
import flow_client as fc

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

# Setup
print('Starting bridge...')
fc._start_bridge_server()
time.sleep(0.5)

# 1. Register auto-auth token
print('\n[1] Auto-auth registration')
r = requests.post('http://127.0.0.1:5556/api/auth/auto', json={
    'account_hash': 'smoketest123',
    'access_token': 'ya29.smoke_test_token',
    'email': 'smoke@test.com'
}, timeout=5)
check('POST /api/auth/auto -> 200', r.status_code == 200)
check('Response ok=True', r.json().get('ok') is True)

# 2. Generate batch (THE DEADLOCK PATH)
print('\n[2] POST /api/generate (deadlock test)')
r = requests.post('http://127.0.0.1:5556/api/generate', json={
    'prompts': 'prompt one\nprompt two\nprompt three',
    'output_folder': 'C:/tmp/smoke_test',
    'accounts': ['smoketest123'],
    'model': 'NARWHAL'
}, timeout=5)
check('POST /api/generate -> 200', r.status_code == 200)
data = r.json()
check('Response ok=True', data.get('ok') is True)
check('batch_id present', bool(data.get('batch_id')))
check('total = 3', data.get('total') == 3)

# 3. Accounts endpoint
print('\n[3] GET /api/accounts')
r = requests.get('http://127.0.0.1:5556/api/accounts', timeout=5)
check('GET /api/accounts -> 200', r.status_code == 200)
accounts = r.json().get('accounts', [])
check('Accounts list returned', len(accounts) > 0)
check('Account has hash field', 'hash' in accounts[0])
check('Account has connected field', 'connected' in accounts[0])

# 4. Unknown batch -> 404
print('\n[4] GET /api/events?batch=unknown (expect 404)')
r = requests.get('http://127.0.0.1:5556/api/events?batch=nonexistent12345', timeout=5)
check('Unknown batch -> 404', r.status_code == 404)

# 5. Health still works
print('\n[5] GET /health (regression)')
r = requests.get('http://127.0.0.1:5556/health', timeout=5)
check('GET /health -> 200', r.status_code == 200)
check('ok=True', r.json().get('ok') is True)

# 6. Bridge status still works
print('\n[6] GET /flow-bridge-status (regression)')
r = requests.get('http://127.0.0.1:5556/flow-bridge-status', timeout=5)
check('GET /flow-bridge-status -> 200', r.status_code == 200)

# 7. Dashboard HTML stub
print('\n[7] GET / (dashboard stub)')
r = requests.get('http://127.0.0.1:5556/', timeout=5)
check('GET / -> 200 (or FileNotFound handled)', r.status_code in (200, 404))

# Summary
total = PASS + FAIL
print(f'\n{"="*50}')
print(f'  PASS: {PASS}/{total}')
if FAIL > 0:
    print(f'  FAIL: {FAIL}/{total}')
if FAIL == 0:
    print(f'  >>> All smoke tests passed. Deadlock FIXED.')
else:
    print(f'  >>> {FAIL} test(s) failed.')
print(f'{"="*50}')

sys.exit(0 if FAIL == 0 else 1)
