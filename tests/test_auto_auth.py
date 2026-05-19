"""
Integration test para Fase 1: Auto-Auth.
Simula el POST de la extension sin necesidad de Chrome abierto.

Uso:
    python tests/test_auto_auth.py
"""

import sys
import os
import time
import json
import requests

# Agregar engine/ al path para importar flow_client
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))
import flow_client as fc

BRIDGE_URL = 'http://127.0.0.1:5556'

PASS = 0
FAIL = 0

def check(description, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  [PASS] {description}')
    else:
        FAIL += 1
        print(f'  [FAIL] {description}')

def section(title):
    print(f'\n{"="*60}')
    print(f'  {title}')
    print(f'{"="*60}')

# ── Setup ────────────────────────────────────────────────
section('SETUP: Iniciando bridge')

fc._start_bridge_server()
time.sleep(0.5)

check('Bridge healthy', fc.is_bridge_healthy())

if not fc.is_bridge_healthy():
    print('\n  ❌ Bridge no pudo bindear. ¿Hay otro proceso en el puerto 5556?')
    print(f'     Error: {fc._bridge_bind_error}')
    sys.exit(1)

# ── Test 1: POST válido ─────────────────────────────────
section('TEST 1: POST /api/auth/auto con datos válidos')

TEST_HASH = 'test123abc'
TEST_TOKEN = 'ya29.fake_access_token_for_testing'
TEST_EMAIL = 'test@example.com'
TEST_NAME = 'Test User'

resp = requests.post(
    f'{BRIDGE_URL}/api/auth/auto',
    json={
        'account_hash': TEST_HASH,
        'access_token': TEST_TOKEN,
        'email': TEST_EMAIL,
        'name': TEST_NAME,
    },
    timeout=5,
)

check('Status 200', resp.status_code == 200)
data = resp.json()
check('Respuesta ok=True', data.get('ok') is True)
check('Respuesta incluye account', data.get('account') == TEST_HASH)

# ── Test 2: Token guardado ───────────────────────────────
section('TEST 2: Verificar que el token se guardó')

check('_auto_tokens contiene el hash', TEST_HASH in fc._auto_tokens)
check('Token coincide', fc._auto_tokens.get(TEST_HASH) == TEST_TOKEN)
check('_auto_emails contiene email', fc._auto_emails.get(TEST_HASH) == TEST_EMAIL)
check('_auto_names contiene name', fc._auto_names.get(TEST_HASH) == TEST_NAME)

stored_token = fc.get_auto_token(TEST_HASH)
check('get_auto_token() devuelve token', stored_token == TEST_TOKEN)

stored_email = fc.get_auto_email(TEST_HASH)
check('get_auto_email() devuelve email', stored_email == TEST_EMAIL)

# ── Test 3: hash no existente ────────────────────────────
section('TEST 3: get_auto_token con hash que no existe')

check('Hash inexistente -> None', fc.get_auto_token('no_existe_hash') is None)
check('Email inexistente -> ""', fc.get_auto_email('no_existe_hash') == '')

# ── Test 4: POST inválido (campos faltantes) ─────────────
section('TEST 4: POST inválido — faltan campos')

resp = requests.post(
    f'{BRIDGE_URL}/api/auth/auto',
    json={'account_hash': 'algun_hash'},
    timeout=5,
)

check('Status 400', resp.status_code == 400)
check('Respuesta ok=False', resp.json().get('ok') is False)
check('Mensaje de error', 'missing' in resp.json().get('error', '').lower())

# ── Test 5: POST sin body ────────────────────────────────
section('TEST 5: POST sin body')

resp = requests.post(
    f'{BRIDGE_URL}/api/auth/auto',
    data='',
    timeout=5,
)

check('Status 400 (sin body)', resp.status_code == 400)

# ── Test 6: FlowClientInstance con auto-auth ─────────────
section('TEST 6: FlowClientInstance con account_hash (sin cookie)')

cli = fc.FlowClientInstance(
    cookie_string='',
    label='test-client',
    account_hash=TEST_HASH,
)

check('account_hash seteado', cli.account_hash == TEST_HASH)
check('_auth_token seteado desde auto-auth', cli._auth_token == TEST_TOKEN)
check('user_email seteado', cli.user_email == TEST_EMAIL)
check('cookie_string vacio', cli.cookie_string == '')

# ── Test 7: FlowClientInstance sin token (hash no registrado) ──
section('TEST 7: FlowClientInstance con hash no registrado')

cli_noauth = fc.FlowClientInstance(
    cookie_string='',
    label='no-auth-client',
    account_hash='hash_no_registrado',
)

check('account_hash seteado igual', cli_noauth.account_hash == 'hash_no_registrado')
check('_auth_token es None (no hay token)', cli_noauth._auth_token is None)
check('user_email es None', cli_noauth.user_email is None)

# ── Test 8: Cookie path sigue funcionando ────────────────
section('TEST 8: Backward compat — cookie_string vacio con hash registrado')

cli_cookie = fc.FlowClientInstance(
    cookie_string='fake_cookie_for_test',
    label='cookie-client',
)

check('account_hash es None (sin hash explicito)', cli_cookie.account_hash is None)
check('cookie_string preservado', cli_cookie.cookie_string == 'fake_cookie_for_test')
check('_auth_token es None (no inyecta auto-auth)', cli_cookie._auth_token is None)
check('user_email es None', cli_cookie.user_email is None)

# ── Test 9: Re-sobrescritura de token ────────────────────
section('TEST 9: Actualizar token existente (re-send)')

NEW_TOKEN = 'ya29.updated_token_v2'
resp = requests.post(
    f'{BRIDGE_URL}/api/auth/auto',
    json={
        'account_hash': TEST_HASH,
        'access_token': NEW_TOKEN,
        'email': TEST_EMAIL,
    },
    timeout=5,
)

check('Status 200 (update)', resp.status_code == 200)
check('Token actualizado', fc._auto_tokens.get(TEST_HASH) == NEW_TOKEN)

# Restaurar token original para pruebas siguientes
fc._auto_tokens[TEST_HASH] = TEST_TOKEN

# ── Resultado ────────────────────────────────────────────
section('RESULTADO')

total = PASS + FAIL
print(f'\n  PASS: {PASS}/{total}')
if FAIL > 0:
    print(f'  FAIL: {FAIL}/{total}')
print()

if FAIL == 0:
    print('  >>> Todos los tests de auto-auth pasaron. La Fase 1 funciona.')
else:
    print(f'  >>> Hay {FAIL} test(s) que fallaron. Revisar arriba.')

sys.exit(0 if FAIL == 0 else 1)
