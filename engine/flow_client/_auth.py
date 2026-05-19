"""Auto-auth: browser token registration and retrieval."""
import threading
from ._core import _log

# Auto-auth tokens (poblados por la extensión Chrome)
_auto_tokens = {}          # account_hash -> access_token
_auto_tokens_lock = threading.Lock()
_auto_emails = {}          # account_hash -> email
_auto_names = {}           # account_hash -> name

def _register_auto_auth(account_hash, access_token, email, name=''):
    with _auto_tokens_lock:
        _auto_tokens[account_hash] = access_token
        _auto_emails[account_hash] = email
        if name:
            _auto_names[account_hash] = name
    _log(f'Auto-auth registrado: {account_hash} ({email})')

def get_auto_token(account_hash):
    with _auto_tokens_lock:
        return _auto_tokens.get(account_hash)

def get_auto_email(account_hash):
    with _auto_tokens_lock:
        return _auto_emails.get(account_hash, '')

