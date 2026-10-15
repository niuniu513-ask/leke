import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def test_task_scope_selection_search_and_browser_layout(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QLabel
    from gui_app import store, unipus_engine as engine
    from gui_app.page_unipus import UnipusPage
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(store, 'app_dir', lambda: str(tmp_path))
    monkeypatch.setattr(engine, '_store', None)
    monkeypatch.setattr(engine, '_runners', {})
    page = UnipusPage()
    tasks = [dict(index=0, display='[必修] Listening ' + '长任务名称' * 20, is_compulsory=True),
             dict(index=1, display='[已完成] Reading', is_compulsory=True, is_finished=True)]
    page.fill_tasks(tasks)
    assert page._checked_indices() == [0]
    page._boxes[0][1].setChecked(False)
    page.fill_tasks(tasks)
    assert page._checked_indices() == []
    page.cb_skip_finished.setChecked(False)
    assert page._checked_indices() == [1]
    page.ed_task_search.setText('Reading')
    assert page._boxes[0][1].parentWidget().isHidden()
    page.ed_task_search.clear()
    page.resize(720, 850)
    page.show()
    app.processEvents()
    page._toggle_browser_visible()
    assert page.browser.isHidden() and page.browser_canvas.isHidden()
    assert page.task_area.horizontalScrollBar().maximum() == 0
    assert page._boxes[0][1].parentWidget().findChild(QLabel).wordWrap()
    calls = []
    monkeypatch.setattr(page.runner, 'process', lambda indices: calls.append(('selected', indices)))
    monkeypatch.setattr(page.runner, 'quick', lambda: calls.append(('current',)))
    page._on_process()
    page.cb_task.setCurrentIndex(1)
    page._on_process()
    assert calls == [('selected', [1]), ('current',)]
    page.close()
