"""Test project API endpoints"""
import sys, os, time, requests, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
import flow_client as fc

fc.set_projects_base_dir('C:/Users/T-Gency/Documents/Youtube/canal')
fc._start_bridge_server()
time.sleep(0.5)

errors = 0
def check(desc, condition):
    global errors
    if condition: print(f'  [OK] {desc}')
    else: print(f'  [FAIL] {desc}'); errors += 1

BASE = 'http://127.0.0.1:5556'

print('GET /api/projects')
r = requests.get(f'{BASE}/api/projects', timeout=5)
check('status=200', r.status_code == 200)
projects = r.json().get('projects', [])
check('projects is list', isinstance(projects, list))
check('has projects', len(projects) > 0)
if projects:
    p = projects[0]
    check('has _name', '_name' in p)

print('POST /api/projects')
r = requests.post(f'{BASE}/api/projects', json={'name': '_test_proj', 'title': 'Test'}, timeout=5)
check('status=201', r.status_code == 201)
check('ok=True', r.json().get('ok') is True)

print('GET /api/projects/_test_proj')
r = requests.get(f'{BASE}/api/projects/_test_proj', timeout=5)
check('status=200', r.status_code == 200)
check('title matches', r.json().get('title') == 'Test')

print('GET /api/read-file')
r = requests.get(f'{BASE}/api/read-file?path=C:/Users/T-Gency/Documents/Youtube/canal/acuario-mayo-2026/prompts-2026-05-16.json', timeout=5)
check('status=200', r.status_code == 200)
check('has content', bool(r.json().get('content')))

print('GET /api/projects (verify new project)')
r = requests.get(f'{BASE}/api/projects', timeout=5)
names = [p.get('_name') for p in r.json().get('projects', [])]
check('has _test_proj', '_test_proj' in names)

print()
if errors == 0:
    print('All project API tests PASSED.')
else:
    print(f'{errors} test(s) FAILED.')
sys.exit(0 if errors == 0 else 1)
