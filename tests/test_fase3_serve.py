"""Quick test: verify dashboard HTML and JS served correctly."""
import sys, os, time, requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
import flow_client as fc

fc._start_bridge_server()
time.sleep(0.5)

errors = 0

def check(desc, condition):
    global errors
    if condition:
        print(f'  [OK] {desc}')
    else:
        print(f'  [FAIL] {desc}')
        errors += 1

# Dashboard HTML
print('GET /')
r = requests.get('http://127.0.0.1:5556/', timeout=5)
check(f'status=200', r.status_code == 200)
check(f'content-type html', 'text/html' in r.headers.get('content-type', ''))
check(f'FlowForge in body', 'FlowForge' in r.text)
check(f'dark bg theme', '#080810' in r.text)
check(f'app.js referenced', 'app.js' in r.text)
check(f'textarea exists', 'textarea' in r.text)

# App.js
print('GET /dashboard/app.js')
r = requests.get('http://127.0.0.1:5556/dashboard/app.js', timeout=5)
check(f'status=200', r.status_code == 200)
check(f'content-type js', 'javascript' in r.headers.get('content-type', ''))
check(f'EventSource api', 'EventSource' in r.text)
check(f'pollAccounts func', 'pollAccounts' in r.text)
check(f'startBatch func', 'startBatch' in r.text)
check(f'connectSSE func', 'connectSSE' in r.text)

# 404 for nonexistent static file
print('GET /dashboard/nope.js')
r = requests.get('http://127.0.0.1:5556/dashboard/nope.js', timeout=5)
check(f'status=404 for missing file', r.status_code == 404)

print()
if errors == 0:
    print('All dashboard serving tests PASSED.')
else:
    print(f'{errors} test(s) FAILED.')
sys.exit(0 if errors == 0 else 1)
