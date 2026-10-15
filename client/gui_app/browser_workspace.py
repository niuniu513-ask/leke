"""Host only the Edge window owned by a particular WebDriver service.

No title matching and no shared browser process: each account retains its own
profile/session. Unsupported systems and attachment failures stay external.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QStackedWidget, QVBoxLayout


def descendants(root: int, parents: dict[int, int]) -> set[int]:
    if root <= 0:
        return set()
    found = {root}
    while True:
        children = {pid for pid, parent in parents.items() if parent in found}
        if children <= found:
            return found
        found |= children


class WindowsHost:
    def __init__(self):
        self.u = ctypes.WinDLL('user32', use_last_error=True)
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, args, result in (
            ('GetParent', [W.HWND], W.HWND),
            ('SetParent', [W.HWND, W.HWND], W.HWND),
            ('IsWindow', [W.HWND], W.BOOL),
            ('IsWindowVisible', [W.HWND], W.BOOL),
            ('GetWindowThreadProcessId', [W.HWND, ctypes.POINTER(W.DWORD)], W.DWORD),
            ('MoveWindow', [W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.BOOL], W.BOOL),
            ('ShowWindow', [W.HWND, ctypes.c_int], W.BOOL),
        ):
            fn = getattr(self.u, name); fn.argtypes = args; fn.restype = result
        self.get_style = self.u.GetWindowLongPtrW
        self.get_style.argtypes = [W.HWND, ctypes.c_int]; self.get_style.restype = ctypes.c_ssize_t
        self.set_style = self.u.SetWindowLongPtrW
        self.set_style.argtypes = [W.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.set_style.restype = ctypes.c_ssize_t
        self.callback = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        self.u.EnumWindows.argtypes = [self.callback, W.LPARAM]
        self.u.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
        self.k.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
        self.k.CreateToolhelp32Snapshot.restype = W.HANDLE
        self.k.CloseHandle.argtypes = [W.HANDLE]

    def owned_pids(self, root):
        class Entry(ctypes.Structure):
            _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD),
                        ('heap', ctypes.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                        ('parent', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD),
                        ('exe', W.WCHAR * 260)]
        snap = self.k.CreateToolhelp32Snapshot(2, 0)
        if snap == ctypes.c_void_p(-1).value:
            return set()
        parents = {}
        try:
            e = Entry(); e.size = ctypes.sizeof(e)
            for name in ('Process32FirstW', 'Process32NextW'):
                getattr(self.k, name).argtypes = [W.HANDLE, ctypes.POINTER(Entry)]
            ok = self.k.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                parents[int(e.pid)] = int(e.parent)
                ok = self.k.Process32NextW(snap, ctypes.byref(e))
        finally:
            self.k.CloseHandle(snap)
        return descendants(root, parents) if root in parents else set()

    def pid(self, hwnd):
        p = W.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        return int(p.value)

    def find(self, root):
        pids = self.owned_pids(root)
        windows = []
        @self.callback
        def visit(hwnd, _):
            name = ctypes.create_unicode_buffer(128)
            self.u.GetClassNameW(hwnd, name, 128)
            if name.value == 'Chrome_WidgetWin_1' and self.pid(hwnd) in pids:
                if self.u.IsWindowVisible(hwnd):
                    windows.append(int(hwnd))
            return True
        self.u.EnumWindows(visit, 0)
        return windows[0] if windows else 0

    def attach(self, hwnd, parent):
        original = (self.u.GetParent(hwnd), self.get_style(hwnd, -16))
        # Keep browser chrome (tabs/address bar); remove OS caption and frame.
        style = (original[1] & ~0x80CF0000) | 0x40000000
        self.set_style(hwnd, -16, style)
        ctypes.set_last_error(0)
        self.u.SetParent(hwnd, parent)
        if self.u.GetParent(hwnd) != parent:
            self.set_style(hwnd, -16, original[1])
            raise OSError(ctypes.get_last_error(), '无法嵌入浏览器窗口')
        self.u.ShowWindow(hwnd, 5)
        return original

    def detach(self, hwnd, original):
        if self.u.IsWindow(hwnd):
            self.u.SetParent(hwnd, original[0])
            self.set_style(hwnd, -16, original[1])
            self.u.MoveWindow(hwnd, 100, 100, 1100, 760, True)
            self.u.ShowWindow(hwnd, 5)


class BrowserSession(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('BrowserCanvas')
        self.setAttribute(Qt.WA_NativeWindow)
        self.native = WindowsHost() if sys.platform == 'win32' else None
        self.root_pid = 0
        self.hwnd = 0
        self.original = None
        self.external = False
        self.display_browser = True
        self.hidden_external = 0
        self.hint = QLabel('课程网页会在这里打开\n\n先在右侧「账号与 AI」保存账号，再点击「连接并登录」。', self)
        self.hint.setObjectName('BrowserEmpty')
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self._try_attach)

    def start(self, pid):
        self.release()
        self.root_pid = int(pid)
        self.external = False
        self.hint.setText('正在打开课程网页…\n登录验证可直接在这里完成。')
        self.hint.show()
        self.deadline = time.monotonic() + 15
        self.timer.start()

    def _try_attach(self):
        if not self.native:
            self._fallback(); return
        hwnd = self.native.find(self.root_pid)
        if hwnd:
            try:
                self.original = self.native.attach(hwnd, int(self.winId()))
                self.hwnd = hwnd
                self.hint.hide()
                self.timer.stop()
                self._resize_browser()
                if not self.display_browser:
                    self.native.u.ShowWindow(self.hwnd, 0)
                return
            except OSError:
                self._fallback(); return
        if time.monotonic() >= self.deadline:
            self._fallback()

    def _fallback(self):
        self.timer.stop()
        self.external = True
        self.hint.setText('浏览器已使用独立窗口\n请在 Edge 中完成登录与课程页面操作。')
        self.hint.show()
        if not self.display_browser:
            self.set_display(False)

    def set_display(self, visible):
        self.display_browser = bool(visible)
        if not self.native:
            return
        hwnd = self.hwnd or self.hidden_external or self.native.find(self.root_pid)
        if (hwnd and self.native.u.IsWindow(hwnd)
                and self.native.pid(hwnd) in self.native.owned_pids(self.root_pid)):
            self.native.u.ShowWindow(hwnd, 5 if visible else 0)
            if not self.hwnd:
                self.hidden_external = 0 if visible else hwnd

    def release(self):
        self.timer.stop()
        if self.hwnd and self.original:
            self.native.detach(self.hwnd, self.original)
        self.hwnd = 0
        self.original = None

    def toggle_external(self):
        if not self.root_pid:
            return
        if self.external:
            self.start(self.root_pid)
        else:
            self.release()
            self._fallback()

    def reset(self):
        self.release()
        self.root_pid = 0
        self.hidden_external = 0
        self.hint.setText('当前账号未连接\n在右侧配置账号后，点击「连接并登录」。')
        self.hint.show()

    def _resize_browser(self):
        if self.hwnd and self.native.u.IsWindow(self.hwnd):
            scale = self.devicePixelRatioF()
            self.native.u.MoveWindow(self.hwnd, 0, 0, round(self.width()*scale), round(self.height()*scale), True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.hint.setGeometry(28, 28, max(0, self.width()-56), max(0, self.height()-56))
        self._resize_browser()


class BrowserWorkspace(QStackedWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.sessions = {}
        self.display_browser = True
        self.setMinimumSize(300, 240)

    def select(self, account):
        if account not in self.sessions:
            session = BrowserSession(self)
            self.sessions[account] = session
            self.addWidget(session)
            session.set_display(self.display_browser)
        self.setCurrentWidget(self.sessions[account])

    def start(self, account, pid):
        if account not in self.sessions:
            current = self.currentWidget()
            self.select(account)
            if current:
                self.setCurrentWidget(current)
        self.sessions[account].start(pid)

    def reset(self, account):
        if account in self.sessions:
            self.sessions[account].reset()

    def release_all(self):
        for session in self.sessions.values():
            session.release()

    def set_browser_visible(self, visible):
        self.display_browser = bool(visible)
        for session in self.sessions.values():
            session.set_display(visible)
        self.setVisible(visible)
