from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import os
import time as monotonic_time

_zone_cache = None
_zone_checked = 0.0
_zone_fingerprint = None


def refresh_device_zone(force=False):
    global _zone_cache, _zone_checked, _zone_fingerprint
    local = datetime.now().astimezone()
    fingerprint = (os.environ.get("TZ"), local.utcoffset(), local.tzname())
    now = monotonic_time.monotonic()
    if force or _zone_cache is None or fingerprint != _zone_fingerprint or now - _zone_checked >= 15:
        try:
            from tzlocal import reload_localzone, get_localzone_name
            reload_localzone()
            _zone_cache = get_localzone_name()
        except (ImportError, OSError, ValueError):
            _zone_cache = _fallback_zone(local)
        _zone_checked, _zone_fingerprint = now, fingerprint
    return _zone_cache


def _fallback_zone(local):
    if os.environ.get("TZ"):
        return os.environ["TZ"]
    offset = local.utcoffset() or timedelta(0)
    minutes = int(offset.total_seconds() / 60)
    return "UTC" if minutes == 0 else "UTC%s%02d:%02d" % ("+" if minutes >= 0 else "-", abs(minutes)//60, abs(minutes)%60)


def device_zone():
    """IANA identity; fixed offset is a conservative last resort."""
    return refresh_device_zone()


def zone(name):
    if name.startswith('UTC') and len(name) == 9 and name[3] in '+-':
        minutes = int(name[4:6]) * 60 + int(name[7:9])
        return timezone(timedelta(minutes=minutes if name[3] == '+' else -minutes), name)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, TypeError) as error:
        raise ValueError('Unknown reminder timezone: %s. Restore a valid timezone or install tzdata.' % name) from error


def resolve_local(value, name, fold=0):
    """First fold by default. A gap moves forward to the first real minute."""
    local = value.replace(tzinfo=None)
    tz = zone(name)
    for _ in range(181):
        candidate = local.replace(tzinfo=tz, fold=fold)
        if candidate.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == local:
            return candidate
        local += timedelta(minutes=1)
    raise ValueError('Local time cannot be resolved')


def utc_stamp(value=None, name=None):
    # The current instant never depends on a cached zone or an ambiguous wall clock.
    if value is None:
        return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds')
    aware = value if value.tzinfo else resolve_local(value, name or device_zone(), value.fold)
    return aware.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds')


def event_local(row, utc_key, local_key, now_zone=None):
    stamp = row.get(utc_key) if hasattr(row, 'get') else row[utc_key] if utc_key in row.keys() else None
    if stamp:
        return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc).astimezone(zone(now_zone or device_zone())).replace(tzinfo=None)
    return datetime.fromisoformat(row[local_key] or row['scheduled_at'])


def event_due_local(row):
    return event_local(row, 'due_utc', 'due_at')


def event_scheduled_local(row):
    return event_local(row, 'scheduled_utc', 'scheduled_at')
