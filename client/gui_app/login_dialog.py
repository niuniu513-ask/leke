# -*- coding: utf-8 -*-
"""授权闸门：登录/注册/找回账号对话框 + 离线宽限判定。

未登录（或宽限期已过/被封禁/已过期）时完全锁死 —— 不看到主界面。
网络请求都跑在后台线程，UI 只等信号。

注册与找回账号都不在软件内做，跳浏览器门户完成（邮箱验证码由服务端发）：
  · 注册     → /register
  · 找回账号 → /forget（忘记密码，用注册邮箱收验证码重置）
"""
from __future__ import annotations

import threading
import webbrowser

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from api import kc_client


class _Signaler(QObject):
    done = Signal(object)  # (ok: bool, payload: dict)


class AccessGateDialog(QDialog):
    """启动闸门。accept() = 放行（self.access_info 有结果），reject() = 退出程序。"""

    def __init__(self, version: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.version = version
        self.access_info: dict = {}
        self._sig = _Signaler()
        self._sig.done.connect(self._on_done)
        self._busy = False

        self.setWindowTitle("乐课 · 账号登录")
        self.setModal(True)
        self.setFixedSize(380, 248)

        # 标题栏图标：模块级 lazy 缓存，独立启动时对话框也有品牌图标
        from gui_app.brand import app_icon as _brand_icon
        self.setWindowIcon(_brand_icon())

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(10)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(9)
        from gui_app.brand import render_pixmap, svg_for
        logo = QLabel()
        logo.setPixmap(render_pixmap(svg_for(48), 28, 28))
        logo.setFixedSize(28, 28)
        head.addWidget(logo)
        self.title = QLabel("登录乐课账号")
        self.title.setStyleSheet("font-size:17px;font-weight:600;")
        head.addWidget(self.title)
        head.addStretch(1)
        root.addLayout(head)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # 唯一页：登录（注册跳浏览器门户，软件内不内置注册表单）
        login_page = QWidget()
        lf = QVBoxLayout(login_page)
        lf.setContentsMargins(0, 0, 0, 0)
        lf.setSpacing(8)
        self.in_user = QLineEdit()
        self.in_user.setPlaceholderText("账号 / 邮箱")
        self.in_pass = QLineEdit()
        self.in_pass.setPlaceholderText("密码")
        self.in_pass.setEchoMode(QLineEdit.EchoMode.Password)
        lf.addWidget(self.in_user)
        lf.addWidget(self.in_pass)
        btns = QHBoxLayout()
        self.btn_submit = QPushButton("登 录")
        self.btn_submit.setDefault(True)
        btns.addWidget(self.btn_submit, 1)
        lf.addLayout(btns)

        # 注册 / 找回账号都跳浏览器门户（软件内不内置表单，验证码由服务端发）
        links = QHBoxLayout()
        links.setSpacing(6)
        self.btn_goto_reg = QPushButton("注册新账号")
        self.btn_goto_reg.setFlat(True)
        self.btn_forgot = QPushButton("忘记密码 / 找回账号")
        self.btn_forgot.setFlat(True)
        links.addWidget(self.btn_goto_reg)
        links.addStretch(1)
        links.addWidget(self.btn_forgot)
        lf.addLayout(links)
        self.stack.addWidget(login_page)

        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setStyleSheet("color:#d92d20;font-size:12px;")
        root.addWidget(self.msg)

        self.btn_submit.clicked.connect(self._do_login)
        self.in_pass.returnPressed.connect(self._do_login)
        self.btn_goto_reg.clicked.connect(self._open_register)
        self.btn_forgot.clicked.connect(self._open_forgot)

        # 有历史 token 就静默验证（含离线宽限判定）
        if kc_client.load_state().get("kc_token"):
            self._set_busy(True, "正在验证登录状态…")
            threading.Thread(
                target=self._verify_saved, args=(self.version,), daemon=True
            ).start()

    # ---------- 线程回调 ----------

    def _verify_saved(self, version: str) -> None:
        result = kc_client.check_access(version=version)
        self._sig.done.emit({"kind": "verify", **result})

    def _do_login(self) -> None:
        if self._busy:
            return
        u = self.in_user.text().strip()
        p = self.in_pass.text()
        if not u or not p:
            self.msg.setText("请输入账号和密码")
            return
        self._set_busy(True, "登录中…")
        threading.Thread(
            target=self._login_worker, args=(u, p, self.version), daemon=True
        ).start()

    def _login_worker(self, u: str, p: str, version: str) -> None:
        try:
            kc_client.login(u, p, version=version)
            hb = kc_client.heartbeat(version=version)
            self._sig.done.emit({"kind": "verify", "mode": "online", "reason": "", "hb": hb})
        except kc_client.KcError as e:
            self._sig.done.emit({"kind": "error", "msg": str(e)})

    def _open_register(self) -> None:
        """注册跳浏览器：门户网页完成邮箱验证码注册，软件内不内置注册表单。"""
        self._open_portal("/register", "注册")

    def _open_forgot(self) -> None:
        """找回账号跳浏览器：门户 /forget 用注册邮箱收验证码重置密码。"""
        self._open_portal("/forget", "找回账号")

    def _open_portal(self, path: str, what: str) -> None:
        url = kc_client._server() + path
        try:
            webbrowser.open(url)
            self.msg.setStyleSheet("color:#69718a;font-size:12px;")
            self.msg.setText(f"已在浏览器打开{what}页：\n" + url)
        except Exception:
            self.msg.setStyleSheet("color:#d92d20;font-size:12px;")
            self.msg.setText("打开浏览器失败，请手动访问：" + url)

    # ---------- UI 状态 ----------

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.btn_submit.setEnabled(not busy)
        self.msg.setStyleSheet(
            "color:#69718a;font-size:12px;" if busy else "color:#d92d20;font-size:12px;"
        )
        self.msg.setText(text)
        if not busy:
            self.msg.setText("")

    def _on_done(self, payload: dict) -> None:
        kind = payload.get("kind")
        if kind == "error":
            self._set_busy(False)
            self.msg.setText(payload.get("msg", "操作失败"))
            return
        mode = payload.get("mode")
        if mode in ("online", "grace"):
            self.access_info = payload
            self.accept()
            return
        # mode == login：需要登录
        self._set_busy(False)
        if payload.get("reason"):
            self.msg.setText(payload["reason"])
        self.in_pass.clear()
