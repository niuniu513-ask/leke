"""Real Windows/Edge embedding probe; never logs in or submits coursework."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PySide6.QtWidgets import QApplication
from selenium import webdriver
from gui_app.browser_workspace import BrowserWorkspace

app = QApplication([])
host = BrowserWorkspace()
host.resize(1000, 700)
host.select('probe')
host.show()
results = {}
drivers = []

def pump(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.03)

try:
    with tempfile.TemporaryDirectory(prefix='leke_browser_probe_') as profile:
        options = webdriver.EdgeOptions()
        options.add_argument('--user-data-dir=' + profile)
        driver = webdriver.Edge(options=options)
        drivers.append(driver)
        driver.get('data:text/html,<h1>Course browser probe</h1><input id="answer"><button onclick="document.title=document.getElementById(\'answer\').value">Check</button>')
        pid = driver.service.process.pid
        host.start('probe', pid)
        pump(3)
        session = host.sessions['probe']
        results['attached'] = bool(session.hwnd)
        if not session.hwnd:
            raise RuntimeError('Edge embedding failed')
        hwnd = session.hwnd
        results['parent'] = session.native.u.GetParent(hwnd) == int(session.winId())
        results['owned'] = session.native.pid(hwnd) in session.native.owned_pids(pid)
        host.resize(850, 580)
        pump(.3)
        driver.find_element('id', 'answer').send_keys('interactive')
        driver.find_element('tag name', 'button').click()
        results['interactive'] = driver.title == 'interactive'
        host.select('other')
        pump(.3)
        results['other_is_empty'] = host.currentWidget().hwnd == 0
        results['inactive_hidden'] = not session.native.u.IsWindowVisible(hwnd)
        results['original_session_preserved'] = driver.title == 'interactive'
        host.select('probe')
        pump(.5)
        results['active_visible'] = bool(session.native.u.IsWindowVisible(hwnd))
        from PIL import ImageGrab
        from PySide6.QtCore import QPoint
        point = host.mapToGlobal(QPoint(0, 0))
        scale = host.devicePixelRatioF()
        x, y = round(point.x()*scale), round(point.y()*scale)
        ImageGrab.grab(bbox=(x, y, x+round(host.width()*scale), y+round(host.height()*scale))).save(str(ROOT / '_reg_logs' / 'chain_audit' / 'browser_screen.png'))
        host.grab().save(str(ROOT / '_reg_logs' / 'chain_audit' / 'browser_native.png'))
        session.toggle_external()
        pump(.3)
        results['detached'] = not session.native.u.GetParent(hwnd)
        session.toggle_external()
        pump(1)
        results['reattached'] = session.hwnd == hwnd
        session.reset()
        results['reset'] = not session.root_pid and not session.hwnd
        driver.quit()
        drivers.remove(driver)
        pump(.5)
finally:
    host.release_all()
    for driver in drivers:
        driver.quit()
    host.close()
    out = ROOT / '_reg_logs' / 'chain_audit' / 'browser_native.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding='utf8')
    print(json.dumps(results))
assert results and all(results.values()), results
