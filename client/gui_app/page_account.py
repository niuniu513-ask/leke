# -*- coding: utf-8 -*-
"""账号页：多账号管理（添加 / 登录 / 退出 / 删除）。

版式
----
左侧是账号列表（固定宽度），右侧是当前账号的详情表单。
窗口拉宽时表单不跟着无限变长 —— 给表单列一个最大宽度，其余留白。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from gui_app import profiles
from gui_app import theme as T
from gui_app.icons import pixmap as _icon_pixmap
from gui_app.widgets import Badge, Card, Divider, EmptyState

FORM_WIDTH = 420          # 表单列最大宽度
LIST_WIDTH = 316          # 账号列表宽度

STATE_TEXT = {
    "idle": ("未登录", "idle"),
    "logging": ("登录中", "info"),
    "logged_in": ("已登录", "ok"),
    "studying": ("刷课中", "info"),
    "error": ("登录失败", "error"),
}


def _field(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("FieldLabel")
    return lb


class AccountRow(QWidget):
    """账号列表里的一行：名称 + 副标题 + 状态徽标。"""

    def __init__(self, acc: dict, display: str, state: str = "idle", parent=None):
        super().__init__(parent)
        text, kind = STATE_TEXT.get(state, STATE_TEXT["idle"])

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(10)

        mark = QLabel()
        mark.setFixedSize(32, 32)
        mark.setAlignment(Qt.AlignCenter)
        fg = T.PRIMARY if kind == "ok" else T.TEXT_SUB
        bg = T.PRIMARY_SOFT if kind == "ok" else T.CARD_SUB_2
        mark.setPixmap(_icon_pixmap("user", fg, 16))
        mark.setStyleSheet(f"background:{bg}; border-radius:9px;")
        lay.addWidget(mark)

        col = QVBoxLayout()
        col.setSpacing(2)
        self.name = QLabel(display)
        self.name.setObjectName("AccName")
        sub = QLabel(profiles.mask_user(acc.get("username", ""))
                     if not acc.get("use_cookies") else "cookies 登录")
        sub.setObjectName("AccSub")
        col.addWidget(self.name)
        col.addWidget(sub)
        lay.addLayout(col, 1)

        self.badge = Badge(text, kind)
        lay.addWidget(self.badge)

    def set_name(self, text: str):
        self.name.setText(text)

    def set_state(self, state: str):
        text, kind = STATE_TEXT.get(state, STATE_TEXT["idle"])
        self.badge.set_kind(kind, text)


class AccountPage(QWidget):
    sig_request_login = Signal(str, dict, bool)   # acc_id, 登录信息, 是否顺带读课程
    sig_request_logout = Signal(str)              # acc_id
    sig_accounts_changed = Signal()               # 增删账号后通知其它页面刷新
    sig_current_changed = Signal(str)             # 当前选中的账号变了

    def __init__(self, book: profiles.ProfileBook, parent=None):
        super().__init__(parent)
        self.book = book
        self._rows: dict[str, AccountRow] = {}
        self._states: dict[str, str] = {}
        self._busy: set[str] = set()
        self._pending_courses = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        cols = QHBoxLayout()
        cols.setSpacing(16)
        cols.addWidget(self._build_list(), 0)
        cols.addWidget(self._build_detail(), 1)
        root.addLayout(cols, 1)

        # 隐私承诺压成页脚一行：原来是顶部工具卡里的三行长说明（已随工具卡删除），
        # 但「信息只留在本机」是用户真正在意的一句，不能一并删掉（v1.6.2）。
        foot = QLabel("所有账号信息只保存在本机，不会上传。")
        foot.setObjectName("CardHint")
        root.addWidget(foot)

        self.reload()

    # ------------------------------------------------------------------ 构建
    def _build_list(self) -> Card:
        card = Card("账号列表", icon="layers", margins=(16, 16, 16, 16))
        card.setFixedWidth(LIST_WIDTH)

        # 「添加账号」并进卡头右侧：它跟列表本就是同一件事，没必要单独占一张
        # 顶部工具卡（还拖着三行长说明），白把整个列表压低一张卡的高度（v1.6.2）。
        self.btn_add = QPushButton("＋ 添加")
        self.btn_add.setProperty("variant", "primary")
        self.btn_add.setMinimumHeight(30)
        self.btn_add.setToolTip("新增一个学习通账号")
        self.btn_add.clicked.connect(self._on_add)

        self.badge_count = Badge("共 0 个账号", "idle")
        card.add_header(self.btn_add)
        card.add(self.badge_count)

        self.list = QListWidget()
        self.list.setObjectName("AccList")
        self.list.setSpacing(2)
        self.list.currentRowChanged.connect(self._on_row_changed)

        self.empty = EmptyState(
            "user", "还没有账号", "点上方「添加」新增一个学习通账号。")

        self.slot = QStackedWidget()
        self.slot.addWidget(self.list)
        self.slot.addWidget(self.empty)
        card.add(self.slot)
        return card

    def _build_detail(self) -> Card:
        card = Card("账号详情", icon="user", elevated=True)
        self.detail = QWidget()
        dl = QVBoxLayout(self.detail)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(10)

        # ---- 备注名 ----
        dl.addWidget(_field("备注名（可留空）"))
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("例如：主号 / 小号")
        self.ed_name.setMinimumHeight(36)
        dl.addWidget(self.ed_name)

        # ---- 手机号 + 密码 ----
        self.ed_user = QLineEdit()
        self.ed_user.setPlaceholderText("学习通手机号 / 账号")
        self.ed_user.setMinimumHeight(36)
        self.ed_pwd = QLineEdit()
        self.ed_pwd.setPlaceholderText("登录密码")
        self.ed_pwd.setEchoMode(QLineEdit.Password)
        self.ed_pwd.setMinimumHeight(36)
        self.ed_pwd.returnPressed.connect(lambda: self._submit(False))

        col = QWidget()
        col.setMaximumWidth(FORM_WIDTH)
        fl = QVBoxLayout(col)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(8)

        fl.addWidget(_field("手机号 / 账号"))
        fl.addWidget(self.ed_user)
        fl.addSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(_field("密码"))
        head.addStretch(1)
        self.cb_show = QCheckBox("显示密码")
        self.cb_show.toggled.connect(
            lambda v: self.ed_pwd.setEchoMode(
                QLineEdit.Normal if v else QLineEdit.Password)
        )
        head.addWidget(self.cb_show)
        fl.addLayout(head)
        fl.addWidget(self.ed_pwd)
        fl.addSpacing(6)

        self.cb_cookie = QCheckBox("使用 cookies.txt 登录（忽略上面的账号密码）")
        self.cb_cookie.toggled.connect(self._sync_cookie_mode)
        self.cb_remember = QCheckBox("保存账号密码到本机配置文件")
        self.cb_remember.setChecked(True)
        fl.addWidget(self.cb_cookie)
        fl.addWidget(self.cb_remember)

        self.lb_cookie = QLabel("")
        self.lb_cookie.setObjectName("CardHint")
        self.lb_cookie.setWordWrap(True)
        fl.addWidget(self.lb_cookie)

        holder = QHBoxLayout()
        holder.addWidget(col)
        holder.addStretch(1)
        dl.addLayout(holder)

        # ---- 状态 ----
        st = QHBoxLayout()
        st.setSpacing(10)
        st.addWidget(_field("当前状态"))
        self.badge = Badge("未登录", "idle")
        st.addWidget(self.badge)
        st.addStretch(1)
        st.addWidget(_field("登录用户"))
        self.lb_name = QLabel("—")
        self.lb_name.setObjectName("AccSub")
        st.addWidget(self.lb_name)
        dl.addLayout(st)

        self.lb_msg = QLabel("")
        self.lb_msg.setObjectName("CardHint")
        self.lb_msg.setWordWrap(True)
        dl.addWidget(self.lb_msg)

        # ---- 按钮 ----
        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_login = QPushButton("登录")
        self.btn_login.setProperty("variant", "primary")
        self.btn_login.setMinimumHeight(38)
        self.btn_login.setMinimumWidth(110)
        self.btn_login.clicked.connect(lambda: self._submit(False))

        self.btn_login_courses = QPushButton("登录并读取课程")
        self.btn_login_courses.setProperty("variant", "soft")
        self.btn_login_courses.setMinimumHeight(38)
        self.btn_login_courses.clicked.connect(lambda: self._submit(True))

        self.btn_logout = QPushButton("退出登录")
        self.btn_logout.setMinimumHeight(38)
        self.btn_logout.clicked.connect(self._on_logout)

        self.btn_delete = QPushButton("删除账号")
        self.btn_delete.setProperty("variant", "danger")
        self.btn_delete.setMinimumHeight(38)
        self.btn_delete.clicked.connect(self._on_delete)

        row.addWidget(self.btn_login)
        row.addWidget(self.btn_login_courses)
        row.addStretch(1)
        dl.addLayout(row)

        # 危险动作另起一行、右对齐并加一条分隔线：
        # 之前「登录 / 登录并读取课程 / 退出登录 / 删除账号」挤在同一行里横跨整卡，
        # 红色"删除账号"就贴在蓝色"登录"右边，主次完全看不出来。
        dl.addWidget(Divider())
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        row2.addStretch(1)
        row2.addWidget(self.btn_logout)
        row2.addWidget(self.btn_delete)
        dl.addLayout(row2)

        # 多余的纵向空间统一垫在最下面：dl 里全是子布局时，Qt 会把富余高度
        # 均分到各子布局之间，"当前状态"和按钮之间就会裂出一道空白。
        dl.addStretch(1)

        self.detail.setEnabled(False)
        card.add(self.detail)
        return card

    # ------------------------------------------------------------------ 列表
    def reload(self):
        """按 accounts.json 重建账号列表。"""
        keep = self.current_id()
        self.list.blockSignals(True)
        self.list.clear()
        self._rows = {}
        for acc in self.book.accounts:
            acc_id = str(acc.get("id") or "")
            display = self.book.display_name(acc)
            row = AccountRow(acc, display, self._states.get(acc_id, "idle"))
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 56))
            item.setData(Qt.UserRole, acc_id)
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            self._rows[acc_id] = row
        self.list.blockSignals(False)

        n = self.list.count()
        self.badge_count.set_kind("info" if n else "idle", f"共 {n} 个账号")
        self.slot.setCurrentIndex(0 if n else 1)

        if n:
            idx = 0
            for i in range(n):
                if self.list.item(i).data(Qt.UserRole) == keep:
                    idx = i
                    break
            self.list.setCurrentRow(idx)
        else:
            self.detail.setEnabled(False)
        self.sig_accounts_changed.emit()

    def current_id(self) -> str:
        it = self.list.currentItem()
        return str(it.data(Qt.UserRole)) if it else ""

    def _on_row_changed(self, row: int):
        self._load_detail()
        acc_id = self.current_id()
        if acc_id:
            self.book.active = acc_id
            self.book.save()
            self.sig_current_changed.emit(acc_id)

    def update_accounts(self):
        """外部改了账号（如登录后拿到昵称）——只刷新列表文案，不重建控件。"""
        for acc_id, row in self._rows.items():
            acc = self.book.get(acc_id)
            if acc:
                row.set_name(self.book.display_name(acc))

    # ------------------------------------------------------------------ 详情
    def _load_detail(self):
        acc_id = self.current_id()
        acc = self.book.get(acc_id) if acc_id else None
        if acc is None:
            self.detail.setEnabled(False)
            return
        self.detail.setEnabled(True)
        self.ed_name.setText(str(acc.get("name") or ""))
        self.ed_user.setText(str(acc.get("username") or ""))
        self.ed_pwd.setText(str(acc.get("password") or ""))
        self.cb_cookie.setChecked(bool(acc.get("use_cookies")))
        self._sync_cookie_mode(self.cb_cookie.isChecked())

        path = profiles.cookie_path(acc_id)
        exists = "已存在" if os.path.exists(path) else "不存在"
        self.lb_cookie.setText(
            f"cookies 文件：{os.path.basename(path)}（{exists}）")

        self._set_badge(acc_id)
        self.lb_name.setText(str(acc.get("realname") or "—"))
        self.lb_msg.setText("")
        self.set_busy(acc_id in self._busy, acc_id)

    def _sync_cookie_mode(self, on: bool):
        self.ed_user.setEnabled(not on)
        self.ed_pwd.setEnabled(not on)

    def _set_badge(self, acc_id: str):
        text, kind = STATE_TEXT.get(self._states.get(acc_id, "idle"),
                                    STATE_TEXT["idle"])
        self.badge.set_kind(kind, text)
        self._refresh_actions(acc_id)

    def _refresh_actions(self, acc_id: str = ""):
        """「退出登录」只在已登录且不在登录中时可用；删除账号不受登录态限制。"""
        acc_id = acc_id or self.current_id()
        logged = self._states.get(acc_id, "") == "logged_in"
        busy = acc_id in self._busy
        self.btn_logout.setEnabled(logged and not busy)

    # ------------------------------------------------------------------ 动作
    def _on_add(self):
        acc = self.book.add(name=f"账号 {len(self.book.accounts) + 1}")
        self.book.active = acc["id"]
        self.book.save()
        self.reload()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == acc["id"]:
                self.list.setCurrentRow(i)
                break
        self.ed_name.setFocus()
        self.ed_name.selectAll()

    def _collect(self) -> dict:
        acc_id = self.current_id()
        return {
            "id": acc_id,
            "name": self.ed_name.text().strip(),
            "username": self.ed_user.text().strip(),
            "password": self.ed_pwd.text(),
            "use_cookies": self.cb_cookie.isChecked(),
            "cookie_path": profiles.cookie_path(acc_id),
        }

    def _submit(self, also_courses: bool = False):
        acc_id = self.current_id()
        if not acc_id:
            return
        payload = self._collect()
        if not payload["use_cookies"] and (not payload["username"]
                                           or not payload["password"]):
            self.set_error(acc_id, "请填写手机号和密码，或改用 cookies 登录")
            return
        # 先把表单里的内容落盘，登录成功后即使程序崩溃也不丢
        self.book.update(acc_id, name=payload["name"], username=payload["username"],
                         password=payload["password"],
                         use_cookies=payload["use_cookies"])
        self.set_busy(True, acc_id)
        # 列表行也要显示「登录中」：此前只置了 _busy，行徽标还停在旧状态，
        # 多账号一起登录时根本看不出哪个在登（v1.5.4）
        self._states[acc_id] = "logging"
        row = self._rows.get(acc_id)
        if row:
            row.set_state("logging")
        self.badge.set_kind("info", "登录中…")
        self.lb_msg.setText("")
        self._pending_courses = also_courses
        self.sig_request_login.emit(acc_id, payload, also_courses)

    def _on_logout(self):
        acc_id = self.current_id()
        if acc_id:
            self.sig_request_logout.emit(acc_id)

    def _on_delete(self):
        from PySide6.QtWidgets import QMessageBox
        acc_id = self.current_id()
        acc = self.book.get(acc_id)
        if acc is None:
            return
        name = self.book.display_name(acc)
        r = QMessageBox.question(
            self, "删除账号",
            f"确定删除账号「{name}」吗？\n\n它的 cookies 文件也会一并删除，"
            "下次使用需要重新登录。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if r != QMessageBox.Yes:
            return
        self.book.remove(acc_id)
        self._states.pop(acc_id, None)
        self.reload()

    # ------------------------------------------------------------------ 状态
    def set_busy(self, busy: bool, acc_id: str = ""):
        """切换「登录中」状态；acc_id 为空时作用于当前选中的账号。"""
        acc_id = acc_id or self.current_id()
        if acc_id:
            if busy:
                self._busy.add(acc_id)
            else:
                self._busy.discard(acc_id)
        on = bool(acc_id) and acc_id in self._busy
        self.btn_login.setEnabled(not on)
        self.btn_login_courses.setEnabled(not on)
        self.btn_login.setText("登录中…" if on else "登录")
        self._refresh_actions(acc_id)

    def set_error(self, acc_id: str, msg: str):
        self._states[acc_id] = "error"
        row = self._rows.get(acc_id)
        if row:
            row.set_state("error")
        if acc_id == self.current_id():
            self.badge.set_kind("error", "登录失败")
            self.lb_msg.setText(msg)
        self.set_busy(False, acc_id)

    def on_login_result(self, acc_id: str, ok: bool, msg: str, realname: str):
        self._states[acc_id] = "logged_in" if ok else "error"
        row = self._rows.get(acc_id)
        if row:
            row.set_state(self._states[acc_id])
        self.set_busy(False, acc_id)
        self.update_accounts()
        if acc_id == self.current_id():
            self._load_detail()
            if ok:
                self.badge.set_kind("ok", "已登录")
                self.lb_name.setText(realname or "（未获取到昵称）")
                self.lb_msg.setText(msg or "登录成功")
            else:
                self.badge.set_kind("error", "登录失败")
                self.lb_msg.setText(msg)

    def set_account_state(self, acc_id: str, state: str):
        self._states[acc_id] = state
        row = self._rows.get(acc_id)
        if row:
            row.set_state(state)
        if acc_id == self.current_id():
            self._set_badge(acc_id)

    def wants_courses_after_login(self) -> bool:
        return bool(self._pending_courses)

    def clear_pending(self):
        self._pending_courses = False
