"""Small, offline pre-publication gates. Call before any output write.

A gate raises rather than writing or swallowing errors; the caller's existing
atomic writer and workflow notification policy remain responsible for publication.
"""
import math


class OutputRejected(RuntimeError):
    """A candidate is not safe to replace the previous published snapshot."""


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def require(condition, label, reason):
    if not condition:
        raise OutputRejected(f"{label}: {reason}; previous output kept")


def unique_keys(keys, label):
    """Reject missing/duplicate identities before counting coverage."""
    seen = set()
    for key in keys:
        require(isinstance(key, str) and bool(key.strip()), label, "missing identity")
        require(key not in seen, label, f"duplicate identity {key}")
        seen.add(key)
    return seen


def retain_count(current, previous, label, *, min_share, minimum=1):
    """Absolute floor plus a relative floor against the restored snapshot."""
    require(0 < min_share <= 1, label, "invalid retention threshold")
    require(current >= minimum, label, f"only {current} usable records (minimum {minimum})")
    require(current >= previous * min_share, label,
            f"usable records {current}/{previous} below {min_share:.0%} retention")


def retain_keys(current, previous, label, *, min_share, anchors=(), minimum=1):
    """Count both usable identities and retained prior identities (not replacements)."""
    current, previous = set(current), set(previous)
    retain_count(len(current), len(previous), label, min_share=min_share, minimum=minimum)
    missing = set(anchors) & previous - current
    require(not missing, label, "missing previously usable anchors: " + ", ".join(sorted(missing)))
    retain_count(len(current & previous), len(previous), label + " prior identities",
                 min_share=min_share, minimum=0)
