"""Read delivery diagnostics without invoking an OS helper on the UI thread."""
import json
from datetime import datetime, timezone

BLOCKED_SETTINGS = frozenset(('DisabledForUser', 'DisabledForApplication',
                              'DisabledByGroupPolicy', 'DisabledByManifest'))


def read_notification_health(store):
    state = {}
    try:
        candidate = json.loads((store.path.parent / 'worker_status.json').read_text(encoding='utf-8'))
        heartbeat = datetime.fromisoformat(candidate['heartbeat_utc'])
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
        if candidate.get('running') is True and 0 <= age <= 90:
            state = candidate
    except (OSError, ValueError, TypeError, KeyError):
        pass
    diagnostic = state.get('notification_status')
    if isinstance(diagnostic, dict) and diagnostic.get('setting') in BLOCKED_SETTINGS | {'Enabled'}:
        return {**diagnostic, 'blocked': diagnostic['setting'] in BLOCKED_SETTINGS,
                'evidence': 'worker_status'}
    errors = store.rows("SELECT last_error FROM reminder_events WHERE status='pending' AND last_error IS NOT NULL ORDER BY id DESC LIMIT 1")
    error = str(errors[0][0]) if errors else str(state.get('last_error') or '')
    setting = next((value for value in BLOCKED_SETTINGS if value in error), None)
    return {'blocked': bool(setting), 'setting': setting or 'Unknown',
            'evidence': 'delivery_error' if setting else 'unknown'}
