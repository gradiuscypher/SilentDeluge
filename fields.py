"""Read effective Incus fields; normalize limits only for comparison."""

import re
from fractions import Fraction


def observed_fields(instance):
    config = instance["expanded_config"]
    if not isinstance(config, dict):
        raise ValueError("expanded_config must contain the effective instance settings")
    return {
        "kind": instance["type"],
        "cpu": config.get("limits.cpu"),
        "memory": config.get("limits.memory"),
        "state": instance["status"],
    }


def _cpu(value):
    text = str(value).strip()
    if re.fullmatch(r"[0-9]+", text):
        return "count", int(text)

    # Merge intervals rather than expanding a potentially large pin range.
    intervals = []
    for part in text.split(","):
        pin = re.fullmatch(r"([0-9]+)(?:\s*-\s*([0-9]+))?", part.strip())
        if not pin:
            return "literal", value
        start = int(pin[1])
        end = int(pin[2]) if pin[2] else start
        if end < start:
            return "literal", value
        intervals.append((start, end))
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return "pins", merged


_MEMORY_UNITS = {"": 1, "B": 1}
for _power, _prefix in enumerate("KMGTPE", 1):
    _MEMORY_UNITS[_prefix + "B"] = 1000 ** _power
    _MEMORY_UNITS[_prefix + "IB"] = 1024 ** _power


def _memory(value):
    size = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]*|%)", str(value).strip())
    if size:
        unit = size[2].upper()
        if unit == "%":
            return "percent", Fraction(size[1])
        if unit in _MEMORY_UNITS:
            return "bytes", Fraction(size[1]) * _MEMORY_UNITS[unit]
    return "literal", value


def differences(declared, observed):
    """Return disputed fields in display order; omitted limits make no claim."""
    keys = [] if declared["kind"] == observed["kind"] else ["kind"]
    for key, normalize in (("cpu", _cpu), ("memory", _memory)):
        if declared.get(key) is not None and normalize(declared[key]) != normalize(observed[key]):
            keys.append(key)
    return keys
