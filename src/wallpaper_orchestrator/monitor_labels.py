"""Human-friendly *unique* labels while retaining persistent Windows IDs as keys."""

from hashlib import sha1


def monitor_labels(monitors, mapping: dict[str, str]) -> dict[str, str]:
    """Return stable distinguishable labels even for two same-sized landscapes.

    A role in mapping (e.g. 'landscape') need not be unique. The model/UID
    in Windows' monitor device path and an ID fingerprint disambiguate it.
    Labels are presentation only; saved rules continue to use device_id.
    """
    roles = {mid: role for role, mid in mapping.items()}
    labels = {}
    for monitor in monitors:
        mid = monitor.device_id
        role = roles.get(mid) or getattr(monitor, "orientation", "display")
        dimensions = f"{monitor.width}×{monitor.height}"
        pieces = mid.split("#")
        model = pieces[1] if len(pieces) > 1 else ""
        identity = pieces[2].split("&")[-1] if len(pieces) > 2 else ""
        marker = " / ".join(value for value in (model, identity) if value)
        marker = marker[:28] if marker else "ID"
        unique = sha1(mid.encode("utf-8")).hexdigest()[:6]
        labels[mid] = f"{role} — {dimensions} — {marker} [{unique}]"
    return labels
