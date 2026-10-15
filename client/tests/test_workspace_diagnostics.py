import json
from types import SimpleNamespace as NS

from gui_app.workspace_diagnostics import save_snapshot


def test_snapshot_does_not_export_credentials_or_create_runners(tmp_path, monkeypatch):
    from gui_app import unipus_engine
    monkeypatch.setattr(unipus_engine, 'all_runners', lambda: {})
    account = dict(id='private-id', cookie='private-cookie', password='private-password',
                   name='private-name', login_mode='standard')
    window = NS(_current_key='yuketang',
        yuketang_page=NS(_store=NS(accounts=[account]), _runners={},
            _courses={'private-id': [{'course_name': 'private-course'}]},
            _selected_cache={}),
        unipus_page=NS(store=NS(accounts=[account]), _views={}))
    target = tmp_path / 'snapshot.json'
    save_snapshot(window, target)
    text = target.read_text(encoding='utf-8')
    assert 'private-' not in text
    payload = json.loads(text)
    assert payload['platforms'][0]['courses'] == 1
    assert payload['platforms'][1]['browser_attached'] is False
    assert not list(tmp_path.glob('.leke-diagnostic-*'))


def test_task_preferences_persist_and_are_isolated(tmp_path, monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from gui_app import store, unipus_engine, yuketang_engine
    from gui_app.page_unipus import UnipusPage
    from gui_app.page_yuketang import YuketangPage
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(store, 'app_dir', lambda: str(tmp_path))
    monkeypatch.setattr(unipus_engine, '_store', None)
    monkeypatch.setattr(unipus_engine, '_runners', {})
    monkeypatch.setattr(yuketang_engine, '_STORE', None)
    monkeypatch.setattr(yuketang_engine, '_runners', {})
    for page in (UnipusPage(), YuketangPage()):
        book = page.store if hasattr(page, 'store') else page._store
        first = page._current
        page.cb_task.setCurrentIndex(1)
        if hasattr(page, 'sp_jobs'):
            page.sp_jobs.setValue(3)
        else:
            page.cb_skip_finished.setChecked(False)
        second = book.add()['id']
        book.set_active(second)
        page._current = second
        page._restore_task_options()
        assert page.cb_task.currentIndex() == 0
        assert book.get(second).get('workspace_options') is None
        page._current = first
        page._restore_task_options()
        assert page.cb_task.currentIndex() == 1
        if hasattr(page, 'sp_jobs'):
            assert page.sp_jobs.value() == 3
        else:
            assert not page.cb_skip_finished.isChecked()
        page.close()
