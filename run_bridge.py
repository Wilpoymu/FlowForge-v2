"""Start bridge server and keep it alive for dashboard testing."""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'engine'))
import flow_client as fc

# Auto-detect projects directory
candidates = [
    os.path.expanduser('~/Documents/Youtube/canal'),
    os.path.expanduser('~/Documents/Youtube'),
    os.path.join(os.path.dirname(os.path.dirname(__file__)), 'canal'),
]
for cand in candidates:
    if os.path.isdir(cand):
        fc.set_projects_base_dir(cand)
        break
else:
    print('Warning: No se encontro directorio de proyectos. Configuralo con set_projects_base_dir().')
    print(f'  Buscado en: {candidates}')

fc._start_bridge_server()
print('Bridge running on http://127.0.0.1:5556', flush=True)
print('Press Ctrl+C to stop.', flush=True)

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print('\nBridge stopped.')