"""FlowForge v2 — public API. Backward-compatible with old flow_client module."""
from ._core import *

# Explicit re-export of underscore-prefixed names (not imported by *)
from ._core import (
    _debug_callback, _log_lock, _flow_account_hash, _log, _browser_headers,
    _TOKEN_MAX_AGE_S, _bridge_server_started, _ws_server_started,
    _bridge_bind_ok, _ws_bind_ok, _bridge_bind_error, _ws_bind_error,
    _generate_queue, _generate_queue_lock, _generate_results, _generate_results_lock,
    _generate_results_event, _throttle_until, _throttle_lock,
    _ws_clients, _ws_clients_lock, _http_seen, _http_seen_lock, _HTTP_SEEN_TTL,
    _close_all_recaptcha_sessions, _parse_cookie_string,
    save_image_from_url, save_base64_image
)

# Auth
from ._auth import *
from ._auth import _auto_tokens, _auto_tokens_lock, _auto_emails, _auto_names

# Projects
from ._projects import (
    _scan_project_folder, _list_projects, _save_project,
    _create_project, _get_project, _update_project, _batch_project,
    _get_project_references
)

# Generation
from ._generation import (
    FlowClientInstance, batch_generate
)

# Bridge (startup + health)
from ._bridge import (
    _bridge_bind_ok, _bridge_bind_error, _ws_bind_ok, _ws_bind_error,
    _start_bridge_server, _start_ws_server,
    is_bridge_healthy, get_bridge_status,
    repair_bridge, get_connected_accounts, clear_pending_state,
    _kill_processes_on_port,
    _sse_clients, _sse_lock, _sse_register, _sse_unregister, _sse_broadcast,
    _batch_state, _batch_state_lock,
    _update_batch_progress, _update_batch_status, _update_batch_result, _batch_complete,
    _BridgeHandler, _FlowBridgeHandler, _cors_headers,
    _ws_push_to_client, _ws_drain_queue_for
)
