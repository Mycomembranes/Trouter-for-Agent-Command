"""
Data Exfiltration Monitoring for ROTIFER.

Lightweight egress monitoring that detects when sensitive data escapes
the agent sandbox:

1. File access audit — logs when agents read sensitive files
2. Outbound data check — scans agent outputs for canary tokens
3. Prompt length anomaly detection — flags oversized prompts as payload carriers

Usage:
    from CLI.lib.egress_monitor import (
        audit_file_access, scan_for_exfiltration, check_prompt_anomaly
    )
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("rotifer-egress-monitor")

# Directory for egress audit logs
EGRESS_LOG_DIR = Path.home() / ".claude" / "hooks_data" / "egress"

# Sensitive file patterns that should trigger alerts when accessed
SENSITIVE_PATTERNS = [
    re.compile(r"\.env$", re.IGNORECASE),
    re.compile(r"\.env\.", re.IGNORECASE),
    re.compile(r"credentials?\.(json|yaml|yml|toml|xml)$", re.IGNORECASE),
    re.compile(r"secrets?\.(json|yaml|yml|toml|xml)$", re.IGNORECASE),
    re.compile(r"api[_\-]?key", re.IGNORECASE),
    re.compile(r"\.pem$", re.IGNORECASE),
    re.compile(r"\.key$", re.IGNORECASE),
    re.compile(r"\.p12$", re.IGNORECASE),
    re.compile(r"\.pfx$", re.IGNORECASE),
    re.compile(r"\bid_rsa\b", re.IGNORECASE),
    re.compile(r"\bid_ed25519\b", re.IGNORECASE),
    re.compile(r"\bid_ecdsa\b", re.IGNORECASE),
    re.compile(r"authorized_keys$", re.IGNORECASE),
    re.compile(r"known_hosts$", re.IGNORECASE),
    re.compile(r"\.ssh/config$", re.IGNORECASE),
    re.compile(r"token\.json$", re.IGNORECASE),
    re.compile(r"\.netrc$", re.IGNORECASE),
    re.compile(r"\.npmrc$", re.IGNORECASE),
    re.compile(r"\.pypirc$", re.IGNORECASE),
]

# Patterns in output that suggest data exfiltration
EXFILTRATION_INDICATORS = [
    # API key patterns
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),          # OpenAI-style keys
    re.compile(r"sk-ant-[a-zA-Z0-9\-]{20,}"),    # Anthropic API keys
    re.compile(r"AKIA[0-9A-Z]{16}"),              # AWS access keys
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),           # GitHub PATs
    re.compile(r"gho_[a-zA-Z0-9]{36}"),           # GitHub OAuth
    re.compile(r"xox[bpsar]-[a-zA-Z0-9\-]+"),    # Slack tokens
    # SSH private key markers
    re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"),
    re.compile(r"-----BEGIN\s+OPENSSH\s+PRIVATE\s+KEY-----"),
    # Base64-encoded credential patterns (> 100 chars of base64)
    re.compile(r"(?:password|passwd|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9+/=]{20,}", re.IGNORECASE),
]

# ROTIFER canary pattern
CANARY_PATTERN = re.compile(r"RTFR_CAN_[0-9a-f]{24}")

# Prompt length anomaly threshold
PROMPT_ANOMALY_THRESHOLD = 5000


def _ensure_log_dir():
    """Create egress log directory if it doesn't exist."""
    EGRESS_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _append_log(filename: str, record: dict):
    """Append a JSON record to an egress log file."""
    import json
    _ensure_log_dir()
    log_file = EGRESS_LOG_DIR / filename
    with open(log_file, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def is_sensitive_path(filepath: str) -> bool:
    """Check if a file path matches sensitive file patterns."""
    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(filepath):
            return True
    return False


def audit_file_access(filepath: str, accessor: str = "unknown",
                      action: str = "read") -> Optional[dict]:
    """Log and alert when an agent accesses a sensitive file.

    Args:
        filepath: Path to the file being accessed
        accessor: Identifier of the agent/tool accessing the file
        action: Type of access (read/write/execute)

    Returns:
        Alert dict if sensitive, None otherwise
    """
    if not is_sensitive_path(filepath):
        return None

    alert = {
        "timestamp": datetime.now().isoformat(),
        "event": "sensitive_file_access",
        "filepath": filepath,
        "accessor": accessor,
        "action": action,
        "severity": "HIGH",
    }

    logger.warning(f"EGRESS ALERT: Sensitive file accessed: {filepath} by {accessor}")
    _append_log("file_access_audit.jsonl", alert)

    return alert


def scan_for_exfiltration(text: str, source: str = "unknown") -> list[dict]:
    """Scan text (e.g., agent output) for signs of data exfiltration.

    Checks for:
    - Canary tokens
    - API key patterns
    - SSH private key markers
    - Credential patterns

    Args:
        text: Text to scan
        source: Identifier of the source (e.g., agent session ID)

    Returns:
        List of detected exfiltration indicators
    """
    if not text:
        return []

    detections = []

    # Check for ROTIFER canary tokens
    canary_matches = CANARY_PATTERN.findall(text)
    if canary_matches:
        for match in canary_matches:
            detection = {
                "timestamp": datetime.now().isoformat(),
                "event": "canary_detected",
                "source": source,
                "canary": match,
                "severity": "CRITICAL",
            }
            detections.append(detection)
            logger.critical(f"EXFILTRATION ALERT: Canary token detected in output from {source}")

    # Check for credential patterns
    for pattern in EXFILTRATION_INDICATORS:
        matches = pattern.findall(text)
        if matches:
            detection = {
                "timestamp": datetime.now().isoformat(),
                "event": "credential_pattern_detected",
                "source": source,
                "pattern": pattern.pattern[:80],
                "match_count": len(matches),
                "severity": "HIGH",
            }
            detections.append(detection)
            logger.warning(
                f"EGRESS ALERT: Credential pattern '{pattern.pattern[:40]}' "
                f"found in output from {source}"
            )

    # Log all detections
    if detections:
        for d in detections:
            _append_log("exfiltration_alerts.jsonl", d)

    return detections


def check_prompt_anomaly(prompt: str, source: str = "unknown") -> Optional[dict]:
    """Flag prompts that exceed the anomaly threshold.

    Oversized prompts may be payload carriers for injection attacks.

    Args:
        prompt: The prompt text
        source: Identifier of the requester

    Returns:
        Anomaly dict if flagged, None otherwise
    """
    if not prompt or len(prompt) <= PROMPT_ANOMALY_THRESHOLD:
        return None

    anomaly = {
        "timestamp": datetime.now().isoformat(),
        "event": "prompt_length_anomaly",
        "source": source,
        "length": len(prompt),
        "threshold": PROMPT_ANOMALY_THRESHOLD,
        "severity": "MEDIUM",
    }

    logger.warning(
        f"PROMPT ANOMALY: Prompt from {source} is {len(prompt)} chars "
        f"(threshold: {PROMPT_ANOMALY_THRESHOLD})"
    )
    _append_log("prompt_anomalies.jsonl", anomaly)

    return anomaly


def get_egress_summary(days: int = 1) -> dict:
    """Get a summary of recent egress alerts.

    Args:
        days: Number of days to look back

    Returns:
        Summary dict with counts by event type
    """
    import json
    from datetime import timedelta

    _ensure_log_dir()
    cutoff = datetime.now() - timedelta(days=days)
    summary = {
        "period_days": days,
        "file_access_alerts": 0,
        "exfiltration_alerts": 0,
        "canary_detections": 0,
        "prompt_anomalies": 0,
    }

    log_files = {
        "file_access_audit.jsonl": "file_access_alerts",
        "exfiltration_alerts.jsonl": "exfiltration_alerts",
        "prompt_anomalies.jsonl": "prompt_anomalies",
    }

    for filename, key in log_files.items():
        log_path = EGRESS_LOG_DIR / filename
        if not log_path.exists():
            continue
        try:
            with open(log_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        ts = datetime.fromisoformat(record.get("timestamp", ""))
                        if ts >= cutoff:
                            summary[key] += 1
                            if record.get("event") == "canary_detected":
                                summary["canary_detections"] += 1
                    except (json.JSONDecodeError, ValueError):
                        continue
        except OSError:
            continue

    return summary
