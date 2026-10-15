# -*- coding: utf-8 -*-
"""智慧树（知到）页：多账号 + 账密登录 + 并行刷课。

与其他平台页的关系
------------------
结构上刻意与「U校园」「雨课堂」对称（Hero 品牌条 / 账号条 / 账号卡 / 控制卡 /
课程卡 / 进度 / 日志），但**完全独立**：账号存 zhihuishu_accounts.json，
引擎每账号一个 runner，与学习通 / U校园 / 雨课堂的登录态互不影响。

登录方式
--------
智慧树是**账密登录**（passport.zhihuishu.com），不需要扫码、不需要浏览器。
遇到"验证码 / 短信 / 设备二次认证"时，引擎会把原因翻译成人话提示，
并保留 cookie 兜底入口（浏览器 F12 复制 Cookie 整段）。

多账号
------
* 顶部 AccountBar 切账号，下面所有内容（登录态 / 课程 / 进度）跟着切；
* 「全部开始」对所有已登录且已勾选课程的账号并行 start()；
* 进度区多账号时每账号一行摘要，当前账号的视频进度用 SmoothBar；
* 日志行首带 [账号名] 前缀，多账号并行时能分辨来源。

线程安全
--------
信号不额外带 acc_id（保持引擎信号签名），页面槽用 self.sender() 反查来源 ——
lambda 连接在跨线程时可能被当成直连，绑方法连接才会走队列投递到 GUI 线程。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from gui_app import theme as T
from gui_app.widgets import AccountBar, Badge, Card, EmptyState, SmoothBar
from gui_app.zhihuishu_engine import (
    DEFAULT_SPEED, account_store, drop_runner, get_runner, masked_cookie,
)

_PHASE_TEXT = {
    "idle": ("未登录", "idle"),
    "logging_in": ("登录中", "info"),
    "ready": ("已登录", "ok"),
    "loading": ("读取课程中", "info"),
    "loaded": ("课程已就绪", "ok"),
    "running": ("刷课中", "info"),
}

_HINT = (
    "智慧树使用**账号密码**登录（passport.zhihuishu.com），不需要扫码。"
    "若提示需要验证码 / 短信 / 设备认证，请先在浏览器登录一次智慧树，"
    "或用页面下方的「cookie 登录」兜底。学习按视频心跳上报，"
    "「共享课（知到）」与「校内学分课」会自动一起读取。")

#: 运行中（要禁用配置类控件）的 phase
_BUSY_PHASES = ("running", "loading", "logging_in")


class ZhihuishuPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = account_store()
        self._runners: dict = {}          # acc_id -> ZhihuishuRunner
        self._connected: set = set()      # 已连过信号的 acc_id
        self._phases: dict = {}           # acc_id -> phase
        self._steps: dict = {}            # acc_id -> 最近一条登录步骤文案
        self._progress: dict = {}         # acc_id -> 最近一帧进度
        self._courses: dict = {}          # acc_id -> 课程列表
        self._selected_cache: dict = {}   # acc_id -> 已勾选的课程
        self._boxes: list = []            # 当前账号显示中的课程复选框
        self._row_labels: dict = {}       # acc_id -> 进度摘要行
        self._current = str(self._store.active or "")
        self._phase = "idle"

        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        root.addWidget(self._build_hero())
        root.addWidget(self._build_accounts())
        root.addWidget(self._build_login())
        root.addWidget(self._build_control())
        root.addWidget(self._build_courses())
        root.addWidget(self._build_progress())
        root.addWidget(self._build_log())
        root.addStretch(1)

        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.NoFrame)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroller.setWidget(inner)

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(scroller)

        # ---- 依赖其它控件的初始化放 __init__ 末尾 ----
        self.runner = self._runner_for(self._current)
        self._refresh_accounts()
        self._fill_account()
        self.set_state(self._phases.get(self._current, "idle"))

    # ------------------------------------------------------------------ 构建
    def _build_hero(self) -> QFrame:
        hero = QFrame()
        hero.setObjectName("ZhihuishuHero")
        lay = QHBoxLayout(hero)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(14)

        mark = QLabel()
        mark.setObjectName("ZhihuishuHeroMark")
        mark.setFixedSize(46, 46)
        mark.setAlignment(Qt.AlignCenter)
        mark.setPixmap(_white_mark(28))
        lay.addWidget(mark, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(0)
        title = QLabel("智慧树 · 知到")
        title.setObjectName("ZhihuishuHeroTitle")
        col.addWidget(title)
        lay.addLayout(col, 1)

        self.badge = Badge(*_PHASE_TEXT["idle"])
        lay.addWidget(self.badge)

        self.btn_login = QPushButton("登录")
        self.btn_login.setProperty("variant", "zhihuishu")
        self.btn_login.setMinimumHeight(38)
        self.btn_login.setMinimumWidth(96)
        self.btn_login.clicked.connect(self._on_login)
        lay.addWidget(self.btn_login, 0, Qt.AlignVCenter)

        self.btn_start_all = QPushButton("全部开始")
        self.btn_start_all.setProperty("variant", "zhihuishu")
        self.btn_start_all.setMinimumHeight(38)
        self.btn_start_all.setMinimumWidth(104)
        self.btn_start_all.clicked.connect(self._on_start_all)
        lay.addWidget(self.btn_start_all, 0, Qt.AlignVCenter)

        self.btn_stop_all = QPushButton("全部停止")
        self.btn_stop_all.setProperty("variant", "danger")
        self.btn_stop_all.setMinimumHeight(38)
        self.btn_stop_all.setEnabled(False)
        self.btn_stop_all.clicked.connect(self._on_stop_all)
        lay.addWidget(self.btn_stop_all, 0, Qt.AlignVCenter)
        return hero

    def _build_accounts(self) -> AccountBar:
        self.account_bar = AccountBar()
        self.account_bar.setProperty("brand", "zhihuishu")
        self.account_bar.style().unpolish(self.account_bar)
        self.account_bar.style().polish(self.account_bar)
        self.account_bar.sig_switch.connect(self._on_switch)
        self.account_bar.sig_add.connect(self._on_add)
        self.account_bar.sig_remove.connect(self._on_remove)
        return self.account_bar

    def _build_login(self) -> Card:
        card = Card("账号与登录", _HINT, icon="key",
                    accent=T.ZHIHUISHU, accent_soft=T.ZHIHUISHU_SOFT,
                    margins=(20, 16, 20, 18))

        row = QHBoxLayout()
        row.setSpacing(10)
        lab_u = QLabel("账号")
        lab_u.setObjectName("FieldLabel")
        row.addWidget(lab_u)
        self.ed_user = QLineEdit()
        self.ed_user.setPlaceholderText("手机号 / 学号")
        self.ed_user.setMinimumHeight(34)
        self.ed_user.setMinimumWidth(180)
        row.addWidget(self.ed_user)

        lab_p = QLabel("密码")
        lab_p.setObjectName("FieldLabel")
        row.addWidget(lab_p)
        self.ed_pwd = QLineEdit()
        self.ed_pwd.setPlaceholderText("智慧树登录密码")
        self.ed_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_pwd.setMinimumHeight(34)
        self.ed_pwd.setMinimumWidth(180)
        self.ed_pwd.returnPressed.connect(self._on_login)
        row.addWidget(self.ed_pwd)

        self.btn_save = QPushButton("保存")
        self.btn_save.setProperty("variant", "ghost")
        self.btn_save.setMinimumHeight(34)
        self.btn_save.setToolTip("把账号密码保存到当前账号档案（保存在本机程序目录）")
        self.btn_save.clicked.connect(self._on_save_account)
        row.addWidget(self.btn_save)

        row.addStretch(1)
        self.btn_logout = QPushButton("退出登录")
        self.btn_logout.setProperty("variant", "ghost")
        self.btn_logout.setMinimumHeight(34)
        self.btn_logout.clicked.connect(self._on_logout)
        row.addWidget(self.btn_logout)
        card.add(row)

        self.lb_account = QLabel("尚未登录")
        self.lb_account.setObjectName("CardHint")
        self.lb_account.setWordWrap(True)
        self.lb_account.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.add(self.lb_account)

        # cookie 兜底入口：默认折叠
        self.btn_cookie = QPushButton("用 cookie 登录")
        self.btn_cookie.setProperty("variant", "ghost")
        self.btn_cookie.setMinimumHeight(32)
        self.btn_cookie.setToolTip("在浏览器登录智慧树后，F12 → Network 复制请求头里的 "
                                   "Cookie 整段（形如 k=v; k2=v2）")
        self.btn_cookie.clicked.connect(self._toggle_cookie)

        self.cookie_box = QFrame()
        self.cookie_box.setObjectName("ZhihuishuBox")
        crow = QHBoxLayout(self.cookie_box)
        crow.setContentsMargins(12, 10, 12, 10)
        crow.setSpacing(8)
        self.ed_cookie = QLineEdit()
        self.ed_cookie.setPlaceholderText("k=v; k2=v2（浏览器 Cookie 整段）")
        self.ed_cookie.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_cookie.setMinimumHeight(34)
        crow.addWidget(self.ed_cookie, 1)
        self.btn_cookie_ok = QPushButton("确认登录")
        self.btn_cookie_ok.setProperty("variant", "zhihuishu")
        self.btn_cookie_ok.setMinimumHeight(34)
        self.btn_cookie_ok.clicked.connect(self._on_cookie_login)
        crow.addWidget(self.btn_cookie_ok)
        self.cookie_box.setVisible(False)

        cookie_row = QHBoxLayout()
        cookie_row.setSpacing(10)
        cookie_row.addWidget(self.btn_cookie)
        cookie_row.addStretch(1)
        card.add(cookie_row)
        card.add(self.cookie_box)

        self.lb_step = QLabel("登录步骤：未开始")
        self.lb_step.setObjectName("CardHint")
        self.lb_step.setWordWrap(True)
        card.add(self.lb_step)
        return card

    def _build_control(self) -> Card:
        card = Card(margins=(20, 14, 20, 14))
        row = QHBoxLayout()
        row.setSpacing(10)

        self.btn_load = QPushButton("读取课程")
        self.btn_load.setMinimumHeight(38)
        self.btn_load.clicked.connect(self._on_load)

        self.btn_start = QPushButton("开始刷课")
        self.btn_start.setProperty("variant", "zhihuishu")
        self.btn_start.setMinimumHeight(38)
        self.btn_start.setMinimumWidth(130)
        self.btn_start.clicked.connect(self._on_start)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setProperty("variant", "danger")
        self.btn_stop.setMinimumHeight(38)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)

        row.addWidget(self.btn_load)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        card.add(row)
        return card

    def _build_courses(self) -> Card:
        card = Card(
            "课程列表",
            "共享课（知到）与校内学分课一起列出，左侧标注来源；每个账号各读各的。",
            icon="book", accent=T.ZHIHUISHU, accent_soft=T.ZHIHUISHU_SOFT,
            margins=(20, 16, 20, 18))

        bar = QHBoxLayout()
        bar.setSpacing(9)
        self.btn_all = QPushButton("全选")
        self.btn_all.setProperty("variant", "ghost")
        self.btn_all.clicked.connect(lambda: self._select_all(True))
        self.btn_none = QPushButton("全不选")
        self.btn_none.setProperty("variant", "ghost")
        self.btn_none.clicked.connect(lambda: self._select_all(False))
        bar.addWidget(self.btn_all)
        bar.addWidget(self.btn_none)
        bar.addStretch(1)
        self.lb_count = QLabel("")
        self.lb_count.setObjectName("CardHint")
        bar.addWidget(self.lb_count)
        card.add(bar)

        self.empty = EmptyState(
            "inbox", "还没有课程数据",
            "填好账号密码并登录后，点上方「读取课程」。")
        self.empty.set_visible(False)
        card.add(self.empty)

        self.courses_box = QVBoxLayout()
        self.courses_box.setSpacing(6)
        card.add(self.courses_box)
        return card

    def _build_progress(self) -> Card:
        card = Card(
            "刷课进度", icon="chart", accent=T.ZHIHUISHU,
            accent_soft=T.ZHIHUISHU_SOFT, margins=(20, 16, 20, 18))

        self.summary_box = QFrame()
        self.summary_box.setObjectName("ZhihuishuBox")
        rows = QVBoxLayout(self.summary_box)
        rows.setContentsMargins(12, 10, 12, 10)
        rows.setSpacing(4)
        self._rows_lay = rows
        self.summary_box.setVisible(False)
        card.add(self.summary_box)

        self.lb_now = QLabel("等待开始…")
        self.lb_now.setObjectName("SlotTitle")
        self.lb_now.setWordWrap(True)
        card.add(self.lb_now)

        self.bar = SmoothBar(height=8, radius=6)
        self.bar.setRange(0.0, 1.0)
        self.bar.setValue(0)
        card.add(self.bar)

        self.lb_stats = QLabel("尚未开始")
        self.lb_stats.setObjectName("CardHint")
        self.lb_stats.setWordWrap(True)
        card.add(self.lb_stats)
        return card

    def _build_log(self) -> Card:
        card = Card("智慧树日志", "行首带 [账号名]，多账号并行时能分辨来源。",
                    icon="clipboard", accent=T.ZHIHUISHU,
                    accent_soft=T.ZHIHUISHU_SOFT, margins=(20, 14, 20, 16))
        self.log = QPlainTextEdit()
        self.log.setObjectName("LogView")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log.setPlaceholderText("智慧树的处理日志会实时显示在这里…")
        self.log.setMinimumHeight(180)
        card.add(self.log)
        return card

    # ------------------------------------------------------------------ 账号路由
    def _sender_acc_id(self, fallback: str = "") -> str:
        """槽函数里反查是哪个 runner 发来的信号（绑方法连接 + sender()）。"""
        try:
            sender = self.sender()
        except Exception:
            sender = None
        acc = getattr(sender, "acc_id", None)
        if acc:
            return str(acc)
        return str(fallback or self._current)

    def _runner_for(self, acc_id: str):
        acc_id = str(acc_id or "")
        runner = get_runner(acc_id)
        self._runners[acc_id] = runner
        if acc_id not in self._connected:
            self._connected.add(acc_id)
            runner.sig_log.connect(self.append_log)
            runner.sig_state.connect(self.set_state)
            runner.sig_account.connect(self._on_account)
            runner.sig_courses.connect(self.fill_courses)
            runner.sig_progress.connect(self.on_progress)
            runner.sig_done.connect(self._on_done)
            runner.sig_login_step.connect(self._on_login_step)
        return runner

    def _running_ids(self) -> set:
        return {str(a) for a, p in self._phases.items() if p == "running"}

    def _refresh_running(self):
        try:
            self.account_bar.set_running(self._running_ids())
        except Exception:
            pass
        self._refresh_start_all()

    def _refresh_accounts(self):
        pairs = self._store.pairs()
        ids = [a for a, _n in pairs]
        if self._current not in ids:
            self._current = str(self._store.active or (ids[0] if ids else ""))
        self.account_bar.set_accounts(pairs, self._current, self._running_ids())
        self._refresh_progress_rows()

    def _on_switch(self, acc_id: str):
        acc_id = str(acc_id or "")
        if not acc_id or acc_id == self._current:
            return
        self._current = acc_id
        self._store.set_active(acc_id)
        self.runner = self._runner_for(acc_id)
        self._fill_account()
        self._rebuild_courses()
        self._refresh_accounts()
        self.set_state(self._phases.get(acc_id, "idle"))

    def _on_add(self):
        acc = self._store.add()
        new_id = str(acc.get("id") or "")
        self._on_switch(new_id)
        self._refresh_accounts()
        self.set_state(self._phases.get(self._current, "idle"))
        self.append_log("SUCCESS", "已新增账号：" + self._store.display_name(new_id))

    def _on_remove(self, acc_id: str):
        acc_id = str(acc_id or "")
        if not acc_id or not self._store.get(acc_id):
            return
        if len(self._store.accounts) <= 1:
            self.append_log("WARNING", "至少保留一个账号，无法删除")
            return
        name = self._store.display_name(acc_id)
        answer = QMessageBox.question(
            self, "删除账号",
            "确定删除账号「" + name + "」？\n\n"
            "该账号的智慧树登录态与课程勾选会一并清除，正在运行的任务会被停止。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        runner = self._runners.get(acc_id)
        if runner is not None:
            try:
                runner.shutdown()
            except Exception:
                pass
        drop_runner(acc_id)
        self._runners.pop(acc_id, None)
        self._connected.discard(acc_id)
        for book in (self._phases, self._steps, self._progress,
                     self._courses, self._selected_cache):
            book.pop(acc_id, None)
        self._store.remove(acc_id)
        if acc_id == self._current:
            self._current = str(self._store.active or "")
            self.runner = self._runner_for(self._current)
            self._fill_account()
            self._rebuild_courses()
        self._refresh_accounts()
        self.set_state(self._phases.get(self._current, "idle"))
        self.append_log("SUCCESS", "已删除账号：" + name)

    # ------------------------------------------------------------------ 账号信息
    def _fill_account(self):
        info = self.runner.account_info()
        if info.get("logged_in"):
            who = info.get("realname") or info.get("username") or "已登录"
            self.lb_account.setText(
                "登录状态：已登录（" + str(who) + "）\n"
                "账号：" + str(info.get("username") or "—")
                + "　·　倍速：" + str(info.get("speed") or DEFAULT_SPEED) + "x\n"
                "cookie：" + str(info.get("cookie") or "—"))
        else:
            self.lb_account.setText(
                "登录状态：尚未登录。填写账号密码后点右上角「登录」，"
                "登录态只保存在该账号档案里。")
        self.ed_pwd.clear()
        self.lb_step.setText("登录步骤：" + (self._steps.get(self._current) or "未开始"))
        self._sync_buttons()

    def _on_account(self, _info: dict):
        acc_id = self._sender_acc_id()
        if acc_id == self._current:
            self._fill_account()
        self._refresh_start_all()

    def _on_save_account(self):
        acc_id = self._current
        user = self.ed_user.text().strip()
        pwd = self.ed_pwd.text()
        try:
            self._store.update(acc_id, username=user, password=pwd)
        except Exception as exc:
            self.append_log("ERROR", "保存账号失败：" + str(exc), acc_id)
            return
        self.runner.reload_account()
        self._fill_account()
        self.ed_user.setText(user)
        self.append_log("INFO", "账号已保存：" + (user or "（空）"), acc_id)

    def _on_logout(self):
        self.runner.logout()
        self._fill_account()
        self._refresh_start_all()

    # ------------------------------------------------------------------ 登录
    def _on_login(self):
        acc_id = self._current
        user = self.ed_user.text().strip()
        pwd = self.ed_pwd.text()
        if not user or not pwd:
            self.append_log("WARNING", "请先填写账号和密码", acc_id)
            return
        try:
            self._store.update(acc_id, username=user, password=pwd)
        except Exception:
            pass
        self.runner.reload_account()
        self.lb_step.setText("登录步骤：准备中…")
        self.runner.login(user, pwd)

    def _toggle_cookie(self):
        show = not self.cookie_box.isVisible()
        self.cookie_box.setVisible(show)
        self.btn_cookie.setText("收起 cookie 登录" if show else "用 cookie 登录")
        if show:
            self.ed_cookie.setFocus()

    def _on_cookie_login(self):
        acc_id = self._current
        cookie = self.ed_cookie.text().strip()
        if not cookie:
            self.append_log("WARNING", "请先粘贴 Cookie（形如 k=v; k2=v2）", acc_id)
            return
        self.append_log("INFO", "使用 cookie 登录：" + masked_cookie(cookie), acc_id)
        self.runner.login_with_cookie(cookie)

    def _on_login_step(self, acc_id: str, text: str):
        acc_id = str(acc_id or "")
        self._steps[acc_id] = str(text)
        if acc_id == self._current:
            self.lb_step.setText("登录步骤：" + str(text))

    # ------------------------------------------------------------------ 课程
    def fill_courses(self, courses: list, acc_id: str = ""):
        acc_id = self._sender_acc_id(acc_id)
        items = [c for c in (courses or []) if isinstance(c, dict)]
        self._courses[acc_id] = items
        self._selected_cache[acc_id] = list(items)
        if acc_id == self._current:
            self._rebuild_courses()
        self._refresh_start_all()

    def _rebuild_courses(self):
        while self.courses_box.count():
            item = self.courses_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._boxes = []
        courses = self._courses.get(self._current) or []
        for index, course in enumerate(courses):
            name = str(course.get("title") or course.get("course_id") or "未命名课程")
            tag = str(course.get("subtitle") or "")
            text = str(index + 1) + ". " + name + ("　[" + tag + "]" if tag else "")
            cb = QCheckBox(text)
            cb.setChecked(True)
            cb.toggled.connect(self._update_count)
            self.courses_box.addWidget(cb)
            self._boxes.append(cb)
        self.empty.set_visible(not courses)
        self._update_count()
        busy = self._phases.get(self._current, "idle") in _BUSY_PHASES
        for cb in self._boxes:
            cb.setEnabled(not busy)

    def _select_all(self, on: bool):
        for box in self._boxes:
            box.setChecked(bool(on))

    def _selected(self, acc_id: str = "") -> list:
        acc_id = str(acc_id or self._current)
        if acc_id == self._current and self._boxes:
            courses = self._courses.get(acc_id) or []
            return [c for c, b in zip(courses, self._boxes) if b.isChecked()]
        return list(self._selected_cache.get(acc_id) or [])

    def _update_count(self, *_):
        acc_id = self._current
        courses = self._courses.get(acc_id) or []
        selected = [c for c, b in zip(courses, self._boxes) if b.isChecked()]
        self._selected_cache[acc_id] = selected
        self.lb_count.setText("已选 " + str(len(selected)) + " / "
                              + str(len(courses)) + " 门")
        self._refresh_start_all()
        self._sync_buttons()

    # ------------------------------------------------------------------ 刷课
    def _on_load(self):
        self.runner.load_courses()

    def _on_start(self):
        acc_id = self._current
        selected = self._selected(acc_id)
        if not selected:
            self.append_log("WARNING", "请先读取课程并勾选要处理的课程", acc_id)
            return
        self._launch(acc_id, selected)

    def _launch(self, acc_id: str, selected: list) -> bool:
        runner = self._runner_for(acc_id)
        if not runner.busy():
            runner.reload_account()
        if not runner.logged_in():
            self.append_log("WARNING", "账号未登录，已跳过："
                            + self._store.display_name(acc_id), acc_id)
            return False
        if acc_id == self._current:
            self.bar.setRange(0.0, 1.0)
            self.bar.setValue(0)
            self.lb_stats.setText("已选 " + str(len(selected)) + " 门课程，正在开始…")
        runner.start(selected, {})
        return True

    def _on_start_all(self):
        started = 0
        skipped = []
        for acc_id, name in self._store.pairs():
            if self._phases.get(acc_id) == "running":
                skipped.append(name + "（正在刷课）")
                continue
            runner = self._runner_for(acc_id)
            if not runner.logged_in():
                skipped.append(name + "（未登录）")
                continue
            selected = self._selected(acc_id)
            if not selected:
                skipped.append(name + "（未读取课程或未勾选）")
                continue
            if self._launch(acc_id, selected):
                started += 1
        if started:
            self.append_log("INFO", "已并行启动 " + str(started) + " 个账号的刷课")
        else:
            self.append_log("WARNING",
                            "没有可启动的账号：请先为账号登录、读取课程并勾选")
        if skipped:
            self.append_log("INFO", "已跳过：" + "；".join(skipped))

    def _on_stop(self):
        self.runner.stop()

    def _on_stop_all(self):
        count = 0
        for acc_id, runner in list(self._runners.items()):
            if self._phases.get(acc_id) == "running":
                runner.stop()
                count += 1
        self.append_log("INFO", "已请求停止 " + str(count) + " 个账号的刷课")
        self._refresh_start_all()

    def on_progress(self, info: dict):
        acc_id = self._sender_acc_id()
        if not isinstance(info, dict):
            return
        self._progress[acc_id] = dict(info)
        self._refresh_row_texts()
        if acc_id != self._current:
            return
        phase = str(info.get("phase") or "")
        course = str(info.get("course") or "")
        chapter = str(info.get("chapter") or "")
        video = str(info.get("video") or "")
        rate = float(info.get("rate") or 0.0)
        ci = int(info.get("course_index") or 0)
        ct = int(info.get("course_total") or 0)
        head = ("第 " + str(ci) + "/" + str(ct) + " 门　·　") if ct else ""
        tail = "　·　".join(x for x in (chapter, video) if x)
        if course or tail:
            self.lb_now.setText(head + course + ("　·　" + tail if tail else ""))
        if phase == "playing":
            self.bar.setRange(0.0, 1.0)
            self.bar.setValue(max(0.0, min(1.0, rate)))
            self.lb_stats.setText(
                "当前视频进度 " + format(rate * 100, ".1f") + "%"
                + "　·　课程 " + str(int(info.get("courses_done") or 0)) + " 门已完成")
        elif phase == "video":
            self.bar.setValue(0.0)

    def _on_done(self, status: str, message: str):
        acc_id = self._sender_acc_id()
        level = {"ok": "SUCCESS", "stopped": "WARNING"}.get(status, "ERROR")
        self.append_log(level, message, acc_id)
        if status == "error":
            self._alert_error(str(message), acc_id)
        if acc_id == self._current and status == "ok":
            self.bar.setValue(1.0)
        self._refresh_start_all()

    def _alert_error(self, message: str, acc_id: str = ""):
        """失败要显性：把原因原文弹给用户，不吞。"""
        name = self._store.display_name(acc_id) if acc_id else ""
        title = "智慧树" + (" · " + name if name else "")
        try:
            QMessageBox.warning(self, title, str(message))
        except Exception:
            pass

    # ------------------------------------------------------------------ 进度摘要
    def _refresh_progress_rows(self):
        while self._rows_lay.count():
            item = self._rows_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._row_labels = {}
        pairs = self._store.pairs()
        for acc_id, _name in pairs:
            label = QLabel()
            label.setObjectName("CardHint")
            label.setWordWrap(True)
            self._rows_lay.addWidget(label)
            self._row_labels[str(acc_id)] = label
        self.summary_box.setVisible(len(pairs) > 1)
        self._refresh_row_texts()

    def _refresh_row_texts(self):
        for acc_id, name in self._store.pairs():
            label = self._row_labels.get(str(acc_id))
            if label is None:
                continue
            data = self._progress.get(str(acc_id)) or {}
            if not data:
                label.setText("[" + name + "] 等待开始")
                continue
            ci = int(data.get("course_index") or 0)
            ct = int(data.get("course_total") or 0)
            head = ("第 " + str(ci) + "/" + str(ct) + " 门") if ct else "等待开始"
            parts = [head, str(data.get("course") or "—")]
            if data.get("video"):
                parts.append(str(data["video"]))
            parts.append(format(float(data.get("rate") or 0.0) * 100, ".1f") + "%")
            label.setText("[" + name + "] " + " · ".join(parts))

    # ------------------------------------------------------------------ 状态
    def append_log(self, level: str, text: str, acc_id: str = ""):
        level = str(level or "INFO").upper()
        if level == "DEBUG":
            return
        acc_id = self._sender_acc_id(acc_id)
        name = self._store.display_name(acc_id) if acc_id else "系统"
        self.log.appendPlainText("[" + name + "] [" + level + "] "
                                 + str(text).rstrip())

    def set_state(self, phase: str, acc_id: str = ""):
        """按 phase 统一切换控件可用态（每个账号各记一份，切换账号时重放）。"""
        acc_id = self._sender_acc_id(acc_id)
        phase = str(phase)
        self._phases[acc_id] = phase
        self._refresh_running()
        if acc_id != self._current:
            return
        self._phase = phase
        text, kind = _PHASE_TEXT.get(phase, _PHASE_TEXT["idle"])
        self.badge.set_kind(kind, text)
        self._sync_buttons()

    def _sync_buttons(self):
        phase = self._phases.get(self._current, "idle")
        busy = phase in _BUSY_PHASES
        logged = self.runner.logged_in()
        has_courses = bool(self._courses.get(self._current))
        has_selected = bool(self._selected(self._current))

        self.ed_user.setEnabled(not busy)
        self.ed_pwd.setEnabled(not busy)
        self.btn_save.setEnabled(not busy)
        self.btn_logout.setEnabled(not busy and logged)
        self.btn_logout.setToolTip("" if logged else "当前账号尚未登录")
        self.btn_login.setEnabled(phase != "logging_in")
        self.btn_login.setToolTip("" if phase != "logging_in" else "正在登录，请稍候")
        self.btn_cookie.setEnabled(not busy)
        self.ed_cookie.setEnabled(not busy)
        self.btn_cookie_ok.setEnabled(not busy)
        for box in self._boxes:
            box.setEnabled(not busy)

        self.btn_load.setEnabled(not busy and logged)
        if busy:
            self.btn_load.setToolTip("正在处理中，请稍候")
        elif not logged:
            self.btn_load.setToolTip("请先登录智慧树账号")
        else:
            self.btn_load.setToolTip("读取该账号下的智慧树课程")

        self.btn_stop.setEnabled(phase == "running")
        self.btn_stop.setToolTip("" if phase == "running" else "当前账号没有在刷课")

        can_start = logged and phase in ("ready", "loaded") and has_selected
        self.btn_start.setEnabled(can_start)
        if phase == "running":
            tip = "正在刷课；先「停止」才能再次开始"
        elif phase == "loading":
            tip = "正在读取课程，请稍候"
        elif not logged:
            tip = "请先登录智慧树账号"
        elif not has_courses:
            tip = "请先点「读取课程」"
        elif not has_selected:
            tip = "请至少勾选一门课程"
        else:
            tip = "开始处理勾选的课程（按心跳上报，不需要人工看视频）"
        self.btn_start.setToolTip(tip)

        self.btn_all.setEnabled(not busy)
        self.btn_none.setEnabled(not busy)
        self._refresh_start_all()

    def _any_startable(self) -> bool:
        for acc_id, _name in self._store.pairs():
            if self._phases.get(acc_id) == "running":
                continue
            if not self._runner_for(acc_id).logged_in():
                continue
            if self._selected(acc_id):
                return True
        return False

    def _refresh_start_all(self):
        running = bool(self._running_ids())
        self.btn_stop_all.setEnabled(running)
        self.btn_stop_all.setToolTip("" if running else "当前没有账号在刷课")
        logged_any = any(self._runner_for(a).logged_in() for a, _n in self._store.pairs())
        self.btn_start_all.setEnabled(self._any_startable())
        if not logged_any:
            tip = "请先登录至少一个账号"
        elif not self._any_startable():
            tip = "请先为要刷的账号点「读取课程」并勾选课程"
        else:
            tip = "对所有已登录、已勾选课程的账号并行开始刷课"
        self.btn_start_all.setToolTip(tip)

    # ------------------------------------------------------------------ 收尾
    def shutdown(self):
        for runner in list(self._runners.values()):
            try:
                runner.shutdown()
            except Exception:
                pass


def _white_mark(size: int) -> QPixmap:
    """Hero 上的白色树形标志（复用 icons 的 zhihuishu 形状）。"""
    from gui_app import icons as I
    return I.pixmap("zhihuishu", "#FFFFFF", int(size))
