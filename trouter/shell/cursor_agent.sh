#!/usr/bin/env bash
# ============================================================================
# Trouter Cursor Agent Dispatch Wrapper
# ============================================================================
# Native-first Cursor IDE agent wrapper (native -> local).
# Adapted from CLI/bin/cursor-agent for the trouter package.
#
# Best for:
#   - Quick single-file edits
#   - Fast iterative development
#   - Code exploration and understanding
#   - Rapid prototyping
#   - Visual feedback during development
#
# Usage: cursor_agent.sh [OPTIONS] <prompt>
#
# Examples:
#   cursor_agent.sh "Fix the type error on line 42"
#   cursor_agent.sh --mode=plan "Add error handling to this function"
#   cursor_agent.sh -p quick-edit -o result.json "Rename variable x to count"
# ============================================================================

set -euo pipefail

# ============================================================================
# Path Resolution
# ============================================================================

# Determine script location and TROUTER_ROOT
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shell/ -> trouter/ -> trouter (package root)
TROUTER_ROOT="$(dirname "$(dirname "${SCRIPT_DIR}")")"

# Also resolve CLI_ROOT for shared libraries if available (sibling package)
CLI_ROOT="${TROUTER_ROOT}/../CLI"
if [[ ! -d "${CLI_ROOT}" ]]; then
    CLI_ROOT=""
fi

# Source trouter environment setup
source "${SCRIPT_DIR}/cursor_env.sh" || true

# Source shared libraries from CLI if available (subagent_core, prompt context)
if [[ -n "${CLI_ROOT}" && -d "${CLI_ROOT}/lib" ]]; then
    source "${CLI_ROOT}/lib/subagent_core.sh" 2>/dev/null || true
    source "${CLI_ROOT}/lib/build_prompt_context.sh" 2>/dev/null || true
fi

# ============================================================================
# Minimal logging fallback (if subagent_core.sh was not loaded)
# ============================================================================

if ! type log_info &>/dev/null; then
    log_info()  { echo "[INFO]  $*"; }
    log_error() { echo "[ERROR] $*" >&2; }
    log_warn()  { echo "[WARN]  $*" >&2; }
    log_debug() { [[ "${DEBUG:-0}" == "1" ]] && echo "[DEBUG] $*" >&2 || true; }
    log_success() { echo "[OK]    $*"; }
fi

if ! type init_cli_env &>/dev/null; then
    init_cli_env() { true; }
fi

if ! type get_timeout_cmd &>/dev/null; then
    get_timeout_cmd() {
        if command -v gtimeout &>/dev/null; then echo "gtimeout"
        elif command -v timeout &>/dev/null; then echo "timeout"
        fi
    }
fi

if ! type format_duration &>/dev/null; then
    format_duration() {
        local secs="$1"
        if (( secs >= 60 )); then
            echo "$((secs / 60))m $((secs % 60))s"
        else
            echo "${secs}s"
        fi
    }
fi

if ! type write_output_envelope &>/dev/null; then
    write_output_envelope() { true; }
fi

# ============================================================================
# Default Configuration
# ============================================================================

CURSOR_MODE="${CURSOR_MODE:-ask}"
CURSOR_TIMEOUT="${CURSOR_TIMEOUT:-600}"
CURSOR_MODEL="${CURSOR_MODEL:-composer-1.5}"
MODEL_EXPLICIT="false"

# Read persistent cursor config from trouter/etc/ -- single parse for all fields
CURSOR_CONFIG="${TROUTER_ROOT}/etc/cursor_config.json"
_CFG_ENABLED="True"
_CFG_MODEL=""
_CFG_COMPOSER_ONLY="false"
_CFG_ALLOWED_MODELS=""
_CFG_DISPATCH_MODE="native"
_CFG_API_KEY=""

if [[ -f "${CURSOR_CONFIG}" ]]; then
    _cfg_blob=$(python3 -c "
import json, sys
try:
    d = json.load(open('${CURSOR_CONFIG}'))
    print(d.get('enabled', True))
    print(d.get('model_override') or '')
    print(str(d.get('composer_only', False)).lower())
    m = d.get('allowed_models', [])
    print(','.join(m) if isinstance(m, list) else '')
    print(d.get('dispatch_mode', 'native'))
    p = d.get('api_key_pool', [])
    print(next((x for x in p if isinstance(x, str) and x.strip()), '') if isinstance(p, list) else '')
except Exception:
    print('True'); print(''); print('false'); print(''); print('native'); print('')
" 2>/dev/null || printf 'True\n\nfalse\n\nnative\n')

    IFS=$'\n' read -r _CFG_ENABLED _CFG_MODEL _CFG_COMPOSER_ONLY _CFG_ALLOWED_MODELS _CFG_DISPATCH_MODE _CFG_API_KEY <<< "${_cfg_blob}"

    if [[ "${_CFG_ENABLED}" == "False" ]]; then
        echo "ERROR: Cursor agents are disabled via ${CURSOR_CONFIG}" >&2
        echo "Re-enable with: trouter cursor-enable" >&2
        exit 1
    fi

    if [[ -n "${_CFG_MODEL}" ]]; then
        CURSOR_MODEL="${_CFG_MODEL}"
    fi
fi

OUTPUT_FORMAT="${OUTPUT_FORMAT:-text}"
PROFILE=""
OUTPUT_FILE=""
PROMPT=""
FORCE="${FORCE:-false}"
CONTEXT_FILES=""
FAST_MODE="${FAST_MODE:-false}"
NO_TIMEOUT="${NO_TIMEOUT:-false}"

# iTerm integration flags
ITERM_MODE="${ITERM_MODE:-false}"
ITERM_SESSION=""
ITERM_DETACH="${ITERM_DETACH:-false}"
NOTIFY="${NOTIFY:-}"
ITERM_INNER="${ITERM_INNER:-false}"

# ============================================================================
# Help
# ============================================================================

show_help() {
    cat << 'EOF'
Trouter Cursor Agent Dispatch Wrapper
======================================

Native-first Cursor IDE agent wrapper for the trouter package.
Backend preference is dispatch_mode-driven with fallback order:
native -> local (API mode permanently removed)

USAGE:
    cursor_agent.sh [OPTIONS] <prompt>

OPTIONS:
    --mode MODE             Operation mode (default: ask)
                            Options: ask, plan
    --model MODEL           AI model to use (default: composer-1.5)
    --fast                  Use composer-1.5 for quick tasks (overrides --model)
    -p, --profile NAME      Configuration profile to load
    -t, --timeout SECS      Execution timeout in seconds (default: 600)
    --no-timeout            Disable timeout entirely
    -o, --output FILE       Write JSON output to file
    --force                 Apply changes without confirmation
    -f, --file FILE         Add file to context (can be repeated)
    --debug                 Enable debug output
    --iterm                 Run in tmux session with iTerm2 window
    --session NAME          Custom tmux session name (with --iterm)
    --detach                Skip iTerm attachment (tmux-only, background)
    --notify                Send macOS notification on completion
    --no-notify             Disable completion notification
    -h, --help              Show this help message

EXAMPLES:
    cursor_agent.sh "Fix the typo on line 42"
    cursor_agent.sh --mode=plan "Add authentication"
    cursor_agent.sh --iterm "Refactor database module"

ENVIRONMENT VARIABLES:
    CURSOR_MODE     - Default mode (ask)
    CURSOR_TIMEOUT  - Default timeout in seconds (600)
    CURSOR_MODEL    - Default model (composer-1.5)
    TROUTER_ROOT    - Trouter package root (auto-detected)

EOF
}

# ============================================================================
# Argument Parsing
# ============================================================================

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --mode)       CURSOR_MODE="$2"; shift 2 ;;
            --model)      CURSOR_MODEL="$2"; MODEL_EXPLICIT="true"; shift 2 ;;
            -p|--profile) PROFILE="$2"; shift 2 ;;
            -t|--timeout) CURSOR_TIMEOUT="$2"; shift 2 ;;
            -o|--output)  OUTPUT_FILE="$2"; shift 2 ;;
            --fast)       FAST_MODE="true"; CURSOR_MODEL="composer-1.5"; shift ;;
            --no-timeout) NO_TIMEOUT="true"; shift ;;
            --force)      FORCE="true"; shift ;;
            -f|--file)
                if [[ -n "${CONTEXT_FILES}" ]]; then
                    CONTEXT_FILES="${CONTEXT_FILES},$2"
                else
                    CONTEXT_FILES="$2"
                fi
                shift 2
                ;;
            --debug)      export DEBUG=1; shift ;;
            --iterm)      ITERM_MODE="true"; shift ;;
            --session)    ITERM_SESSION="$2"; shift 2 ;;
            --detach)     ITERM_DETACH="true"; shift ;;
            --notify)     NOTIFY="true"; shift ;;
            --no-notify)  NOTIFY="false"; shift ;;
            --_iterm-inner) ITERM_INNER="true"; shift ;;
            -h|--help)    show_help; exit 0 ;;
            -*)           log_error "Unknown option: $1"; echo "Use --help for usage"; exit 1 ;;
            *)            PROMPT="$*"; break ;;
        esac
    done
}

# ============================================================================
# Model Policy Enforcement
# ============================================================================

enforce_cursor_model_policy() {
    [[ ! -f "${CURSOR_CONFIG}" ]] && return
    local composer_only="${_CFG_COMPOSER_ONLY}"
    local allowed="${_CFG_ALLOWED_MODELS}"

    # If composer_only is explicitly true, enforce it
    if [[ "${composer_only}" == "true" ]]; then
        if [[ "${CURSOR_MODEL}" != "composer-1.5" ]]; then
            log_debug "Enforcing composer-1.5 (composer_only=true; --model ${CURSOR_MODEL} ignored)"
        fi
        CURSOR_MODEL="composer-1.5"
        return
    fi

    # Validate requested model is in allowed_models
    if [[ -n "${allowed}" ]]; then
        local model_ok=false
        IFS=',' read -ra model_list <<< "${allowed}"
        for m in "${model_list[@]}"; do
            if [[ "${CURSOR_MODEL}" == "${m}" ]]; then
                model_ok=true
                break
            fi
        done
        if [[ "${model_ok}" == "false" ]]; then
            log_debug "Model ${CURSOR_MODEL} not in allowed_models, falling back to composer-1.5"
            CURSOR_MODEL="composer-1.5"
        fi
    fi

    # Keyword-based auto-routing (only when no explicit --model was passed)
    if [[ "${MODEL_EXPLICIT}" == "true" ]]; then
        log_debug "Skipping keyword routing (--model explicitly set to ${CURSOR_MODEL})"
        return
    fi
    if [[ "${CURSOR_MODEL}" == "composer-1.5" ]] && [[ -n "${PROMPT}" ]]; then
        local prompt_lower
        prompt_lower=$(echo "${PROMPT}" | tr '[:upper:]' '[:lower:]')
        if echo "${prompt_lower}" | grep -qE '(critical|qc|quality.control|review|audit|security|validate correctness)'; then
            if echo ",${allowed}," | grep -q ",gpt-5.3-codex,"; then
                CURSOR_MODEL="gpt-5.3-codex"
                log_debug "Auto-routing to Codex (QC keyword detected in prompt)"
            fi
        fi
    fi
}

# ============================================================================
# Native Cursor Agent Discovery
# ============================================================================

find_native_agent() {
    local agent_dir="${HOME}/.local/share/cursor-agent/versions"
    if [[ -d "${agent_dir}" ]]; then
        local latest
        latest="$(ls -1d "${agent_dir}"/*/ 2>/dev/null | sort -r | head -1)"
        if [[ -n "${latest}" && -x "${latest}cursor-agent" ]]; then
            echo "${latest}cursor-agent"
            return 0
        fi
    fi
    # Fallback: symlink at ~/.local/bin/cursor-agent
    if [[ -L "${HOME}/.local/bin/cursor-agent" ]]; then
        local resolved
        resolved="$(readlink -f "${HOME}/.local/bin/cursor-agent" 2>/dev/null || readlink "${HOME}/.local/bin/cursor-agent")"
        if [[ "${resolved}" == *"cursor-agent/versions"* && -x "${resolved}" ]]; then
            echo "${resolved}"
            return 0
        fi
    fi
    return 1
}

# Read dispatch_mode from cached config
_read_dispatch_mode() {
    echo "${_CFG_DISPATCH_MODE:-native}"
}

# Map Cursor model names to Claude CLI model names
_map_cursor_model_to_claude() {
    local model="${1:-composer-1.5}"
    case "${model}" in
        composer-1.5) echo "sonnet" ;;
        gpt-5.3-codex-low|gpt-5.3-codex-low-fast) echo "haiku" ;;
        gpt-5.3-codex|gpt-5.3-codex-fast|gpt-5.3-codex-high|gpt-5.3-codex-high-fast) echo "sonnet" ;;
        gpt-5.3-codex-xhigh|gpt-5.3-codex-xhigh-fast) echo "opus" ;;
        *) echo "" ;;
    esac
}

# ============================================================================
# Dispatch Backends
# ============================================================================

# Execute via native Cursor agent binary (browser auth, api2.cursor.sh)
execute_native() {
    local prompt="$1"
    local native_bin
    native_bin="$(find_native_agent)" || {
        log_error "Native cursor-agent binary not found at ~/.local/share/cursor-agent/versions/"
        log_error "Install Cursor IDE to get the native agent binary"
        return 1
    }

    log_info "Invoking native Cursor agent..."
    log_info "Model: ${CURSOR_MODEL}"
    log_info "Binary: ${native_bin}"

    local full_prompt="${prompt}"
    if type render_prompt_for_backend &>/dev/null; then
        full_prompt="$(render_prompt_for_backend "cursor-native" "$(pwd)" "${prompt}" "${CONTEXT_FILES:-}" "" 2>/dev/null)"
        log_debug "Applied shared prompt adapter (cursor-native)"
    elif type build_prompt_context_or_empty &>/dev/null && [[ "${CURSOR_SKIP_CONTEXT:-0}" != "1" ]]; then
        local ctx
        ctx=$(build_prompt_context_or_empty "$(pwd)" 2>/dev/null)
        full_prompt="${ctx}"$'\n\n'"${prompt}"
        log_debug "Injected workspace/git/rules context"
    fi

    local tmp_out tmp_err
    tmp_out=$(mktemp "/tmp/trouter-cursor-agent.XXXXXX")
    tmp_err=$(mktemp "/tmp/trouter-cursor-agent-err.XXXXXX")
    trap "rm -f '${tmp_out}' '${tmp_err}' 2>/dev/null" RETURN

    local exit_code=0 start_time=$(date +%s)

    # Native binary uses its own session auth; unset env keys to avoid conflicts
    unset CLAUDECODE 2>/dev/null || true
    unset CURSOR_API_KEY 2>/dev/null || true

    local -a agent_cmd=("${native_bin}" "--print" "--trust")
    agent_cmd+=("--workspace" "${WORKING_DIR:-$(pwd)}")
    agent_cmd+=("--model" "${CURSOR_MODEL}")
    agent_cmd+=("--output-format" "text")
    [[ "${FORCE}" == "true" ]] && agent_cmd+=("--force")
    agent_cmd+=("${full_prompt}")

    if [[ "${NO_TIMEOUT}" == "true" ]]; then
        "${agent_cmd[@]}" > "${tmp_out}" 2>"${tmp_err}" || exit_code=$?
    else
        local timeout_cmd=$(get_timeout_cmd)
        if [[ -n "${timeout_cmd}" ]]; then
            "${timeout_cmd}" "${CURSOR_TIMEOUT}" "${agent_cmd[@]}" > "${tmp_out}" 2>"${tmp_err}" || exit_code=$?
            [[ ${exit_code} -eq 124 ]] && log_error "Timed out after ${CURSOR_TIMEOUT}s" && return 124
        else
            "${agent_cmd[@]}" > "${tmp_out}" 2>"${tmp_err}" || exit_code=$?
        fi
    fi

    [[ -s "${tmp_err}" ]] && cat "${tmp_err}" >&2
    local result=$(cat "${tmp_out}")

    if [[ -n "${OUTPUT_FILE}" ]]; then
        cp "${tmp_out}" "${OUTPUT_FILE}"
        write_output_envelope "${OUTPUT_FILE}" "native" "${CURSOR_MODEL}" "${exit_code}" "${start_time}" "${prompt}"
    fi

    log_info "Completed in $(format_duration $(($(date +%s) - start_time))) (exit: ${exit_code}, dispatch: native)"

    echo "${result}"
    return ${exit_code}
}

# Execute via Claude CLI (local fallback)
execute_local() {
    local prompt="$1"
    local claude_bin
    claude_bin="$(command -v claude 2>/dev/null || true)"
    [[ -z "${claude_bin}" ]] && claude_bin="${HOME}/.local/bin/claude"
    if [[ ! -x "${claude_bin}" ]]; then
        log_error "Claude CLI not found at ${claude_bin}"
        return 1
    fi

    local full_prompt="${prompt}"
    if type render_prompt_for_backend &>/dev/null; then
        full_prompt="$(render_prompt_for_backend "claude-local" "$(pwd)" "${prompt}" "" "" 2>/dev/null)"
        log_debug "Applied shared prompt adapter (claude-local)"
    fi

    local mapped_model
    mapped_model="$(_map_cursor_model_to_claude "${CURSOR_MODEL}")"
    local -a cmd=("${claude_bin}" "--dangerously-skip-permissions")
    [[ -n "${mapped_model}" ]] && cmd+=("--model" "${mapped_model}")
    cmd+=("--max-turns" "1" "-p" "${full_prompt}")

    local exit_code=0
    if [[ "${NO_TIMEOUT}" == "true" ]]; then
        "${cmd[@]}" || exit_code=$?
    else
        local timeout_cmd
        timeout_cmd="$(get_timeout_cmd)"
        if [[ -n "${timeout_cmd}" ]]; then
            "${timeout_cmd}" "${CURSOR_TIMEOUT}" "${cmd[@]}" || exit_code=$?
        else
            "${cmd[@]}" || exit_code=$?
        fi
    fi
    return ${exit_code}
}

# ============================================================================
# Dispatch with Fallback (native -> local)
# ============================================================================

dispatch_with_fallback() {
    local prompt="$1"
    local mode
    mode="$(_read_dispatch_mode)"
    case "${mode}" in
        native)
            execute_native "${prompt}" && return 0
            log_warn "Native dispatch failed; trying local fallback"
            execute_local "${prompt}"
            return $?
            ;;
        local)
            execute_local "${prompt}"
            return $?
            ;;
        *)
            log_warn "Unknown dispatch_mode '${mode}', defaulting to native"
            execute_native "${prompt}" && return 0
            log_warn "Native dispatch failed; trying local fallback"
            execute_local "${prompt}"
            return $?
            ;;
    esac
}

# ============================================================================
# Main
# ============================================================================

main() {
    init_cli_env

    parse_args "$@"

    if [[ -z "${PROMPT}" ]]; then
        log_error "No prompt provided"
        echo ""
        show_help
        exit 1
    fi

    # Enforce model policy from config
    enforce_cursor_model_policy

    # Dispatch with native-first fallback order (native -> local)
    dispatch_with_fallback "${PROMPT}"
}

main "$@"
