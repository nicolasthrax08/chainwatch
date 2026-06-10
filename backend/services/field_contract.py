#!/usr/bin/env python3
"""
ChainWatch Frontend-Backend Field Contract Validator
=====================================================
Cross-references frontend field accesses against backend endpoint responses
and WebSocket push message shapes to find mismatches where the frontend
expects fields the backend doesn't return/send.

Usage:
    python field_contract.py [--path PATH] [--output FORMAT] [--include-ws]

Exit codes:
    0 = all contracts valid
    1 = field mismatches found
"""
import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Tuple


@dataclass
class FieldAccess:
    """A field access found in frontend code."""
    file: str
    line: int
    object_name: str      # e.g., "wallet", "signal", "alert"
    field_name: str       # e.g., "balance_hkd", "confidence_score"
    access_path: str      # full access path, e.g., "wallet.balance_hkd"


@dataclass
class EndpointResponse:
    """Fields returned by a backend endpoint."""
    name: str
    method: str
    path: str
    fields: Set[str]       # top-level fields in response objects
    nested: Dict[str, Set[str]] = field(default_factory=dict)  # nested object fields


@dataclass
class ContractViolation:
    """A field the frontend expects but the backend doesn't return."""
    endpoint: str
    object_name: str
    field_name: str
    frontend_file: str
    frontend_line: int
    suggestion: str


# ─── Backend endpoint response field registry ──────────────────────────
# This is the single source of truth for what each endpoint returns.
# Update this when adding new fields to endpoints.

ENDPOINT_RESPONSES: List[EndpointResponse] = [
    # GET /api/dashboard — returns wallet_meta objects
    EndpointResponse(
        name="dashboard",
        method="GET",
        path="/api/dashboard",
        fields={"portfolio", "wallets", "personal_wallets", "whale_wallets_list",
                "transactions", "recent_transactions"},
        nested={
            "wallet_meta": {
                "id", "address", "chain", "label", "is_whale", "is_mine",
                "is_fresh_wallet", "risk_label",
                "balance_usd", "balance_hkd", "balance_btc", "balance_native",
                "last_balance_update", "created_at", "whale_score",
            },
            "portfolio": {
                "total_value_usd", "total_value_hkd", "total_value_btc",
                "wallets_tracked", "whale_wallets_tracked", "fresh_wallets",
            },
            "transaction": {
                "id", "tx_hash", "type", "amount", "token", "usd_value",
                "timestamp", "chain", "wallet_label", "wallet_address", "status",
            },
            "signal": {
                "id", "token_symbol", "action", "amount_usd",
                "confidence_score", "confidence_final",
                "whale_score", "wallet_address", "wallet_label",
                "status", "created_at",
                "explanation", "explanation_stale",
                "score_at_generation",
            },
        },
    ),
    # GET /api/wallets — returns wallet objects
    # Fields match main.py list_wallets() response dict (lines ~847-864)
    # Note: is_fresh_wallet, risk_label, balance_native are only in the
    # dashboard wallet_meta shape, not in the list_wallets response.
    EndpointResponse(
        name="wallets",
        method="GET",
        path="/api/wallets",
        fields={"wallets", "suggestions"},
        nested={
            "wallet": {
                "id", "address", "chain", "label", "is_whale", "is_mine",
                "balance_usd", "balance_hkd", "balance_btc",
                "whale_score",
                "last_balance_update", "created_at",
            },
        },
    ),
    # GET /api/signals — returns copy trade signal objects
    # Shape matches backend/main.py get_signals() response dict (lines ~1350-1372)
    EndpointResponse(
        name="signals",
        method="GET",
        path="/api/signals",
        fields={"signals"},
        nested={
            "signal": {
                "id", "token_symbol", "action", "amount_usd",
                "confidence_score", "confidence_final",
                "whale_score", "wallet_address", "wallet_label",
                "status", "created_at",
                "explanation", "explanation_stale",
                "score_at_generation",
            },
        },
    ),
    # GET /api/alerts — returns alert rule objects
    # Fields match main.py list_alerts() response dict (lines ~1224-1234)
    EndpointResponse(
        name="alerts",
        method="GET",
        path="/api/alerts",
        fields={"alerts"},
        nested={
            "alert": {
                "id", "rule_type", "threshold",
                "enabled", "created_at", "last_fired",
                "notify_telegram",
            },
        },
    ),
    # GET /api/alerts/history — returns fired alert objects
    # Fields match main.py get_alert_history() response dict (lines ~1253-1264)
    EndpointResponse(
        name="alert_history",
        method="GET",
        path="/api/alerts/history",
        fields={"history"},
        nested={
            "fired_alert": {
                "id", "alert_id", "rule_type",
                "trigger_value", "details", "message", "created_at",
            },
        },
    ),
    # GET /api/activity — returns transaction objects
    EndpointResponse(
        name="activity",
        method="GET",
        path="/api/activity",
        fields={"transactions", "total", "total_pages", "page", "per_page"},
        nested={
            "transaction": {
                "id", "tx_hash", "type", "amount", "token",
                "usd_value", "timestamp", "chain",
                "wallet_label", "wallet_address", "status",
            },
        },
    ),
    # GET /api/whale-suggestions — returns whale suggestion objects
    # Fields match main.py get_whale_suggestions() response (lines ~1103-1113)
    EndpointResponse(
        name="whale_suggestions",
        method="GET",
        path="/api/whale-suggestions",
        fields={"suggestions"},
        nested={
            "suggestion": {
                "id", "chain", "address", "label", "source",
            }
        },
    ),
    # GET /api/wallets/{wallet_id}/score — returns whale scorer diagnostic
    EndpointResponse(
        name="wallet_score",
        method="GET",
        path="/api/wallets/{wallet_id}/score",
        fields={
            "wallet_id", "address", "chain", "label", "is_whale",
            "balance_usd", "balance_native",
            "score", "score_activity", "score_reliability", "score_weight",
            "score_recency", "score_diversity", "score_signals_used",
            "score_is_coldstart", "median_amount_30d", "execution_rate_30d",
            "db_stored_score", "db_score_calculated_at",
        },
    ),
    # GET /api/signals/stats — returns aggregate signal performance statistics
    # Shape matches backend/main.py get_signal_stats() response (lines ~1594-1615)
    EndpointResponse(
        name="signal_stats",
        method="GET",
        path="/api/signals/stats",
        fields={"signals"},  # not used — flat response
        nested={},  # flat response, no nested objects
    ),
    # GET /api/signals/history — returns closed signal objects with outcome details
    # Shape matches backend/main.py get_signal_history() response (lines ~1663-1690)
    EndpointResponse(
        name="signal_history",
        method="GET",
        path="/api/signals/history",
        fields={"signals", "count"},
        nested={
            "signal": {
                "id", "token_symbol", "action", "amount_usd",
                "confidence_score", "confidence_final",
                "whale_score", "score_at_generation",
                "wallet_address", "wallet_label",
                "status", "explanation", "explanation_stale",
                "created_at", "executed_at", "closed_at",
                "time_to_close_seconds",
            },
        },
    ),
    # GET /api/whale-sentiment — returns whale sentiment aggregation
    EndpointResponse(
        name="whale_sentiment",
        method="GET",
        path="/api/whale-sentiment",
        fields={
            "sentiment_score", "classification",
            "inflow_usd", "outflow_usd", "tx_count",
        },
    ),
]

# ─── WebSocket message shape registry ──────────────────────────────────
# These are the shapes pushed by the backend via websocket_manager.send_to_user()
# (from monitor.py Phase 6a/6b). Frontend consumes these in App.jsx ws.onmessage.
# Format: WS_MESSAGE_SHAPES[msg_type][msg_action] = {field_names}

WS_MESSAGE_SHAPES: Dict[str, Dict[str, Set[str]]] = {
    # WS type "signal" — push when a new copy trade signal is generated
    # (monitor.py _poll_all_wallets_inner, Phase 6b)
    # Action: "created"
    "signal": {
        "created": {
            "id", "wallet_id", "wallet_address", "wallet_label", "chain",
            "token_symbol", "action", "amount_usd",
            "confidence_score", "confidence_final",
            "whale_score", "score_at_generation",
            "status", "created_at",
            "explanation", "explanation_stale",
        },
    },
    # WS type "alert" — push when an alert rule fires
    # (monitor.py _poll_all_wallets_inner, Phase 6a)
    # Action: "fired"
    "alert": {
        "fired": {
            "alert_id", "rule_type", "threshold",
            "trigger_value", "message",
        },
    },
}

# ─── Mapping from frontend object names to endpoint response keys ──────
# When frontend code accesses `wallet.balance_hkd`, the "wallet" object
# name maps to the "wallet_meta" or "wallet" nested key in the endpoint.
#
# NOTE on ambiguous names:
#   "s" can be either a signal (CopyTrades.jsx) or a suggestion (Wallets.jsx).
#   We map it to BOTH so the validator checks all possible shapes.
OBJECT_NAME_MAP: Dict[str, List[str]] = {
    "wallet": ["wallet_meta", "wallet"],
    "w": ["wallet_meta", "wallet"],
    "signal": ["signal"],
    # "s" is context-dependent: signal in CopyTrades, suggestion in Wallets
    "s": ["signal", "suggestion"],
    "alert": ["alert"],
    "a": ["alert"],
    "history": ["fired_alert"],
    "h": ["fired_alert"],
    "transaction": ["transaction"],
    "t": ["transaction"],
    "suggestion": ["suggestion"],
    # "sentiment" is returned by /api/whale-sentiment as a flat object
    # (not wrapped in a list key), so top-level fields are checked directly.
    # We use the special key "_top_level" to indicate the endpoint's own
    # top-level fields should be checked.
    "sentiment": ["_top_level"],
}


def find_frontend_field_accesses(base_path: str) -> List[FieldAccess]:
    """
    Scan frontend .jsx/.js files for field accesses like `obj.field`.
    Returns a list of FieldAccess objects.

    Context-aware filtering:
    - Tracks WS msg.type === 'signal' / 'alert' blocks. Variable names assigned
      from msg.payload inside those blocks are checked against WS_MESSAGE_SHAPES
      and excluded from REST validation (prevents false positives like a.alert_id
      inside a WS alert handler being flagged as a REST /api/alerts violation).
    - Tracks .map() iterations over known local arrays (e.g., TX_TYPES.map(t => ...)).
      Field accesses on the iteration variable are skipped (prevents false positives
      like t.value / t.label from local constant arrays).
    """
    accesses: List[FieldAccess] = []
    patterns = [
        # Match: obj.field (property access)
        re.compile(r'\b(\w+)\.(\w+)\b'),
    ]

    # Regex to detect WS msg.type blocks
    _ws_type_block_re = re.compile(
        r"if\s*\(\s*msg\.type\s*===\s*'(\w+)'\s*\)"
    )
    # Regex to detect payload assignment: const a = msg.payload;
    _ws_payload_assign_re = re.compile(
        r'\b(?:const|let|var)\s+(\w+)\s*=\s*msg\.payload\b'
    )
    # Regex to detect .map() over known local arrays: TX_TYPES.map(t => ...)
    _local_array_map_re = re.compile(
        r'\b(\w+)\.map\(\s*(\w+)\s*=>'
    )
    # Known local array constants that produce {value, label} objects
    _known_local_arrays: Set[str] = {"TX_TYPES", "PRESET_ALERTS", "STATUS_COLORS", "chainColors"}

    # Regex to detect inline array literals with .map(): [{...}, ...].map(s => ...)
    # This catches patterns like `subscores.map(s => ...)` where subscores is
    # defined as a local const with an inline array literal.
    _inline_array_const_re = re.compile(
        r'\b(?:const|let|var)\s+(\w+)\s*=\s*\['
    )

    for root, dirs, files in os.walk(base_path):
        dirs[:] = [d for d in dirs if d not in {
            "node_modules", ".git", "__pycache__", ".venv", "venv",
            "build", "dist",
        }]
        for fname in files:
            if not (fname.endswith(".jsx") or fname.endswith(".js")):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except (UnicodeDecodeError, OSError):
                continue

            # ── Per-file context tracking ──────────────────────────────────
            # Track WS msg.type block depth and payload variable names
            in_ws_block: bool = False
            ws_block_depth: int = 0
            ws_payload_vars: Set[str] = set()  # e.g., {"a"} when `const a = msg.payload` inside WS block
            ws_type: str = ""

            # Track .map() iteration variable names from known local arrays
            # and inline array constants (e.g., `const subscores = [...]`).
            map_iter_vars: Set[str] = set()  # e.g., {"t"} when `TX_TYPES.map(t => ...)`
            inline_array_vars: Set[str] = set()  # e.g., {"subscores"} when `const subscores = [...]`

            # Brace depth tracking for WS block scope
            brace_depth: int = 0
            track_braces: bool = False

            for line_num, line in enumerate(lines, 1):
                # Skip comments
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("*"):
                    continue

                # ── Detect WS msg.type block entry ─────────────────────────
                ws_match = _ws_type_block_re.search(line)
                if ws_match:
                    in_ws_block = True
                    ws_type = ws_match.group(1)
                    ws_payload_vars = set()
                    track_braces = True
                    # Count braces, but only from the if-statement onward.
                    # The opening '{' on this line starts the new block, so
                    # initial depth is 1 (we're inside the new block).
                    # We count all braces on the line: the '{' that opens this
                    # block minus any '}' that closes previous blocks.
                    brace_depth = line.count('{') - line.count('}')
                    # If net braces <= 0, the '{' was balanced by a '}' on the
                    # same line (e.g., `} else if (...) { ... }`), meaning a
                    # single-line block. For `} else if (...) {`, the '}' closes
                    # the prior block and '{' opens the new one, so net is 0 but
                    # we ARE inside the new block. Start with depth=1.
                    if brace_depth <= 0:
                        brace_depth = 1
                    continue

                # ── Track brace depth to know when WS block ends ───────────
                if in_ws_block and track_braces:
                    brace_depth += line.count('{') - line.count('}')
                    if brace_depth <= 0:
                        in_ws_block = False
                        track_braces = False
                        ws_payload_vars = set()

                # ── Detect payload assignment inside WS block ──────────────
                if in_ws_block:
                    pa_match = _ws_payload_assign_re.search(line)
                    if pa_match:
                        ws_payload_vars.add(pa_match.group(1))

                # ── Detect .map() over known local arrays ──────────────────
                map_match = _local_array_map_re.search(line)
                if map_match:
                    array_name = map_match.group(1)
                    iter_var = map_match.group(2)
                    if array_name in _known_local_arrays:
                        map_iter_vars.add(iter_var)
                    # Also catch inline array constants: `subscores.map(s => ...)`
                    # where subscores was previously detected as an inline array var
                    elif array_name in inline_array_vars:
                        map_iter_vars.add(iter_var)

                # ── Detect inline array literal constants ──────────────────
                # e.g., `const subscores = [` or `let subscores = [`
                inline_match = _inline_array_const_re.search(line)
                if inline_match:
                    inline_array_vars.add(inline_match.group(1))

                # ── Process field accesses ─────────────────────────────────
                for pat in patterns:
                    for m in pat.finditer(line):
                        obj_name = m.group(1)
                        field_name = m.group(2)

                        # Skip if this is a WS payload variable inside a WS block
                        if obj_name in ws_payload_vars and in_ws_block:
                            # Check if the field is valid in WS_MESSAGE_SHAPES
                            type_shapes = WS_MESSAGE_SHAPES.get(ws_type, {})
                            ws_fields: Set[str] = set()
                            for action_fields in type_shapes.values():
                                ws_fields.update(action_fields)
                            if field_name in ws_fields:
                                continue  # Valid WS field — not a REST violation
                            # If not in WS shapes either, skip it anyway (it's a WS access, not REST)
                            continue

                        # Skip if this is a .map() iteration variable from a known local array
                        if obj_name in map_iter_vars:
                            continue

                        # Skip common non-data objects
                        if obj_name in {
                            "console", "window", "document", "Math",
                            "JSON", "Object", "Array", "String", "Number",
                            "Date", "Promise", "Error", "RegExp",
                            "parseInt", "parseFloat", "isNaN",
                            "setState", "useState", "useEffect",
                            "useCallback", "useRef", "props", "state",
                            "e", "event", "res", "req", "err",
                            "params", "headers", "options", "config",
                            "style", "className", "onClick", "onChange",
                            "value", "key", "id", "type", "label",
                            "name", "ref", "children", "prev", "next",
                            "current", "target", "data", "result",
                            "response", "status", "message", "code",
                            "length", "toString", "toFixed", "toLocaleString",
                            "getTime", "getFullYear", "getMonth", "getDate",
                            "map", "filter", "reduce", "find", "forEach",
                            "push", "pop", "shift", "unshift", "splice",
                            "slice", "concat", "join", "split", "trim",
                            "toLowerCase", "toUpperCase", "replace",
                            "includes", "indexOf", "substring", "slice",
                            "setForm", "setLoading", "setError",
                            "setTransactions", "setAlerts", "setSignals",
                            "setHistory", "setShowAdd", "setShowSuggestions",
                            "setPage", "setTotal", "setTotalPages",
                            "setChainFilter", "setTxTypeFilter",
                            "setAnalyzeSignal", "setMirroring",
                            "setTogglingIds", "setHistoryError",
                            "setHistoryLoading", "apiFetch", "load",
                            "loadHistory", "handleAdd", "handleDelete",
                            "handleToggle", "handleRefresh", "handleSubmit",
                            "handleAddPreset", "handleMirror", "addSuggestion",
                            "timeAgo", "truncateAddress", "fmtTotal",
                            "fmtBalance", "API_BASE", "STATUS_COLORS",
                            "TX_TYPES", "PRESET_ALERTS", "chainColors",
                        }:
                            continue

                        # Skip field names that are JS builtins or React patterns
                        if field_name in {
                            "map", "filter", "reduce", "find", "forEach",
                            "push", "pop", "length", "constructor",
                            "prototype", "then", "catch", "finally",
                            "keys", "values", "entries", "assign",
                            "freeze", "create", "defineProperty",
                            "toString", "valueOf", "hasOwnProperty",
                            "setState", "forceUpdate", "render",
                            "componentDidMount", "componentDidUpdate",
                            "componentWillUnmount", "shouldComponentUpdate",
                            "defaultProps", "propTypes", "displayName",
                            "context", "refs", "updater", "isReactComponent",
                        }:
                            continue

                        accesses.append(FieldAccess(
                            file=fpath,
                            line=line_num,
                            object_name=obj_name,
                            field_name=field_name,
                            access_path=f"{obj_name}.{field_name}",
                        ))

    return accesses


def validate_contracts(
    accesses: List[FieldAccess],
) -> Tuple[List[ContractViolation], List[FieldAccess]]:
    """
    Cross-reference frontend field accesses against backend endpoint responses.
    Returns (violations, unmatched) where unmatched are accesses that couldn't
    be mapped to any endpoint (not necessarily bugs — could be local variables).
    """
    violations: List[ContractViolation] = []
    unmatched: List[FieldAccess] = []

    for fa in accesses:
        # Map frontend object name to possible endpoint response keys
        response_keys = OBJECT_NAME_MAP.get(fa.object_name)
        if not response_keys:
            # Could be a local variable — not necessarily a violation
            unmatched.append(fa)
            continue

        # Check if any endpoint returns this field for this object
        found = False
        for ep in ENDPOINT_RESPONSES:
            for rk in response_keys:
                # Special key: _top_level means check the endpoint's own top-level fields
                if rk == "_top_level":
                    if fa.field_name in ep.fields:
                        found = True
                        break
                if rk in ep.nested and fa.field_name in ep.nested[rk]:
                    found = True
                    break
                # Also check top-level fields
                if fa.field_name in ep.fields:
                    found = True
                    break
            if found:
                break

        if not found:
            # Check if it's a known field that SHOULD be added to the endpoint
            suggestion = _suggest_fix(fa)
            violations.append(ContractViolation(
                endpoint=_find_relevant_endpoint(fa),
                object_name=fa.object_name,
                field_name=fa.field_name,
                frontend_file=fa.file,
                frontend_line=fa.line,
                suggestion=suggestion,
            ))

    return violations, unmatched


def _find_relevant_endpoint(fa: FieldAccess) -> str:
    """Find the most likely endpoint for a field access."""
    response_keys = OBJECT_NAME_MAP.get(fa.object_name, [])
    for ep in ENDPOINT_RESPONSES:
        for rk in response_keys:
            if rk in ep.nested:
                return f"{ep.method} {ep.path}"
    return "unknown"


def _suggest_fix(fa: FieldAccess) -> str:
    """Generate a suggestion for a field contract violation."""
    # Known fields that exist in the DB but might be missing from the response
    known_db_fields = {
        "balance_native": "Add 'balance_native' to the wallet response dict in the endpoint",
        "last_fired": "Use 'last_fired_at' (check alerts table schema) or add 'last_fired' alias",
        "last_fired_at": "Verify this field is returned by the alerts/history endpoint",
        "trigger_value": "Verify this field is returned by the alerts/history endpoint",
        "whale_score": "Add 'whale_score' to the signal response (from wallets table or signal_generator)",
        "confidence_final": "Add 'confidence_final' to the signal response (computed in signal_generator)",
        "explanation_stale": "Add 'explanation_stale' to the signal response",
        "wallet_address": "Add 'wallet_address' to the signal response (from wallets table JOIN)",
        "wallet_label": "Add 'wallet_label' to the signal response (from wallets table JOIN)",
    }

    if fa.field_name in known_db_fields:
        return known_db_fields[fa.field_name]

    return (
        f"Field '{fa.field_name}' is not in the known response shape for "
        f"object '{fa.object_name}'. Either add it to the backend response "
        f"or remove the frontend access if it's not needed."
    )


# ─── WebSocket payload field access scanning ───────────────────────────
# Scans frontend code for field access patterns inside WebSocket onmessage
# handlers, e.g. `s.token_symbol` where `s = msg.payload` after
# `if (msg.type === 'signal')`.

# Regex to find WS onmessage handler blocks and extract payload variable names.
# Matches patterns like: `const s = msg.payload;` or `const { s } = msg.payload;`
_WS_PAYLOAD_PATTERNS = [
    # Direct payload destructuring: const { field } = msg.payload;
    re.compile(r'\b(?:const|let|var)\s*\{\s*([^}]+)\}\s*=\s*msg\.payload\b'),
    # Payload assignment: const s = msg.payload; -> look for s.field later
    re.compile(r'\b(?:const|let|var)\s+(\w+)\s*=\s*msg\.payload\b'),
    # Direct payload field access: msg.payload.field_name
    re.compile(r'\bmsg\.payload\.(\w+)\b'),
]

# Known WS payload object names mapped to (ws_type, ws_action) tuples.
# "s" inside a msg.type==='signal' block maps to signal/created, etc.
_WS_TYPE_BLOCKS = re.compile(
    r"if\s*\(\s*msg\.type\s*===\s*'(\w+)'\s*\)"
)


@dataclass
class WSFieldAccess:
    """A field accessed from a WebSocket message payload."""
    file: str
    line: int
    ws_type: str          # e.g., "signal", "alert"
    ws_action: str        # e.g., "created", "fired"
    field_name: str       # e.g., "token_symbol"
    access_path: str      # e.g., "s.token_symbol"


def find_ws_field_accesses(base_path: str) -> List[WSFieldAccess]:
    """
    Scan frontend .jsx/.js files for WebSocket payload field accesses.

    Looks for `msg.type === 'signal'` / `msg.type === 'alert'` blocks in
    onmessage handlers, then tracks payload variable names and their field
    accesses within that block.

    Also covers direct `msg.payload.field` accesses.
    """
    accesses: List[WSFieldAccess] = []

    for root, dirs, files in os.walk(base_path):
        dirs[:] = [d for d in dirs if d not in {
            "node_modules", ".git", "__pycache__", ".venv", "venv",
            "build", "dist",
        }]
        for fname in files:
            if not (fname.endswith(".jsx") or fname.endswith(".js")):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                    lines = content.splitlines()
            except (UnicodeDecodeError, OSError):
                continue

            # Track state: which WS type block we're inside
            current_ws_type: str = ""
            # payload_var_name -> set of fields accessed on it
            payload_vars: Dict[str, str] = {}  # var_name -> ws_type

            for line_num, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("*"):
                    continue

                # Detect entering a WS type block
                m = _WS_TYPE_BLOCKS.search(line)
                if m:
                    current_ws_type = m.group(1)
                    payload_vars = {}  # Reset for new block

                # Detect direct msg.payload.field access
                for m in re.finditer(r'\bmsg\.payload\.(\w+)\b', line):
                    # Determine WS type from enclosing block context
                    ws_type = current_ws_type
                    accesses.append(WSFieldAccess(
                        file=fpath,
                        line=line_num,
                        ws_type=ws_type,
                        ws_action="",
                        field_name=m.group(1),
                        access_path=f"msg.payload.{m.group(1)}",
                    ))

                # Detect payload assignment: const s = msg.payload;
                m = re.search(
                    r'\b(?:const|let|var)\s+(\w+)\s*=\s*msg\.payload\b',
                    line,
                )
                if m and current_ws_type:
                    var_name = m.group(1)
                    payload_vars[var_name] = current_ws_type

                # Detect field accesses on known payload variables
                for var_name, ws_type in list(payload_vars.items()):
                    pattern = re.compile(rf'\b{re.escape(var_name)}\.(\w+)\b')
                    for m in pattern.finditer(line):
                        field_name = m.group(1)
                        # Skip common non-data fields
                        if field_name in {
                            "length", "constructor", "prototype",
                            "toString", "valueOf", "hasOwnProperty",
                            "map", "filter", "reduce", "find", "forEach",
                        }:
                            continue
                        accesses.append(WSFieldAccess(
                            file=fpath,
                            line=line_num,
                            ws_type=ws_type,
                            ws_action="",
                            field_name=field_name,
                            access_path=f"{var_name}.{field_name}",
                        ))

    return accesses


def validate_ws_contracts(
    ws_accesses: List[WSFieldAccess],
) -> Tuple[List[WSFieldAccess], List[WSFieldAccess]]:
    """
    Cross-reference WS field accesses against WS_MESSAGE_SHAPES.
    Returns (violations, unmatched).
    """
    violations: List[WSFieldAccess] = []
    unmatched: List[WSFieldAccess] = []

    for wa in ws_accesses:
        if not wa.ws_type:
            # Can't determine WS type — skip
            unmatched.append(wa)
            continue

        type_shapes = WS_MESSAGE_SHAPES.get(wa.ws_type)
        if not type_shapes:
            # Unknown WS type
            unmatched.append(wa)
            continue

        # Check all actions for this WS type (usually only one)
        found = False
        for action, fields in type_shapes.items():
            if wa.field_name in fields:
                found = True
                break

        if not found:
            violations.append(wa)

    return violations, unmatched


def main():
    parser = argparse.ArgumentParser(
        description="ChainWatch Frontend-Backend Field Contract Validator",
    )
    parser.add_argument(
        "--path",
        default=".",
        help="Root path of the ChainWatch project (default: current directory)",
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--include-ws",
        action="store_true",
        default=False,
        help="Also validate WebSocket message payload shapes (default: off)",
    )
    args = parser.parse_args()

    base = args.path
    frontend_path = os.path.join(base, "frontend", "src")

    if not os.path.isdir(frontend_path):
        print(f"ERROR: Frontend path not found: {frontend_path}", file=sys.stderr)
        sys.exit(2)

    print(f"Scanning frontend field accesses in: {frontend_path}")
    accesses = find_frontend_field_accesses(frontend_path)
    print(f"Found {len(accesses)} REST endpoint field accesses to validate")

    violations, unmatched = validate_contracts(accesses)

    ws_violations: List[WSFieldAccess] = []
    ws_unmatched: List[WSFieldAccess] = []
    ws_accesses: List[WSFieldAccess] = []
    if args.include_ws:
        print("Including WebSocket payload field validation (--include-ws)")
        ws_accesses = find_ws_field_accesses(frontend_path)
        print(f"Found {len(ws_accesses)} WS payload field accesses to validate")
        ws_violations, ws_unmatched = validate_ws_contracts(ws_accesses)

    total_violations = len(violations) + len(ws_violations)

    if args.output == "json":
        import json
        result = {
            "total_accesses": len(accesses),
            "violations": [
                {
                    "endpoint": v.endpoint,
                    "object": v.object_name,
                    "field": v.field_name,
                    "file": v.frontend_file,
                    "line": v.frontend_line,
                    "suggestion": v.suggestion,
                }
                for v in violations
            ],
            "unmatched_count": len(unmatched),
            "ws_validation": args.include_ws,
            "ws_total_accesses": len(ws_accesses) if args.include_ws else 0,
            "ws_violations": [
                {
                    "ws_type": v.ws_type,
                    "field": v.field_name,
                    "file": v.file,
                    "line": v.line,
                    "access_path": v.access_path,
                }
                for v in ws_violations
            ],
        }
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'=' * 70}")
        print(f"FIELD CONTRACT VALIDATION RESULTS")
        print(f"{'=' * 70}")
        print(f"REST endpoint field accesses scanned: {len(accesses)}")
        print(f"REST violations found: {len(violations)}")
        print(f"REST unmatched (local vars / unknown): {len(unmatched)}")

        if violations:
            print(f"\n{'-' * 70}")
            print("REST VIOLATIONS (frontend expects field not in backend response):")
            print(f"{'-' * 70}")
            for i, v in enumerate(violations, 1):
                rel_path = v.frontend_file
                if rel_path.startswith(base):
                    rel_path = os.path.relpath(rel_path, base)
                print(f"\n  {i}. {v.object_name}.{v.field_name}")
                print(f"     File: {rel_path}:{v.frontend_line}")
                print(f"     Endpoint: {v.endpoint}")
                print(f"     → {v.suggestion}")

        if args.include_ws:
            print(f"\n{'=' * 70}")
            print(f"WEBSOCKET PAYLOAD VALIDATION RESULTS")
            print(f"{'=' * 70}")
            print(f"WS payload field accesses scanned: {len(ws_accesses)}")
            print(f"WS violations found: {len(ws_violations)}")
            print(f"WS unmatched (unknown type): {len(ws_unmatched)}")

            if ws_violations:
                print(f"\n{'-' * 70}")
                print("WS VIOLATIONS (frontend accesses field not in WS message shape):")
                print(f"{'-' * 70}")
                for i, v in enumerate(ws_violations, 1):
                    rel_path = v.file
                    if rel_path.startswith(base):
                        rel_path = os.path.relpath(rel_path, base)
                    print(f"\n  {i}. {v.access_path}")
                    print(f"     File: {rel_path}:{v.line}")
                    print(f"     WS type: {v.ws_type}")
                    print(f"     → Field '{v.field_name}' not in WS_MESSAGE_SHAPES['{v.ws_type}']")
            else:
                print("\n✅ All WebSocket payload contracts are valid!")

        if not violations and not ws_violations:
            print("\n✅ All field contracts are valid!")

        print(f"\n{'=' * 70}")

    sys.exit(1 if total_violations > 0 else 0)


if __name__ == "__main__":
    main()
