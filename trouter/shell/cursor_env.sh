#!/usr/bin/env bash
# ============================================================================
# Trouter Cursor Environment Setup
# ============================================================================
# Sets up environment variables for the trouter dispatch layer.
# Source this file before running trouter cursor-agent or dispatch tasks.
#
# Auth priority: browser auth (cursor agent login) > keychain > env var
# In local/native dispatch mode, API keys are not needed.
# ============================================================================

# ============================================================================
# Resolve TROUTER_ROOT
# ============================================================================

# If TROUTER_ROOT is not already set, derive from this script's location
if [[ -z "${TROUTER_ROOT:-}" ]]; then
    # shell/ -> trouter/ -> trouter (package root)
    TROUTER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
export TROUTER_ROOT

# ============================================================================
# Trouter-specific environment variables
# ============================================================================

export TROUTER_CONFIG_DIR="${TROUTER_ROOT}/etc"
export TROUTER_AGENTS_DIR="${TROUTER_ROOT}/agents"
export TROUTER_SHELL_DIR="${TROUTER_ROOT}/trouter/shell"

# ============================================================================
# Check dispatch mode: if local/native, no API key needed
# ============================================================================

_trouter_cursor_config="${TROUTER_CONFIG_DIR}/cursor_config.json"
if [[ -f "${_trouter_cursor_config}" ]]; then
    # Use cached value if available, else parse from JSON
    if [[ -n "${_CFG_DISPATCH_MODE:-}" ]]; then
        _dispatch_mode="${_CFG_DISPATCH_MODE}"
    else
        _dispatch_mode=$(python3 -c "import json; print(json.load(open('${_trouter_cursor_config}')).get('dispatch_mode','local'))" 2>/dev/null || echo "local")
    fi
    if [[ "${_dispatch_mode}" == "local" || "${_dispatch_mode}" == "native" ]]; then
        # Native mode uses browser/session auth; local mode uses Claude CLI.
        # Neither needs CURSOR_API_KEY -- unset to avoid stale key conflicts.
        unset CURSOR_API_KEY 2>/dev/null || true
        return 0 2>/dev/null || true
    fi
fi

# ============================================================================
# API mode permanently removed (2026-03)
# ============================================================================
# All dispatch now uses native (browser auth) or local (Claude CLI) modes.
# Keychain credential loading has been disabled.

# Ensure CURSOR_API_KEY is unset for native/local modes
unset CURSOR_API_KEY 2>/dev/null || true
