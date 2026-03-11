"""Security: prompt injection defense and egress monitoring."""
from trouter.security.egress_monitor import (
    audit_file_access as audit_file_access,
    scan_for_exfiltration as scan_for_exfiltration,
)
from trouter.security.prompt_guard import (
    check_for_canaries as check_for_canaries,
    fence_data as fence_data,
    sanitize_prompt as sanitize_prompt,
)

__all__ = [
    "sanitize_prompt",
    "fence_data",
    "check_for_canaries",
    "audit_file_access",
    "scan_for_exfiltration",
]
