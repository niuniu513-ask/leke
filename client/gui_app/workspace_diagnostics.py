"""Local, allowlisted workspace snapshots; no requests or account contents."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from gui_app import __version__

PHASES = {'idle', 'ready', 'loaded', 'loading', 'running', 'connecting',
          'login_wait', 'logging_in', 'scanning', 'scanned', 'error'}


def _phase(value):
    return value if isinstance(value, str) and value in PHASES else 'unknown'


def snapshot(window):
    """Read existing runners only; never create one or touch a browser."""
    rain = window.yuketang_page
    unipus = window.unipus_page
    rows = []
    for index, account in enumerate(rain._store.accounts, 1):
        aid = account.get('id')
        runner = rain._runners.get(aid)
        rows.append({'platform': 'yuketang', 'account_number': index,
            'phase': _phase(runner.phase) if runner else 'idle',
            'saved_session': bool(account.get('cookie')),
            'browser_attached': bool(runner and runner._browser_driver is not None),
            'mode': 'standard' if account.get('login_mode') == 'standard' else 'professional',
            'courses': len(rain._courses.get(aid) or []),
            'selected': len(rain._selected_cache.get(aid) or [])})
    from gui_app.unipus_engine import all_runners
    runners = all_runners()
    for index, account in enumerate(unipus.store.accounts, 1):
        aid = account.get('id')
        runner = runners.get(aid)
        rows.append({'platform': 'unipus', 'account_number': index,
            'phase': _phase(runner.phase) if runner else 'idle',
            'browser_attached': bool(runner and runner.connected),
            'tasks': len(unipus._views.get(aid) or [])})
    page = getattr(window, '_current_key', '')
    return {'schema_version': 1, 'app_version': __version__,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'current_page': page if page in {'account', 'courses', 'run', 'settings',
            'ai', 'profile', 'unipus', 'yuketang', 'zhihuishu', 'live'} else 'unknown',
        'platforms': rows,
        'scope': 'Local state only; no real course completion verification.',
        'excluded': ['credentials', 'account identifiers', 'course contents',
                     'logs', 'browser storage', 'screenshots', 'network responses']}


def save_snapshot(window, filename):
    target = Path(filename)
    text = json.dumps(snapshot(window), ensure_ascii=False, indent=2)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                dir=target.parent, prefix='.leke-diagnostic-', delete=False) as stream:
            temporary = stream.name
            stream.write(text)
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return target
