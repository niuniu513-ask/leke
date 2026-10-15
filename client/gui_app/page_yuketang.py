# -*- coding: utf-8 -*-
"""雨课堂页：多账号 + 扫码/cookie 登录 + 并行刷课。

与「U校园」页的关系
------------------
两页结构上刻意对称（Hero 品牌条 / 账号条 / 账号卡 / 任务卡 / 进度 / 日志），
但**完全独立**：账号存 yuketang_accounts.json，引擎每账号一个 runner，
和学习通、U校园的登录态互不影响。

多账号（v1.5.5）
----------------
* 顶部 AccountBar 切账号，下面所有内容（域名 / 登录态 / 课程 / 进度）跟着切；
* 「全部开始」对所有已登录且已勾选课程的账号并行 start()；
* 进度区多账号时每账号一行摘要，当前账号的视频进度用 SmoothBar；
* 日志行首带 [账号名] 前缀，多账号并行时能分辨来源。

线程安全
--------
信号不额外带 acc_id（保持旧信号签名），页面槽用 self.sender() 反查是哪个
runner 发来的 —— lambda 连接在跨线程时可能被当成直连，绑方法连接才会走
队列投递到 GUI 线程，所以这里一律用绑方法 + sender() 识别来源。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QSizePolicy, QSpinBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from gui_app import theme as T
from gui_app.browser_workspace import BrowserWorkspace
from gui_app.widgets import AccountBar, Badge, Card, EmptyState, SmoothBar
from gui_app.yuketang_engine import (
    DEFAULT_DOMAIN, account_store, drop_runner, get_runner, masked_cookie,
    qrcode_available,
)

_PHASE_TEXT = {
    "idle": ("未登录", "idle"),
    "logging_in": ("等待登录", "info"),
    "ready": ("已登录", "ok"),
    "loading": ("读取课程中", "info"),
    "loaded": ("课程已就绪", "ok"),
    "running": ("刷课中", "info"),
}

_HINT = (
    "点击“扫码识别学校”，在官方网页扫码登录并进入目标学校课程，学校信息会自动保存。"
    "多校账号请在官方页面选择目标学校。也可填写学校网址后使用学校扫码或 cookie 登录。")

#: 运行中（要禁用配置类控件）的 phase
_BUSY_PHASES = ("running", "loading", "logging_in")


class _LiveTask(QThread):
    """雨课堂「课堂互动」的后台任务（网络请求不占 UI 线程）。"""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):  # noqa: D102
        try:
            self.done.emit(self._fn())
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class YuketangPage(QWidget):
    sig_login_changed = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = account_store()
        self._runners: dict = {}          # acc_id -> YuketangRunner
        self._connected: set = set()      # 已经连过信号、不再重复连的 acc_id
        self._phases: dict = {}           # acc_id -> phase
        self._steps: dict = {}            # acc_id -> 最近一条登录步骤文案
        self._progress: dict = {}         # acc_id -> 最近一帧进度
        self._courses: dict = {}          # acc_id -> 课程列表
        self._selected_cache: dict = {}   # acc_id -> 已勾选的课程
        self._boxes: list = []            # 当前账号显示中的课程复选框
        self._row_labels: dict = {}       # acc_id -> 进度摘要行
        self._restoring_task_options = False
        self._current = str(self._store.active or "")
        self._phase = "idle"

        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        root.addWidget(self._build_setup())
        root.addStretch(1)

        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.NoFrame)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroller.setWidget(inner)

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self._build_hero())
        shell.addWidget(self._build_accounts())
        bar = QHBoxLayout()
        bar.addWidget(QLabel('课程浏览器 · 当前账号的独立会话'))
        bar.addStretch()
        self.btn_browser_visible = QPushButton('隐藏浏览器')
        self.btn_browser_visible.clicked.connect(self._toggle_browser_visible)
        bar.addWidget(self.btn_browser_visible)
        self.btn_browser_mode = QPushButton('切换独立窗口')
        self.btn_browser_mode.clicked.connect(self._toggle_browser_mode)
        bar.addWidget(self.btn_browser_mode)
        shell.addLayout(bar)
        self.browser = BrowserWorkspace()
        self.browser.select(self._current)
        self.browser.currentWidget().hint.setText('点击「扫码识别学校」\n在这里登录雨课堂并打开课程。')
        self.workspace = QSplitter(Qt.Horizontal)
        self.workspace.addWidget(self.browser)
        self.inspector = QTabWidget()
        self.inspector.setObjectName('WorkspaceInspector')
        task = QWidget()
        task_layout = QVBoxLayout(task)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.addWidget(self._build_control())
        task_layout.addWidget(self._build_courses())
        task_layout.addWidget(self._build_progress())
        task_layout.addWidget(self._build_live())
        task_layout.addStretch()
        task_scroll = QScrollArea()
        task_scroll.setWidgetResizable(True)
        task_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for label in task.findChildren(QLabel):
            if label.wordWrap():
                label.setMinimumWidth(0)
                label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        task_scroll.setWidget(task)
        self.inspector.addTab(task_scroll, '任务')
        self.inspector.addTab(scroller, '账号与登录')
        self.inspector.addTab(self._build_log(), '日志')
        self.inspector.setCurrentIndex(1)
        self.workspace.addWidget(self.inspector)
        self.workspace.setChildrenCollapsible(False)
        self.inspector.setMinimumWidth(0)
        self.workspace.setSizes([640, 680])
        shell.addWidget(self.workspace, 1)

        # ---- 依赖其它控件的初始化放 __init__ 末尾 ----
        self.runner = self._runner_for(self._current)
        self._refresh_accounts()
        self._fill_account()
        self.set_state(self._phases.get(self._current, "idle"))
        if self.runner.logged_in():
            self.inspector.setCurrentIndex(0)
        self._toggle_browser_visible()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'workspace'):
            direction = Qt.Vertical if self.width() < 1100 else Qt.Horizontal
            if self.workspace.orientation() != direction:
                self.workspace.setOrientation(direction)
                self.workspace.setSizes([360, 640])

    # ------------------------------------------------------------------ 构建
    def _build_hero(self) -> QFrame:
        hero = QFrame()
        hero.setObjectName("YuketangHero")
        lay = QHBoxLayout(hero)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(14)

        mark = QLabel()
        mark.setObjectName("YuketangHeroMark")
        mark.setFixedSize(46, 46)
        mark.setAlignment(Qt.AlignCenter)
        mark.setPixmap(_white_mark(28))
        lay.addWidget(mark, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(0)
        title = QLabel("雨课堂 · 学堂在线")
        title.setObjectName("YuketangHeroTitle")
        col.addWidget(title)
        lay.addLayout(col, 1)

        self.badge = Badge(*_PHASE_TEXT["idle"])
        lay.addWidget(self.badge)

        self.btn_login = QPushButton("扫码识别学校")
        self.btn_login.setProperty("variant", "yuketang")
        self.btn_login.setMinimumHeight(38)
        self.btn_login.setMinimumWidth(110)
        self.btn_login.clicked.connect(self._on_auto_login)
        lay.addWidget(self.btn_login, 0, Qt.AlignVCenter)

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setProperty("variant", "ghost")
        self.btn_cancel.setMinimumHeight(38)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        lay.addWidget(self.btn_cancel, 0, Qt.AlignVCenter)

        self.btn_start_all = QPushButton("全部开始")
        self.btn_start_all.setProperty("variant", "yuketang")
        self.btn_start_all.setMinimumHeight(38)
        self.btn_start_all.setMinimumWidth(0)
        self.btn_start_all.setVisible(False)
        self.btn_start_all.clicked.connect(self._on_start_all)
        lay.addWidget(self.btn_start_all, 0, Qt.AlignVCenter)

        self.btn_stop_all = QPushButton("全部停止")
        self.btn_stop_all.setProperty("variant", "danger")
        self.btn_stop_all.setMinimumHeight(38)
        self.btn_stop_all.setEnabled(False)
        self.btn_stop_all.setVisible(False)
        self.btn_stop_all.clicked.connect(self._on_stop_all)
        lay.addWidget(self.btn_stop_all, 0, Qt.AlignVCenter)
        return hero

    def _build_accounts(self) -> AccountBar:
        self.account_bar = AccountBar()
        # chip 选中色走青绿（QSS 属性选择器不会自动重算，改完要 unpolish+polish）
        self.account_bar.setProperty("brand", "yuketang")
        self.account_bar.style().unpolish(self.account_bar)
        self.account_bar.style().polish(self.account_bar)
        self.account_bar.sig_switch.connect(self._on_switch)
        self.account_bar.sig_add.connect(self._on_add)
        self.account_bar.sig_remove.connect(self._on_remove)
        return self.account_bar

    def _build_setup(self) -> Card:
        card = Card(
            "账号与登录", _HINT, icon="key", accent=T.YUKETANG,
            accent_soft=T.YUKETANG_SOFT, margins=(20, 16, 20, 18))

        row = QHBoxLayout()
        row.setSpacing(10)
        lab = QLabel("学校网址")
        lab.setObjectName("FieldLabel")
        row.addWidget(lab)
        self.ed_domain = QLineEdit(DEFAULT_DOMAIN)
        self.ed_domain.setPlaceholderText("粘贴学校雨课堂登录网址或完整域名")
        self.ed_domain.setMinimumHeight(34)
        self.ed_domain.setMinimumWidth(0)
        row.addWidget(self.ed_domain)
        card.add(row)
        row = QHBoxLayout()
        self.btn_save_domain = QPushButton("保存学校")
        self.btn_save_domain.setProperty("variant", "ghost")
        self.btn_save_domain.setMinimumHeight(34)
        self.btn_save_domain.setToolTip("把域名保存到当前账号档案")
        self.btn_save_domain.clicked.connect(self._on_save_domain)
        row.addWidget(self.btn_save_domain)
        self.btn_school_login = QPushButton("学校扫码登录")
        self.btn_school_login.setProperty('variant', 'ghost')
        self.btn_school_login.clicked.connect(self._on_login)
        row.addWidget(self.btn_school_login)
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

        if not qrcode_available():
            warn = QLabel("⚠ 二维码渲染不可用（缺少 Pillow），扫码登录暂时不可用；"
                          "仍可用 cookie 登录。")
            warn.setObjectName("CardHint")
            warn.setWordWrap(True)
            card.add(warn)

        # cookie 兜底入口：默认折叠
        self.btn_cookie = QPushButton("用 cookie 登录")
        self.btn_cookie.setProperty("variant", "ghost")
        self.btn_cookie.setMinimumHeight(32)
        self.btn_cookie.setToolTip("在浏览器登录雨课堂后，F12 → Network 里复制 "
                                   "csrftoken=…; sessionid=… 整段")
        self.btn_cookie.clicked.connect(self._toggle_cookie)

        self.cookie_box = QFrame()
        self.cookie_box.setObjectName("YuketangQRBox")
        crow = QHBoxLayout(self.cookie_box)
        crow.setContentsMargins(12, 10, 12, 10)
        crow.setSpacing(8)
        self.ed_cookie = QLineEdit()
        self.ed_cookie.setPlaceholderText("csrftoken=…; sessionid=…")
        self.ed_cookie.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_cookie.setMinimumHeight(34)
        crow.addWidget(self.ed_cookie, 1)
        self.btn_cookie_ok = QPushButton("确认登录")
        self.btn_cookie_ok.setProperty("variant", "yuketang")
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

        self.qr_box = QFrame()
        self.qr_box.setObjectName("YuketangQRBox")
        ql = QVBoxLayout(self.qr_box)
        ql.setContentsMargins(14, 12, 14, 13)
        ql.setSpacing(8)
        self.lb_qr = QLabel("二维码会显示在这里")
        self.lb_qr.setAlignment(Qt.AlignCenter)
        self.lb_qr.setMinimumHeight(184)
        self.lb_qr.setObjectName("CardHint")
        ql.addWidget(self.lb_qr)
        self.lb_qr_hint = QLabel("用微信扫一扫 → 确认登录。二维码约 60 秒自动刷新。")
        self.lb_qr_hint.setObjectName("CardHint")
        self.lb_qr_hint.setAlignment(Qt.AlignCenter)
        self.lb_qr_hint.setWordWrap(True)
        ql.addWidget(self.lb_qr_hint)
        self.qr_box.setVisible(False)
        card.add(self.qr_box)

        self.lb_step = QLabel("登录步骤：未开始")
        self.lb_step.setObjectName("CardHint")
        self.lb_step.setWordWrap(True)
        card.add(self.lb_step)
        return card

    def _build_control(self) -> Card:
        card = Card("任务配置", "选择课程后配置任务，再点击开始。", margins=(16, 14, 16, 14))
        row = QHBoxLayout()
        row.setSpacing(10)

        self.btn_load = QPushButton("读取课程")
        self.btn_load.setMinimumHeight(38)
        self.btn_load.clicked.connect(self._on_load)
        self.btn_open_course = QPushButton('打开课程')
        self.btn_open_course.setMinimumHeight(38)
        self.btn_open_course.clicked.connect(self._on_open_course)

        self.btn_start = QPushButton("开始刷课")
        self.btn_start.setProperty("variant", "yuketang")
        self.btn_start.setMinimumHeight(38)
        self.btn_start.setMinimumWidth(0)
        self.btn_start.clicked.connect(self._on_configured_start)

        self.btn_answer = QPushButton("开始答题")
        self.btn_answer.setProperty("variant", "yuketang")
        self.btn_answer.setMinimumHeight(38)
        self.btn_answer.setMinimumWidth(0)
        self.btn_answer.clicked.connect(self._on_answer)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setProperty("variant", "danger")
        self.btn_stop.setMinimumHeight(38)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)

        row.addWidget(self.btn_load)
        row.addWidget(self.btn_open_course)
        row.addStretch(1)
        card.add(row)
        row = QHBoxLayout()
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_answer)
        row.addWidget(self.btn_stop)
        self.btn_next = QPushButton("下一门")
        self.btn_next.clicked.connect(lambda: self.runner.next_course())
        row.addWidget(self.btn_next)
        row.addStretch(1)

        card.add(row)
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        self.cb_task = QComboBox()
        self.cb_task.addItem("视频学习", "study")
        self.cb_task.addItem("作业 / 章节测验", "answer")
        grid.addWidget(QLabel("任务类型"), 0, 0)
        grid.addWidget(self.cb_task, 0, 1)
        self.cb_task.currentIndexChanged.connect(lambda: self._sync_buttons())
        self.sp_jobs = QSpinBox()
        self.sp_jobs.setRange(1, 3)
        self.sp_jobs.setValue(1)
        self.sp_jobs.valueChanged.connect(self._save_task_options)
        self.cb_task.currentIndexChanged.connect(self._save_task_options)
        self.sp_jobs.setSuffix(" 个视频")
        grid.addWidget(QLabel("同时处理"), 1, 0)
        grid.addWidget(self.sp_jobs, 1, 1)
        self.cb_workers = QCheckBox("同时处理多个视频")
        self.cb_workers.hide()  # Compatibility with existing settings and regression checks.
        self.cb_workers.toggled.connect(lambda on: self.sp_jobs.setValue(3 if on else 1))
        card.add(grid)

        hint = QLabel("学校专业版「开始答题」会处理课程作业 / 章节测验：优先用雨课堂接口自带的"
                      "答案，其次查题库（官方聚合 → 免费源 → 本地知识库 → AI 兜底）。")
        hint.setObjectName("CardHint")
        hint.setWordWrap(True)
        self.lb_task_hint = hint
        card.add(hint)
        return card

    def _build_courses(self) -> Card:
        card = Card(
            "课程列表",
            "勾选要处理的课程；完整课程名可换行显示。",
            icon="book", accent=T.YUKETANG, accent_soft=T.YUKETANG_SOFT,
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
            "登录后点击「读取课程」，普通版会读取我学的课与我教的课。")
        self.empty.set_visible(False)
        card.add(self.empty)

        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("搜索课程或班级")
        self.ed_search.textChanged.connect(self._filter_courses)
        card.add(self.ed_search)
        self.courses_box = QVBoxLayout()
        self.courses_box.setSpacing(6)
        card.add(self.courses_box)
        return card

    def _build_progress(self) -> Card:
        card = Card(
            "刷课进度", icon="chart", accent=T.YUKETANG,
            accent_soft=T.YUKETANG_SOFT, margins=(20, 16, 20, 18))

        self.summary_box = QFrame()
        self.summary_box.setObjectName("YuketangQRBox")
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

    def _build_live(self) -> Card:
        """课堂互动：扫码/口令签到 · 课堂答题 · 看 PPT。"""
        card = Card("课堂互动", "扫码 / 口令签到 · 课堂答题 · 看 PPT",
                    icon="clipboard", accent=T.YUKETANG,
                    accent_soft=T.YUKETANG_SOFT)
        self._ykt_live = None
        self._live_tasks: list = []

        self.lb_live = QLabel("点「读取正在上课」拉取当前课堂")
        self.lb_live.setObjectName("CardHint")
        self.lb_live.setWordWrap(True)
        card.add(self.lb_live)

        bar = QHBoxLayout()
        self.btn_live_refresh = QPushButton("读取正在上课")
        self.btn_live_refresh.setProperty("variant", "yuketang")
        self.btn_live_refresh.setMinimumHeight(32)
        self.btn_live_refresh.clicked.connect(self._on_live_refresh)
        bar.addWidget(self.btn_live_refresh)
        bar.addStretch(1)
        card.add(bar)

        self.live_host = QWidget()
        self.live_lay = QVBoxLayout(self.live_host)
        self.live_lay.setContentsMargins(0, 0, 0, 0)
        self.live_lay.setSpacing(6)
        card.add(self.live_host)

        scan = QHBoxLayout()
        self.ed_scan = QLineEdit()
        self.ed_scan.setPlaceholderText("粘贴雨课堂二维码 / 口令链接")
        self.btn_scan = QPushButton("扫码签到")
        self.btn_scan.setProperty("variant", "ghost")
        self.btn_scan.setMinimumHeight(30)
        self.btn_scan.clicked.connect(self._on_scan_sign)
        scan.addWidget(self.ed_scan, 1)
        scan.addWidget(self.btn_scan)
        card.add(scan)

        self.live_out = QPlainTextEdit()
        self.live_out.setReadOnly(True)
        self.live_out.setMaximumBlockCount(400)
        self.live_out.setMinimumHeight(80)
        self.live_out.setPlaceholderText("课堂互动日志…")
        card.add(self.live_out)
        return card

    # ------------------------------------------------------------------ 课堂互动
    def _live_log(self, text: str):
        self.live_out.appendPlainText(text)

    def _live_spawn(self, fn, done):
        task = _LiveTask(fn, self)
        self._live_tasks.append(task)

        def finish(result):
            done(result)
            if task in self._live_tasks:
                self._live_tasks.remove(task)

        task.done.connect(finish)
        task.failed.connect(lambda msg: self._live_log(f"✗ {msg}"))
        task.start()

    def _live_client(self):
        return self.runner._ensure_client()

    def _clear_lessons(self):
        while self.live_lay.count():
            item = self.live_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def _on_live_refresh(self):
        self._live_log("读取正在上课…")
        self.btn_live_refresh.setEnabled(False)

        def work():
            from api.ykt_live import YKTLive
            live = YKTLive(self._live_client())
            return live, live.on_lesson()

        def done(res):
            live, lessons = res
            self._ykt_live = live
            self._clear_lessons()
            if not lessons:
                self.lb_live.setText("当前没有正在进行的课堂")
                self._live_log("✓ 没有正在上课的课堂")
            else:
                self.lb_live.setText(f"正在上课：{len(lessons)} 个")
                for lesson in lessons:
                    self.live_lay.addWidget(self._lesson_row(lesson))
                self._live_log(f"✓ 读到 {len(lessons)} 个课堂")
            self.btn_live_refresh.setEnabled(True)

        self._live_spawn(work, done)

    def _lesson_row(self, lesson: dict) -> QWidget:
        row = QFrame()
        row.setObjectName("CardSub")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)
        title = (lesson.get("courseName") or lesson.get("title")
                 or lesson.get("name") or "雨课堂课堂")
        lesson_id = str(lesson.get("lessonId") or lesson.get("lesson_id") or "")
        col = QVBoxLayout()
        col.setSpacing(2)
        name = QLabel(str(title))
        name.setWordWrap(True)
        col.addWidget(name)
        hint = QLabel(f"lessonId {lesson_id}")
        hint.setObjectName("CardHint")
        col.addWidget(hint)
        lay.addLayout(col, 1)

        btn = QPushButton("签到")
        btn.setProperty("variant", "ghost")
        btn.setMinimumHeight(30)
        btn.clicked.connect(lambda _=False, x=lesson_id: self._on_check_in(x))
        lay.addWidget(btn, 0, Qt.AlignVCenter)

        pres_id = str(lesson.get("presentationId") or lesson.get("presentation_id") or "")
        if pres_id:
            btn_ppt = QPushButton("看PPT")
            btn_ppt.setProperty("variant", "ghost")
            btn_ppt.setMinimumHeight(30)
            btn_ppt.clicked.connect(lambda _=False, x=pres_id: self._on_ppt(x))
            lay.addWidget(btn_ppt, 0, Qt.AlignVCenter)
        return row

    def _on_check_in(self, lesson_id: str):
        def work():
            if self._ykt_live is None:
                raise RuntimeError("请先「读取正在上课」")
            return self._ykt_live.check_in(lesson_id)

        def done(res):
            if res.get("code") == 0:
                self._live_log(f"✓ 签到成功（lesson {lesson_id}）")
            else:
                self._live_log(f"✗ 签到失败：{res.get('error')}")

        self._live_spawn(work, done)

    def _on_scan_sign(self):
        url = self.ed_scan.text().strip()
        if not url:
            QMessageBox.information(self, "提示", "先粘贴二维码 / 口令链接")
            return

        def work():
            from api.ykt_live import YKTLive
            return YKTLive(self._live_client()).scan(url)

        def done(res):
            if res.get("code") == 0:
                self._live_log("✓ 扫码签到成功")
                self.ed_scan.clear()
            else:
                self._live_log(f"✗ 扫码签到失败：{res.get('error')}")

        self._live_spawn(work, done)

    def _on_ppt(self, presentation_id: str):
        def work():
            if self._ykt_live is None:
                raise RuntimeError("请先「读取正在上课」")
            return self._ykt_live.presentation(presentation_id)

        def done(data):
            if not data:
                self._live_log("✗ 没拿到 PPT（可能未签到或接口变更）")
                return
            slides = data.get("slides") or data.get("pages") or []
            self._live_log(f"✓ PPT 标题：{data.get('title', '')}；共 {len(slides)} 页")
            for i, slide in enumerate(slides[:20], 1):
                text = str(slide)
                self._live_log(f"  {i}. {text[:120]}")

        self._live_spawn(work, done)

    def _build_log(self) -> Card:
        card = Card("雨课堂日志", "行首带 [账号名]，多账号并行时能分辨来源。",
                    icon="clipboard", accent=T.YUKETANG,
                    accent_soft=T.YUKETANG_SOFT, margins=(20, 14, 20, 16))
        self.log = QPlainTextEdit()
        self.log.setObjectName("LogView")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log.setPlaceholderText("雨课堂的处理日志会实时显示在这里…")
        self.log.setMinimumHeight(180)
        card.add(self.log)
        return card

    # ------------------------------------------------------------------ 账号路由
    def _sender_acc_id(self, fallback: str = "") -> str:
        """槽函数里反查是哪个 runner 发来的信号。

        信号签名保持旧样子（不带 acc_id），而 lambda 连接在跨线程时可能被当成
        直连，所以统一用绑方法连接 + sender() 识别来源。
        """
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
        self._phases.setdefault(acc_id, runner.phase)
        self._runners[acc_id] = runner
        if acc_id not in self._connected:
            self._connected.add(acc_id)
            runner.sig_log.connect(self.append_log)
            runner.sig_state.connect(self.set_state)
            runner.sig_qrcode.connect(self.show_qrcode)
            runner.sig_account.connect(self._on_account)
            runner.sig_courses.connect(self.fill_courses)
            runner.sig_progress.connect(self.on_progress)
            runner.sig_done.connect(self._on_done)
            runner.sig_login_step.connect(self._on_login_step)
            runner.sig_browser.connect(self.browser.start)
            runner.sig_browser_closed.connect(self.browser.reset)
        return runner

    def _running_ids(self) -> set:
        return {str(a) for a, p in self._phases.items() if p == "running"}

    def _refresh_running(self):
        try:
            self.account_bar.set_running(self._running_ids())
        except Exception:
            pass
        # 运行集合变了，「全部停止」的可用态也要跟着变
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
        self._refresh_start_all()

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
            "该账号的雨课堂登录态与课程勾选会一并清除，正在运行的任务会被停止。",
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
        self._refresh_start_all()
        self.append_log("SUCCESS", "已删除账号：" + name)

    # ------------------------------------------------------------------ 账号信息
    def _fill_account(self):
        info = self.runner.account_info()
        self.browser.select(self._current)
        if info.get("logged_in"):
            who = info.get("realname") or info.get("user_id") or "已登录"
            self.lb_account.setText(
                "登录状态：已登录（" + str(who) + "）\n"
                "域名：" + str(info.get("domain") or "") + "　·　"
                "user_id：" + str(info.get("user_id") or "—") + "　·　"
                "university_id：" + str(info.get("university_id") or "—") + "\n"
                "cookie：" + str(info.get("cookie") or ""))
            if info.get('login_mode') == 'standard':
                self.lb_account.setText('登录状态：官网已登录（' + str(who) + '）\n'
                    '点击「读取课程」读取我学的课与我教的课，选择课程后点击「打开课程」。')
        else:
            self.lb_account.setText(
                "登录状态：尚未登录。扫码或用 cookie 登录后，登录态只保存在该账号档案里。")
        self.ed_domain.setText(str(info.get("domain") or "") or DEFAULT_DOMAIN)
        self.ed_domain.setPlaceholderText("粘贴学校雨课堂登录网址或完整域名")
        self.ed_cookie.clear()
        self.lb_step.setText("登录步骤：" + (self._steps.get(self._current) or "未开始"))
        self._sync_buttons()
        self._restore_task_options()
        self.sig_login_changed.emit()

    def _restore_task_options(self):
        options = (self._store.get(self._current) or {}).get('workspace_options')
        options = options if isinstance(options, dict) else {}
        self._restoring_task_options = True
        try:
            index = self.cb_task.findData(options.get('task', 'study'))
            self.cb_task.setCurrentIndex(max(0, index))
            try:
                jobs = int(options.get('jobs', 1))
            except (TypeError, ValueError):
                jobs = 1
            self.sp_jobs.setValue(max(1, min(3, jobs)))
        finally:
            self._restoring_task_options = False
        self._sync_buttons()

    def _save_task_options(self, *_):
        if not self._restoring_task_options and self._current:
            self._store.update(self._current, workspace_options={
                'task': self.cb_task.currentData(), 'jobs': self.sp_jobs.value()})

    def _on_account(self, _info: dict):
        acc_id = self._sender_acc_id()
        if acc_id == self._current:
            self._fill_account()
        self._refresh_start_all()

    def _prepare_school(self):
        try:
            previous = str(self.runner.account.get('domain') or '')
            domain = self.runner.configure_school(self.ed_domain.text())
            self.ed_domain.setText(domain)
            if previous != domain:
                self._courses.pop(self._current, None)
                self._selected_cache.pop(self._current, None)
                self._rebuild_courses()
            return domain
        except Exception as exc:
            self.append_log("WARNING", str(exc), self._current)
            self.lb_step.setText(str(exc))
            self.ed_domain.setFocus()
            return None

    def _on_save_domain(self):
        acc_id = self._current
        domain = self._prepare_school()
        if not domain:
            return
        self.runner.reload_account()
        self._fill_account()
        self.append_log("INFO", "学校域名已保存：" + domain, acc_id)

    def _on_logout(self):
        self.runner.logout()
        self._fill_account()
        self._refresh_start_all()

    # ------------------------------------------------------------------ 登录
    def _on_auto_login(self):
        if self.browser.isHidden():
            self._toggle_browser_visible()
        self.lb_step.setText('正在打开官方扫码页面…')
        self.runner.login_auto()

    def _toggle_browser_visible(self):
        visible = not self.browser.isHidden()
        self.browser.set_browser_visible(not visible)
        self.btn_browser_visible.setText('显示浏览器' if visible else '隐藏浏览器')

    def _toggle_browser_mode(self):
        if self.browser.isHidden():
            self._toggle_browser_visible()
        self.browser.currentWidget().toggle_external()
        self.btn_browser_mode.setText('切换内置浏览器' if self.browser.currentWidget().external else '切换独立窗口')

    def _on_login(self):
        acc_id = self._current
        domain = self._prepare_school()
        if not domain:
            return
        self.runner.reload_account()
        self.qr_box.setVisible(True)
        self.lb_qr.setText("正在获取二维码…")
        self.lb_qr_hint.setText("用微信扫一扫 → 确认登录。二维码约 60 秒自动刷新。")
        self.lb_step.setText("登录步骤：准备中…")
        self.runner.login(domain)

    def _on_cancel(self):
        self.runner.cancel_login()

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
            self.append_log("WARNING",
                            "请先粘贴 cookie（形如 csrftoken=…; sessionid=…）", acc_id)
            return
        domain = self._prepare_school()
        if not domain:
            return
        self.append_log("INFO", "使用 cookie 登录：" + masked_cookie(cookie), acc_id)
        self.runner.login_with_cookie(acc_id, cookie, domain)

    def show_qrcode(self, png: bytes):
        acc_id = self._sender_acc_id()
        if acc_id != self._current:
            return
        pm = QPixmap()
        if not pm.loadFromData(bytes(png or b""), "PNG") or pm.isNull():
            self.lb_qr.setText("二维码渲染失败，请查看日志")
            return
        self.lb_qr.setPixmap(pm.scaled(180, 180, Qt.KeepAspectRatio,
                                       Qt.SmoothTransformation))
        self.qr_box.setVisible(True)

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
        selected = self._selected_cache.get(self._current, courses)
        for index, course in enumerate(courses):
            name = str(course.get("course_name") or course.get("title") or "未命名课程")
            host = QWidget()
            row = QHBoxLayout(host)
            row.setContentsMargins(0, 5, 0, 5)
            cb = QCheckBox()
            cb.setAccessibleName(name)
            cb.setToolTip(name)
            cb.setChecked(course in selected)
            row.addWidget(cb, 0, Qt.AlignTop)
            label = QLabel(str(index + 1) + ". " + name)
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(label, 1)
            host.setProperty("course_search", name.casefold())
            cb.toggled.connect(self._update_count)
            self.courses_box.addWidget(host)
            self._boxes.append(cb)
        self.empty.set_visible(not courses)
        self._filter_courses()
        self._update_count()
        busy = self._phases.get(self._current, "idle") in _BUSY_PHASES
        for cb in self._boxes:
            cb.setEnabled(not busy)

    def _filter_courses(self, *_):
        query = self.ed_search.text().strip().casefold()
        for index in range(self.courses_box.count()):
            host = self.courses_box.itemAt(index).widget()
            if host is not None:
                host.setVisible(not query or query in str(host.property("course_search") or ""))

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
        self.inspector.setCurrentIndex(0)
        self.runner.load_courses()

    def _on_open_course(self):
        selected = self._selected()
        if not selected:
            self.append_log('WARNING', '请先读取并选择一门课程。')
            return
        if self.browser.isHidden():
            self._toggle_browser_visible()
        self.runner.open_course(selected[0])

    def _on_configured_start(self):
        if self.cb_task.currentData() == 'answer':
            self._on_answer()
        else:
            self._on_start()

    def _on_start(self):
        acc_id = self._current
        selected = self._selected(acc_id)
        if not selected:
            self.append_log("WARNING", "请先读取课程并勾选要处理的课程", acc_id)
            return
        self._launch(acc_id, selected)

    def _on_answer(self):
        """对选中课程做作业 / 章节测验（题库链 + 接口自带答案）。"""
        acc_id = self._current
        selected = self._selected(acc_id)
        if not selected:
            self.append_log("WARNING", "请先读取课程并勾选要答题的课程", acc_id)
            return
        runner = self._runner_for(acc_id)
        if not runner.busy():
            runner.reload_account()
        if not runner.logged_in():
            self.append_log("WARNING", "账号未登录，已跳过："
                            + self._store.display_name(acc_id), acc_id)
            return
        if acc_id == self._current:
            self.bar.setRange(0.0, 1.0)
            self.bar.setValue(0)
            self.lb_stats.setText("已选 " + str(len(selected)) + " 门课程，正在答题…")
        runner.answer(selected)

    def _launch(self, acc_id: str, selected: list) -> bool:
        runner = self._runner_for(acc_id)
        if not runner.busy():
            runner.reload_account()
        if not runner.logged_in():
            self.append_log("WARNING", "账号未登录，已跳过："
                            + self._store.display_name(acc_id), acc_id)
            return False
        if not runner.busy():
            options = {"jobs": self.sp_jobs.value()}
        else:
            options = {"jobs": 1}
        if acc_id == self._current:
            self.bar.setRange(0.0, 1.0)
            self.bar.setValue(0)
            self.lb_stats.setText("已选 " + str(len(selected)) + " 门课程，正在开始…")
        if runner.account.get('login_mode') == 'standard':
            if any(c.get('identity') != 2 for c in selected):
                self.append_log('WARNING', '请仅选择我学的课；我教的课可使用打开课程查看。', acc_id)
                return False
            if acc_id == self._current and self.browser.isHidden():
                self._toggle_browser_visible()
        runner.start(selected, options)
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
        course = str(info.get("course") or "")
        video = str(info.get("video") or "")
        rate = float(info.get("rate") or 0.0)
        ci = int(info.get("course_index") or 0)
        ct = int(info.get("course_total") or 0)
        head = ("第 " + str(ci) + "/" + str(ct) + " 门　·　") if ct else ""
        self.lb_now.setText(head + course + ("　·　" + video if video else ""))
        if video:
            self.bar.setRange(0.0, 1.0)
            self.bar.setValue(max(0.0, min(1.0, rate)))
        if info.get('browser_queue'):
            self.lb_stats.setText('正在浏览器学习 · 已打开 ' + str(info.get('visited', 0))
                + '/' + str(ct) + ' 门；完成当前课程后点击下一门。学习进度以官网为准。')
            self.bar.setValue(0)
            return
        self.lb_stats.setText(
            "当前视频进度 " + format(rate * 100, ".1f") + "%"
            + "　·　已完成 " + str(int(info.get("videos_done") or 0))
            + " / " + str(int(info.get("videos_total") or 0)) + " 个视频")

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
        title = "雨课堂" + (" · " + name if name else "")
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
        """按 phase 统一切换控件可用态（每个账号各记一份，切换账号时重放）。

        「开始刷课」不是简单的"非运行中就给点"——登录、课程、勾选三个前提
        缺一就置灰，并且把**原因写进 tooltip**：留着能点却什么也不做，
        比置灰更让人困惑（运行页那条"运行中禁用"的规矩同理）。
        """
        acc_id = self._sender_acc_id(acc_id)
        phase = str(phase)
        self._phases[acc_id] = phase
        self._refresh_running()
        self._refresh_start_all()
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
        student_only = all(c.get("identity") == 2 for c in self._selected())

        self.ed_domain.setEnabled(not busy)
        self.btn_save_domain.setEnabled(not busy)
        self.btn_school_login.setEnabled(not busy)
        self.btn_logout.setEnabled(not busy and logged)
        self.btn_logout.setToolTip("" if logged else "当前账号尚未登录")
        self.btn_login.setEnabled(not busy)
        self.btn_cancel.setVisible(phase == "logging_in")
        self.btn_cookie.setEnabled(not busy)
        self.ed_cookie.setEnabled(not busy)
        self.btn_cookie_ok.setEnabled(not busy)
        self.cb_workers.setEnabled(not busy)
        self.cb_task.setEnabled(not busy)
        self.sp_jobs.setEnabled(not busy and self.runner.account.get("login_mode") != "standard")
        for box in self._boxes:
            box.setEnabled(not busy)

        professional = self.runner.account.get('login_mode') != 'standard'
        self.btn_start_all.setVisible(professional)
        self.btn_stop_all.setVisible(professional)
        self.lb_task_hint.setText(
            '普通版：开始浏览器学习后，在官网完成当前课程，再点击下一门。自动答题暂不可用；学习进度以官网为准。'
            if not professional else '学校专业版可处理学习任务与作业：先查题库，未命中交给大模型。')
        self.btn_load.setEnabled(not busy and logged)
        self.btn_open_course.setEnabled(not busy and logged and not professional and has_selected)
        self.btn_open_course.setToolTip('在内置浏览器打开第一个选中的普通版课程。')
        if busy:
            self.btn_load.setToolTip("正在处理中，请稍候")
        elif not logged:
            self.btn_load.setToolTip("请先扫码登录雨课堂（或用 cookie 登录）")
        else:
            self.btn_load.setToolTip("读取该账号下的雨课堂课程")
        if logged and not professional:
            self.btn_load.setToolTip('读取普通版我学的课与我教的课。')
            self.btn_start.setToolTip('按所选课程打开学习队列；在官网学习后点击下一门。' if student_only else '请仅选择我学的课。')

        self.btn_stop.setEnabled(phase == "running")
        self.btn_stop.setToolTip("" if phase == "running" else "当前账号没有在刷课")

        student_only = all(c.get('identity') == 2 for c in self._selected())
        can_start = logged and phase in ("ready", "loaded") and has_selected and (professional or student_only)
        if not professional and self.cb_task.currentData() == 'answer':
            can_start = False
        self.btn_start.setText('开始任务' if professional else '开始浏览器学习')
        self.cb_task.setItemText(0, '视频学习' if professional else '浏览器学习（手动完成）')
        self.btn_next.setVisible(not professional)
        self.btn_next.setEnabled(not professional and phase == 'running')
        self.btn_answer.setVisible(False)
        self.btn_start.setEnabled(can_start)
        if phase == "running":
            tip = "正在刷课；先「停止」才能再次开始"
        elif phase == "loading":
            tip = "正在读取课程，请稍候"
        elif not logged:
            tip = "请先扫码登录雨课堂"
        elif not has_courses:
            tip = "请先点「读取课程」"
        elif not has_selected:
            tip = "请至少勾选一门课程"
        else:
            tip = "开始处理勾选的课程（按心跳上报，不需要人工看视频）"
        self.btn_start.setToolTip(tip)
        if logged and not professional:
            self.btn_start.setToolTip('按所选课程打开学习队列；在官网学习后点击下一门。' if student_only else '请仅选择我学的课。')

        self.btn_answer.setEnabled(can_start and professional)
        if phase == "running":
            ans_tip = "正在处理；先「停止」才能再次操作"
        elif not logged:
            ans_tip = "请先扫码登录雨课堂"
        elif not has_courses:
            ans_tip = "请先点「读取课程」"
        elif not has_selected:
            ans_tip = "请至少勾选一门课程"
        else:
            ans_tip = ("做作业 / 章节测验：优先用接口自带答案，再查题库栈"
                       "（官方聚合 → 免费源 → 本地知识库 → AI 兜底）")
        self.btn_answer.setToolTip(ans_tip)
        if logged and not professional:
            self.btn_answer.setToolTip('普通版作业请先打开课程在网页中操作。')

        self.btn_all.setEnabled(not busy)
        self.btn_none.setEnabled(not busy)
        self._refresh_start_all()

    def _any_startable(self) -> bool:
        for acc_id, _name in self._store.pairs():
            if self._phases.get(acc_id) == "running":
                continue
            if not self._runner_for(acc_id).logged_in() or self._runner_for(acc_id).account.get('login_mode') == 'standard':
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
        self.browser.release_all()
        for runner in list(self._runners.values()):
            try:
                runner.shutdown()
            except Exception:
                pass


def _white_mark(size: int) -> QPixmap:
    """Hero 上的白色雨滴标志（复用 icons 的 yuketang 形状）。"""
    from gui_app import icons as I
    return I.pixmap("yuketang", "#FFFFFF", int(size))
