# -*- coding: utf-8 -*-
"""U校园页：品牌 Hero 条 + 多账号并行 + AI 配置展示 + 任务清单与日志。

U校园（Unipus）是独立于学习通的区域：账号存 unipus_accounts.json
（AccountStore 档案，与 accounts.json 分离；旧的 unipus.json 会自动迁移），
AI 答题复用「AI 大模型」页配置的同一份大模型。视觉走 theme.UNIPUS
（v1.6.8 起即主色青绿，四平台共用一套强调色）。

多账号并行
----------
- 账号条（AccountBar）切换 / 新增 / 删除账号，chip 上有运行态圆点；
- 每个账号一个 UnipusRunner = 一个独立的 Edge 会话，可以同时跑；
- 「全部连接」依次对所有待机账号发起连接，「全部停止」对所有 runner
  发送停止请求。多个 Edge 窗口同时开是刻意为之：验证码仍需人工完成。

引擎侧见 gui_app/unipus_engine.py（UnipusRunner 注册表 + api/unipus vendor）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from gui_app.browser_workspace import BrowserWorkspace
from gui_app import icons as I
from gui_app import theme as T
from gui_app.unipus_engine import (
    ai_override_keys, all_runners, drop_runner, get_runner, resolve_ai,
    shutdown_all, unipus_load, unipus_save, _mask_key,
)
from gui_app.widgets import AccountBar, Badge, Card, EmptyState

_PHASE_TEXT = {
    "idle": ("待机", "idle"),
    "connecting": ("连接中", "info"),
    "login_wait": ("等待登录", "info"),
    "ready": ("已连接", "ok"),
    "scanned": ("已扫描", "ok"),
    "scanning": ("扫描中", "info"),
    "running": ("处理中", "info"),
    "error": ("出错", "error"),
}

#: 圆点（忙碌）状态：正在真实占用一个 Edge 会话 / 正在处理
_BUSY_PHASES = ("connecting", "login_wait", "scanning", "running")

_CONTROL_HINT = (
    "每个账号都有独立的内置浏览器；多账号可以同时连接、同时处理 —— "
    "首次登录需要人工完成验证码，之后登录会话会被记住（填一次账密，以后自动连）。"
    "扫描时已完成的任务点会标记为「已完成」且默认跳过；处理时每个任务只做一遍，"
    "失败不重试。AI 答题与学习通共用「AI 大模型」页配置的大模型。")


class UnipusPage(QWidget):
    sig_login_changed = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        from gui_app.unipus_engine import account_store
        self.store = account_store()
        if not self.store.accounts:
            self.store.add()               # 首次使用给一个可编辑的账号
        self._phases: dict[str, str] = {}
        self._views: dict[str, list] = {}
        self._steps: dict[str, str] = {}
        self._wired: set[str] = set()
        self._current = str(self.store.active or "")
        self._restoring_task_options = False
        self._selection_cache = {}
        self._tasks: list[dict] = []       # [{index, display, is_compulsory}]
        self._boxes: list[tuple[int, QCheckBox]] = []

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(12)
        shell.addWidget(self._build_hero())
        shell.addWidget(self._build_account_bar())

        self.workspace = QSplitter(Qt.Horizontal)
        canvas = QWidget()
        self.browser_canvas = canvas
        canvas_lay = QVBoxLayout(canvas)
        canvas_lay.setContentsMargins(0, 0, 0, 0)
        browser_bar = QHBoxLayout()
        label = QLabel("课程浏览器  /  当前账号的独立会话")
        label.setObjectName("WorkspaceCaption")
        browser_bar.addWidget(label)
        browser_bar.addStretch()
        self.btn_browser_visible = QPushButton('隐藏浏览器')
        self.btn_browser_visible.clicked.connect(self._toggle_browser_visible)
        browser_bar.addWidget(self.btn_browser_visible)
        self.btn_browser_mode = QPushButton("切换独立窗口")
        self.btn_browser_mode.setToolTip("在客户端内置浏览器与独立 Edge 窗口之间切换，保留登录会话。")
        self.btn_browser_mode.clicked.connect(self._toggle_browser_mode)
        browser_bar.addWidget(self.btn_browser_mode)
        shell.addLayout(browser_bar)
        self.browser = BrowserWorkspace()
        self.browser.select(self._current)
        canvas_lay.addWidget(self.browser, 1)
        self.workspace.addWidget(canvas)

        self.inspector = QTabWidget()
        self.inspector.setObjectName("WorkspaceInspector")
        self.inspector.setMinimumWidth(0)
        task_scroll = QScrollArea()
        task_scroll.setWidgetResizable(True)
        task_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        task_scroll.setWidget(self._build_tasks())
        self.inspector.addTab(task_scroll, "任务")
        account = QWidget()
        account_lay = QVBoxLayout(account)
        account_lay.setContentsMargins(0, 0, 0, 0)
        account_lay.addWidget(self._build_account())
        account_lay.addWidget(self._build_ai())
        account_lay.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(account)
        self.inspector.addTab(scroll, "账号与 AI")
        self.inspector.addTab(self._build_log(), "日志")
        self.workspace.addWidget(self.inspector)
        self.workspace.setStretchFactor(0, 1)
        self.workspace.setStretchFactor(1, 0)
        self.workspace.setChildrenCollapsible(False)
        self.workspace.setSizes([640, 680])
        shell.addWidget(self.workspace, 1)

        # ---- 首次接线 + 回填 ----
        self._refresh_accounts()
        self.refresh_ai_info()
        self._apply_phase(self._phase_of(self._current))
        self._sync_buttons()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'workspace'):
            direction = Qt.Vertical if self.width() < 1100 else Qt.Horizontal
            if self.workspace.orientation() != direction:
                self.workspace.setOrientation(direction)
                self.workspace.setSizes([360, 640])

    # ------------------------------------------------------------------ Runner
    @property
    def runner(self):
        """当前账号的 runner（旧的单 runner 接口，保持可用）。"""
        r = get_runner(self._current)
        self._wire_runner(r)
        return r

    def _wire_runner(self, runner):
        if runner.acc_id in self._wired:
            return runner
        self._wired.add(runner.acc_id)
        runner.sig_log.connect(self._on_runner_log)
        runner.sig_login_step.connect(self._on_runner_step)
        runner.sig_state.connect(self._on_runner_state)
        runner.sig_tasks.connect(self._on_runner_tasks)
        runner.sig_done.connect(self._on_runner_done)
        runner.sig_browser.connect(self.browser.start)
        return runner

    def _phase_of(self, acc_id: str) -> str:
        if not acc_id:
            return "idle"
        runner = all_runners().get(acc_id)
        if runner is None and self.store.get(acc_id):
            runner = get_runner(acc_id)
        if runner is not None:
            self._phases[acc_id] = runner.phase
            return runner.phase
        return self._phases.get(acc_id, "idle")

    # ------------------------------------------------------------------ Hero
    def _build_hero(self) -> QWidget:
        """品牌条：U校园彩色标志 + 标题 + 状态徽标 + 连接 / 全部连接 / 全部停止。"""
        hero = QFrame()
        hero.setObjectName("UnipusHero")
        lay = QHBoxLayout(hero)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(14)

        mark = QLabel()
        mark.setObjectName("UnipusHeroMark")
        mark.setFixedSize(32, 32)
        mark.setAlignment(Qt.AlignCenter)
        mark.setPixmap(I.brand_pixmap(30))
        lay.addWidget(mark, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(0)
        t = QLabel("U校园课程工作区")
        t.setObjectName("UnipusHeroTitle")
        col.addWidget(t)
        lay.addLayout(col, 1)

        self.badge = Badge(*_PHASE_TEXT["idle"])
        lay.addWidget(self.badge)

        self.btn_connect = QPushButton("连接并登录")
        self.btn_connect.setProperty("variant", "unipus")
        self.btn_connect.setMinimumHeight(38)
        self.btn_connect.setMinimumWidth(130)
        self.btn_connect.clicked.connect(self._on_connect_toggle)
        lay.addWidget(self.btn_connect, 0, Qt.AlignVCenter)

        self.btn_connect_all = QPushButton("全部连接")
        self.btn_connect_all.setMinimumHeight(38)
        self.btn_connect_all.clicked.connect(self._on_connect_all)
        lay.addWidget(self.btn_connect_all, 0, Qt.AlignVCenter)

        self.btn_stop_all = QPushButton("全部停止")
        self.btn_stop_all.setProperty("variant", "danger")
        self.btn_stop_all.setMinimumHeight(38)
        self.btn_stop_all.clicked.connect(self._on_stop_all)
        lay.addWidget(self.btn_stop_all, 0, Qt.AlignVCenter)

        self.lb_hint = QLabel(_CONTROL_HINT)
        self.lb_hint.setObjectName("CardHint")
        self.lb_hint.setWordWrap(True)

        wrap = QVBoxLayout()
        wrap.setSpacing(8)
        wrap.addWidget(hero)
        self.lb_hint.setVisible(False)
        host = QWidget()
        host.setObjectName("Root")
        w = QVBoxLayout(host)
        w.setContentsMargins(0, 0, 0, 0)
        w.addLayout(wrap)
        self._hero_host = host
        return host

    # ------------------------------------------------------------------ 账号条
    def _set_bar_brand(self):
        """账号条品牌色：新 AccountBar 有 set_brand（连子控件一起 repolish），
        旧版只有 setProperty；两种都兼容。"""
        setter = getattr(self.acc_bar, "set_brand", None)
        if callable(setter):
            setter("unipus")
        else:
            self.acc_bar.setProperty("brand", "unipus")

    def _build_account_bar(self) -> AccountBar:
        self.acc_bar = AccountBar()
        self._set_bar_brand()
        self.acc_bar.sig_switch.connect(self._on_switch_account)
        self.acc_bar.sig_add.connect(self._on_add_account)
        self.acc_bar.sig_remove.connect(self._on_remove_account)
        return self.acc_bar

    # ------------------------------------------------------------------ 账号 + AI
    def _build_account_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self._build_account(), 5)
        row.addWidget(self._build_ai(), 4)
        return row

    def _build_account(self) -> Card:
        card = Card(
            "U校园账号",
            "登录信息仅保存在本机。每个账号使用独立的课程会话。",
            icon="user", accent=T.UNIPUS, accent_soft=T.UNIPUS_SOFT,
            margins=(20, 16, 20, 18))

        row = QVBoxLayout()
        row.setSpacing(8)
        self.ed_user = QLineEdit()
        self.ed_user.setPlaceholderText("手机号 / 账号")
        self.ed_user.setMinimumHeight(34)
        self.ed_pass = QLineEdit()
        self.ed_pass.setPlaceholderText("密码")
        self.ed_pass.setMinimumHeight(34)
        self.ed_pass.setEchoMode(QLineEdit.EchoMode.Password)
        row.addWidget(QLabel("账号"), 0)
        row.addWidget(self.ed_user, 2)
        row.addWidget(QLabel("密码"), 0)
        row.addWidget(self.ed_pass, 2)
        card.add(row)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.ed_token = QLineEdit()
        self.ed_token.setPlaceholderText("官方 token（可选，普通用户留空即可）")
        self.ed_token.setMinimumHeight(34)
        self.ed_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.btn_save = QPushButton("保存账密")
        self.btn_save.setMinimumHeight(34)
        self.btn_save.clicked.connect(self._on_save_account)
        row2.addWidget(QLabel("token"), 0)
        row2.addWidget(self.ed_token, 1)
        row2.addWidget(self.btn_save)
        card.add(row2)

        self.lb_login = QLabel("登录状态：未连接")
        self.lb_login.setObjectName("CardHint")
        self.lb_login.setWordWrap(True)
        card.add(self.lb_login)
        return card

    def _build_ai(self) -> Card:
        card = Card(
            "AI 答题配置",
            "先查题库，未命中再用大模型。听力、视频采用内置本地语音识别，无需另装 Whisper 或 FFmpeg；首次识别可能较慢。",
            icon="spark", accent=T.UNIPUS, accent_soft=T.UNIPUS_SOFT,
            margins=(20, 16, 20, 18))
        self.lb_ai = QLabel()
        self.lb_ai.setObjectName("CardHint")
        self.lb_ai.setWordWrap(True)
        self.lb_ai.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.add(self.lb_ai)

        self.btn_ai_goto = QPushButton("去「AI 大模型」页配置")
        self.btn_ai_goto.setProperty("variant", "ghost")
        self.btn_ai_goto.setMinimumHeight(32)
        self.btn_ai_goto.clicked.connect(self._goto_ai)
        self.btn_ai_goto.setVisible(False)
        card.add(self.btn_ai_goto)
        return card

    # ------------------------------------------------------------------ 日志卡
    def _build_log(self) -> Card:
        log_card = Card("U校园日志", "多账号日志以 [账号名] 开头，便于分辨来源。",
                        icon="clipboard", accent=T.UNIPUS,
                        accent_soft=T.UNIPUS_SOFT, margins=(20, 14, 20, 16))
        self.log = QPlainTextEdit()
        self.log.setObjectName("LogView")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log.setPlaceholderText("U校园的处理日志会实时显示在这里…")
        self.log.setMinimumHeight(180)
        log_card.add(self.log)
        return log_card

    # ------------------------------------------------------------------ 任务
    def _build_tasks(self) -> Card:
        card = Card(
            "任务配置与课程清单",
            "打开课程 → 读取任务 → 勾选并开始；已完成任务默认跳过。",
            icon="list", accent=T.UNIPUS, accent_soft=T.UNIPUS_SOFT,
            margins=(20, 16, 20, 18))

        ctrl = QGridLayout()
        ctrl.setSpacing(10)
        self.btn_scan = QPushButton("读取任务")
        self.btn_scan.setMinimumHeight(38)
        self.btn_scan.clicked.connect(self._on_scan)
        self.btn_process = QPushButton("处理选中任务 (0)")
        self.btn_process.setProperty("variant", "unipus")
        self.btn_process.setMinimumHeight(38)
        self.btn_process.clicked.connect(self._on_process)
        self.btn_quick = QPushButton("快速处理当前页")
        self.btn_quick.setMinimumHeight(38)
        self.btn_quick.clicked.connect(self._on_quick)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setProperty("variant", "danger")
        self.btn_stop.setMinimumHeight(38)
        self.btn_stop.clicked.connect(self._on_stop_current)
        for i, b in enumerate((self.btn_scan, self.btn_quick, self.btn_process, self.btn_stop)):
            ctrl.addWidget(b, i // 2, i % 2)

        config = QGridLayout()
        config.setColumnStretch(1, 1)
        self.cb_task = QComboBox()
        self.cb_task.addItem('处理所选任务', 'selected')
        self.cb_task.addItem('处理浏览器当前页', 'current')
        self.cb_task.currentIndexChanged.connect(lambda: self._sync_buttons())
        config.addWidget(QLabel('任务范围'), 0, 0)
        config.addWidget(self.cb_task, 0, 1)
        self.cb_skip_finished = QCheckBox('默认跳过已完成任务')
        self.cb_skip_finished.setChecked(True)
        self.cb_skip_finished.toggled.connect(self._apply_skip_finished)
        self.cb_skip_finished.toggled.connect(self._save_task_options)
        self.cb_task.currentIndexChanged.connect(self._save_task_options)
        config.addWidget(self.cb_skip_finished, 1, 0, 1, 2)
        card.add(config)
        card.add(ctrl)
        self.lb_task_hint = QLabel('先查题库，未命中直接使用大模型；缺少可靠答案不猜题。任务顺序处理，共用一个浏览器会话。')
        self.lb_task_hint.setObjectName('CardHint')
        self.lb_task_hint.setWordWrap(True)
        self.lb_task_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        card.add(self.lb_task_hint)
        self.ed_task_search = QLineEdit()
        self.ed_task_search.setPlaceholderText('搜索章节或任务')
        self.ed_task_search.textChanged.connect(self._filter_tasks)
        card.add(self.ed_task_search)

        pick = QHBoxLayout()
        pick.setSpacing(8)
        b_all = QPushButton("全选")
        b_all.setProperty("variant", "ghost")
        b_all.clicked.connect(lambda: self._select_all(True))
        b_comp = QPushButton("仅必修")
        b_comp.setProperty("variant", "ghost")
        b_comp.clicked.connect(self._select_compulsory)
        b_none = QPushButton("全不选")
        b_none.setProperty("variant", "ghost")
        b_none.clicked.connect(lambda: self._select_all(False))
        self.lb_count = QLabel("已选 0 / 0")
        self.lb_count.setObjectName("CardHint")
        for b in (b_all, b_comp, b_none):
            pick.addWidget(b)
        pick.addStretch(1)
        pick.addWidget(self.lb_count)
        card.add(pick)

        self.summary_host = QWidget()
        self.summary_lay = QVBoxLayout(self.summary_host)
        self.summary_lay.setContentsMargins(0, 0, 0, 0)
        self.summary_lay.setSpacing(4)
        card.add(self.summary_host)

        # 任务清单本体（内层滚动，高度固定，避免整页被撑爆）
        self.task_area = QScrollArea()
        self.task_area.setWidgetResizable(True)
        self.task_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.task_area.setFrameShape(QFrame.NoFrame)
        self.task_area.setObjectName("TaskPanel")
        self.task_area.setMinimumHeight(180)
        self.task_area.setMaximumHeight(16777215)
        host = QWidget()
        self.tasks_lay = QVBoxLayout(host)
        self.tasks_lay.setContentsMargins(10, 8, 10, 8)
        self.tasks_lay.setSpacing(6)
        self._add_empty_state()
        self.task_area.setWidget(host)
        card.add(self.task_area)
        return card

    def _add_empty_state(self):
        self.empty_state = EmptyState(
            "unipus", "还没有任务数据",
            "连接并登录当前账号后点「扫描任务列表」，课程任务会列在这里"
            "（已完成的默认跳过）；也可以翻到题目页用「快速处理当前页」。")
        self.empty_state.set_visible(True)
        self.tasks_lay.addWidget(self.empty_state)
        self.tasks_lay.addStretch(1)

    # ------------------------------------------------------------------ 样式工具
    @staticmethod
    def _polish(w):
        try:
            w.style().unpolish(w)
            w.style().polish(w)
            w.update()
        except Exception:
            pass

    # ------------------------------------------------------------------ 账号操作
    def _refresh_accounts(self):
        ids = [a["id"] for a in self.store.accounts]
        if self._current not in ids:
            self._current = str(self.store.active or (ids[0] if ids else ""))
        for aid in ids:
            self._wire_runner(get_runner(aid))
        self._refresh_bar()
        self._refresh_account_card()
        self._update_summary()

    def _refresh_bar(self):
        self._set_bar_brand()
        running = {aid for aid, r in all_runners().items()
                   if r.phase in ("running",)}
        self.acc_bar.set_accounts(self.store.pairs(), self._current,
                                  running=running)
        self._polish(self.acc_bar)
        for b in getattr(self.acc_bar, "_buttons", {}).values():
            self._polish(b)

    def _refresh_account_card(self):
        u = unipus_load(self._current)
        self.ed_user.setText(str(u.get("username") or ""))
        self.ed_pass.setText(str(u.get("password") or ""))
        self.ed_token.setText(str(u.get("token_full") or ""))
        self._restore_task_options()
        self._refresh_login_state()

    def _restore_task_options(self):
        options = (self.store.get(self._current) or {}).get('workspace_options')
        options = options if isinstance(options, dict) else {}
        self._restoring_task_options = True
        try:
            index = self.cb_task.findData(options.get('scope', 'selected'))
            self.cb_task.setCurrentIndex(max(0, index))
            self.cb_skip_finished.setChecked(options.get('skip_finished', True) is not False)
        finally:
            self._restoring_task_options = False

    def _save_task_options(self, *_):
        if not self._restoring_task_options and self._current:
            self.store.update(self._current, workspace_options={
                'scope': self.cb_task.currentData(),
                'skip_finished': self.cb_skip_finished.isChecked()})

    def _fill_account(self):
        """旧接口名（测试 / 兼容）：回填当前账号的账密。"""
        self._refresh_account_card()

    def _refresh_login_state(self):
        self.sig_login_changed.emit()
        runner = get_runner(self._current) if self._current else None
        name = self.store.display_name(self._current)
        acc = self.store.get(self._current) or {}
        last = str(acc.get("last_login") or "")
        if runner is not None and runner.connected:
            self.lb_login.setText(f"登录状态：已连接（{name}）")
        elif last:
            self.lb_login.setText(f"登录状态：未连接　·　该账号上次登录：{last}")
        else:
            self.lb_login.setText("登录状态：未连接")

    def _on_save_account(self):
        user = self.ed_user.text().strip()
        pwd = self.ed_pass.text()
        token = self.ed_token.text().strip()
        unipus_save({"username": user, "password": pwd, "token_full": token},
                    self._current)
        name = self.store.display_name(self._current)
        self.append_log("SUCCESS", f"账号「{name}」的账密已保存。")
        self.refresh_ai_info()
        self._refresh_login_state()

    def refresh_ai_info(self):
        endpoint, key, model = resolve_ai(self._current)
        overrides = ai_override_keys(self._current)
        if endpoint and key and model:
            if len(overrides) >= 3:
                src = "本账号覆盖"
            elif overrides:
                src = "本账号部分覆盖 + 其余继承「AI 大模型」页"
            else:
                src = "继承「AI 大模型」页（与学习通共用）"
            self.lb_ai.setText(
                f"配置来源：{src}\nendpoint：{endpoint}\n"
                f"model：{model}\nkey：{_mask_key(key)}")
            self.btn_ai_goto.setVisible(False)
        else:
            self.lb_ai.setText(
                "⚠ AI 配置不完整 —— U校园答题依赖 AI 大模型。\n"
                "请先到「AI 大模型」页配置（endpoint / key / model）；"
                "该页配置与学习通共用，本页只做展示。")
            self.btn_ai_goto.setVisible(True)

    def _goto_ai(self):
        win = self.window()
        goto = getattr(win, "goto", None)
        if callable(goto):
            try:
                goto("ai")
                return
            except Exception:
                pass
        self.append_log("INFO", "请从左侧导航打开「AI 大模型」页配置"
                                "（endpoint / key / model）。")

    def _on_switch_account(self, acc_id):
        acc_id = str(acc_id or "")
        if not acc_id or not self.store.get(acc_id):
            return
        if acc_id == self._current:
            self._refresh_bar()
            return
        self.store.set_active(acc_id)
        self._current = acc_id
        self.browser.select(acc_id)
        self._wire_runner(get_runner(acc_id))
        self._refresh_bar()
        self._refresh_account_card()
        self.refresh_ai_info()
        self.fill_tasks(self._views.get(acc_id) or [])
        self._apply_phase(self._phase_of(acc_id))
        self._update_summary()
        self._sync_buttons()
        self.append_log("INFO",
                        f"已切换到账号「{self.store.display_name(acc_id)}」。")

    def _on_add_account(self):
        acc = self.store.add()
        acc_id = str(acc.get("id") or "")
        self._current = acc_id
        self.browser.select(acc_id)
        self._wire_runner(get_runner(acc_id))
        self._refresh_bar()
        self._refresh_account_card()
        self.refresh_ai_info()
        self.fill_tasks([])
        self._apply_phase("idle")
        self._update_summary()
        self._sync_buttons()
        self.append_log("INFO", f"已新增账号「{self.store.display_name(acc_id)}」："
                                f"填好账密后可单独连接，也可用「全部连接」。")

    def _confirm_remove(self, name: str) -> bool:
        r = QMessageBox.question(
            self, "删除账号",
            f"确定删除账号「{name}」吗？\n\n"
            "该账号的运行实例会被停止并关闭对应 Edge 窗口，其它账号不受影响。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return r == QMessageBox.Yes

    def _on_remove_account(self, acc_id):
        acc_id = str(acc_id or self._current)
        if not self.store.get(acc_id):
            return
        name = self.store.display_name(acc_id)
        if not self._confirm_remove(name):
            return
        # 先从档案里摘掉，再 drop_runner：shutdown() 会同步发 sig_state，
        # 若账号还在档案里，页面的按钮刷新会把刚删掉的 runner 又建回来。
        was_current = (acc_id == self._current)
        self.store.remove(acc_id)
        if was_current or not self.store.get(self._current):
            self._current = str(self.store.active or "")
        drop_runner(acc_id)
        self._phases.pop(acc_id, None)
        self._views.pop(acc_id, None)
        self._steps.pop(acc_id, None)
        self._wired.discard(acc_id)
        self._refresh_bar()
        self._refresh_account_card()
        self.refresh_ai_info()
        self.fill_tasks(self._views.get(self._current) or [])
        self._apply_phase(self._phase_of(self._current))
        self._update_summary()
        self._sync_buttons()
        self.append_log("INFO", f"已删除账号「{name}」：对应 Edge 窗口已关闭。")

    # ------------------------------------------------------------------ 连接 / 停止
    def _on_connect_toggle(self):
        if self.browser.isHidden():
            self._toggle_browser_visible()
        runner = self.runner
        if runner.connected or runner.phase not in ("idle",):
            self._disconnect_current()
        else:
            runner.connect()
        self._sync_buttons()

    def _disconnect_current(self):
        runner = self.runner
        acc_id = runner.acc_id
        name = self.store.display_name(acc_id)
        self.browser.reset(acc_id)
        runner.shutdown()
        self._phases[acc_id] = "idle"
        self._views[acc_id] = []
        if acc_id == self._current:
            self.fill_tasks([])
        self._refresh_bar()
        self._refresh_login_state()
        self._apply_phase("idle")
        self._update_summary()
        self._sync_buttons()
        self.append_log("INFO", f"账号「{name}」已断开（浏览器已关闭）。")

    def _on_connect_all(self):
        ids = [a["id"] for a in self.store.accounts]
        if not ids:
            self.append_log("WARNING", "还没有账号：请先点「＋ 新增账号」。")
            return
        self.append_log(
            "INFO",
            f"正在连接全部 {len(ids)} 个账号：每个账号使用独立浏览器会话，"
            "首次登录需人工完成验证码，之后自动复用已保存的登录会话。")
        for acc_id in ids:
            self._wire_runner(get_runner(acc_id)).connect()
        self._update_summary()
        self._sync_buttons()

    def _on_stop_all(self):
        n = 0
        for runner in all_runners().values():
            try:
                runner.stop()
                n += 1
            except Exception:
                pass
        self.append_log("INFO", f"已向 {n} 个账号的 runner 发送停止请求。")
        self._update_summary()
        self._sync_buttons()

    # ------------------------------------------------------------------ Runner 回调
    def _sender_id(self) -> str:
        sender = self.sender()
        return str(getattr(sender, "acc_id", self._current) or self._current)

    def _on_runner_log(self, text: str):
        acc_id = self._sender_id()
        name = self.store.display_name(acc_id)
        self.append_log("INFO", f"[{name}] {text}")

    def _on_runner_step(self, acc_id: str, text: str):
        self._steps[str(acc_id)] = str(text)
        self._update_summary()

    def _on_runner_state(self, phase: str):
        acc_id = self._sender_id()
        self._phases[acc_id] = str(phase)
        if phase == "idle":
            self.browser.reset(acc_id)
        self._refresh_bar()
        self._update_summary()
        if acc_id == self._current:
            self._apply_phase(str(phase))
            self._refresh_login_state()
        self._sync_buttons()
        self.sig_login_changed.emit()

    def _on_runner_tasks(self, views: list):
        acc_id = self._sender_id()
        self._views[acc_id] = list(views or [])
        if acc_id == self._current:
            self.fill_tasks(self._views[acc_id])
        self._update_summary()

    def _on_runner_done(self, kind: str, status: str):
        acc_id = self._sender_id()
        name = self.store.display_name(acc_id)
        if kind == "process" and status == "ok":
            self.append_log("SUCCESS", f"[{name}] 选中的任务全部处理完毕。")
        elif status == "stopped":
            self.append_log("WARNING", f"[{name}] 任务已停止。")
        elif status == "error":
            self.append_log("ERROR", f"[{name}] 任务处理出错，详见上面的日志。")
        runner = get_runner(acc_id)
        self._phases[acc_id] = runner.phase
        self._refresh_bar()
        self._update_summary()
        self._sync_buttons()

    # ------------------------------------------------------------------ 摘要
    def _update_summary(self):
        while self.summary_lay.count():
            item = self.summary_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        pairs = self.store.pairs()
        if not pairs:
            lab = QLabel("还没有账号：点账号条右侧的「＋ 新增账号」开始。")
            lab.setObjectName("CardHint")
            self.summary_lay.addWidget(lab)
            return
        runners = all_runners()
        for acc_id, name in pairs:
            runner = runners.get(acc_id)
            phase = runner.phase if runner is not None else self._phases.get(acc_id, "idle")
            text, _kind = _PHASE_TEXT.get(phase, (phase, "idle"))
            done = runner.processed if runner is not None else 0
            dot = "● " if phase == "running" else (
                "○ " if phase in _BUSY_PHASES else "")
            line = f"{dot}{name}　·　{text}　·　已处理 {done}"
            step = self._steps.get(acc_id, "")
            if step and phase in ("connecting", "login_wait"):
                line += f"（{step}）"
            lab = QLabel(line)
            lab.setObjectName("SlotTitle" if acc_id == self._current else "CardHint")
            self.summary_lay.addWidget(lab)

    # ------------------------------------------------------------------ 任务
    def _on_scan(self):
        self.runner.scan()

    def _on_quick(self):
        self.runner.quick()

    def _on_stop_current(self):
        self.runner.stop()

    def _filter_tasks(self, *_):
        query = self.ed_task_search.text().strip().casefold()
        for _, cb in self._boxes:
            host = cb.parentWidget()
            host.setVisible(not query or query in cb.accessibleName().casefold())

    def _apply_skip_finished(self, on):
        if self._restoring_task_options:
            return
        for index, cb in self._boxes:
            task = next((t for t in self._tasks if t.get('index') == index), {})
            if task.get('is_finished'):
                cb.setChecked(not on)

    def fill_tasks(self, views: list):
        self._tasks = list(views or [])
        self._boxes = []
        while self.tasks_lay.count():
            item = self.tasks_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if not self._tasks:
            self._add_empty_state()
            self._update_count()
            return
        for t in self._tasks:
            name = str(t.get('display') or '未命名任务')
            host = QWidget()
            row = QHBoxLayout(host)
            row.setContentsMargins(0, 5, 0, 5)
            cb = QCheckBox()
            cb.setAccessibleName(name)
            row.addWidget(cb, 0, Qt.AlignTop)
            label = QLabel(name)
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(label, 1)
            finished = bool(t.get("is_finished", False))
            # 已完成的任务点默认不勾选：完成即停，不重复遍历。
            # 保持可点（不禁用）：用户坚持重做时手动勾上即可，
            # 禁用会让「全选」后无法取消，交互自相矛盾。
            cached = self._selection_cache.get(self._current, {})
            key = (t.get('index'), name)
            default = bool(t.get('is_compulsory', False)) and (not finished or not self.cb_skip_finished.isChecked())
            cb.setChecked(cached.get(key, default))
            if finished:
                cb.setToolTip("该任务点已标记为完成，默认跳过；如需重做请手动勾选。")
            idx = int(t.get("index", -1))
            cb.toggled.connect(self._update_count)
            self.tasks_lay.addWidget(host)
            self._boxes.append((idx, cb))
        self.tasks_lay.addStretch(1)
        self._filter_tasks()
        self._update_count()

    def _checked_indices(self) -> list[int]:
        return [i for i, cb in self._boxes if cb.isChecked()]

    def _select_all(self, on: bool):
        for _, cb in self._boxes:
            cb.setChecked(on)

    def _select_compulsory(self):
        for i, cb in self._boxes:
            t = next((x for x in self._tasks if x.get("index") == i), {})
            cb.setChecked(bool(t.get("is_compulsory", False)) and (not t.get("is_finished") or not self.cb_skip_finished.isChecked()))

    def _update_count(self, *_):
        n = len(self._checked_indices())
        self._selection_cache[self._current] = {(i, cb.accessibleName()): cb.isChecked() for i, cb in self._boxes}
        self.btn_process.setText(f"开始任务 ({n})")
        self.lb_count.setText(f"已选 {n} / {len(self._tasks)}")
        self._sync_buttons()

    def _on_process(self):
        if self.cb_task.currentData() == 'current':
            self.runner.quick()
        else:
            self.runner.process(self._checked_indices())

    # ------------------------------------------------------------------ 状态
    def set_state(self, phase: str):
        """兼容旧接口：把 phase 反映到当前账号的徽标与按钮态。"""
        self._phases[self._current] = str(phase)
        self._apply_phase(str(phase))
        self._refresh_bar()
        self._update_summary()
        self._sync_buttons()

    def _apply_phase(self, phase: str):
        text, kind = _PHASE_TEXT.get(phase, (phase, "idle"))
        self.badge.set_kind(kind, text)

    def _sync_buttons(self):
        self.btn_browser_mode.setEnabled(bool(self.browser.currentWidget().root_pid))
        runner = self.runner
        phase = runner.phase
        connected = runner.connected
        readyish = connected and phase in ("ready", "scanned")
        busy_conn = phase in ("connecting", "login_wait")
        n_sel = len(self._checked_indices())

        # 连接并登录 / 断开（当前账号）
        can_toggle = (phase == "idle") or connected
        self.btn_connect.setEnabled(can_toggle)
        if connected:
            self.btn_connect.setText("断开")
            tip = "断开当前账号并关闭它的 Edge 窗口"
        elif busy_conn:
            self.btn_connect.setText("连接中…")
            tip = "正在连接或等待人工登录，完成后才能再次操作"
        else:
            self.btn_connect.setText("连接并登录")
            tip = "启动当前账号的 Edge 窗口并登录 U校园"
        self.btn_connect.setToolTip(tip)

        # 任务类按钮（当前账号）
        self.btn_scan.setEnabled(readyish)
        self.btn_scan.setToolTip("" if readyish else "请先连接并登录当前账号，再扫描任务列表")

        current_page = self.cb_task.currentData() == 'current'
        can_process = readyish and (n_sel > 0 or current_page)
        self.cb_task.setEnabled(phase not in _BUSY_PHASES)
        self.cb_skip_finished.setEnabled(phase not in _BUSY_PHASES)
        for _, cb in self._boxes:
            cb.setEnabled(phase not in _BUSY_PHASES)
        self.btn_process.setEnabled(can_process)
        if not connected:
            p_tip = "请先连接并登录当前账号"
        elif phase not in ("ready", "scanned"):
            p_tip = "当前账号正在扫描或处理中，请稍候"
        elif n_sel == 0 and not current_page:
            p_tip = "请先勾选要处理的任务"
        else:
            p_tip = "处理当前账号勾选的任务"
        self.btn_process.setToolTip(p_tip)

        self.btn_quick.setEnabled(readyish)
        self.btn_quick.setToolTip(
            "" if readyish else "请先连接并登录当前账号；快速处理针对浏览器当前打开的题目页")

        self.btn_stop.setEnabled(phase == "running")
        self.btn_stop.setToolTip(
            "" if phase == "running" else "当前账号没有正在处理的任务")

        # 全部连接 / 全部停止
        accounts = list(self.store.accounts)
        idle_any = any(self._phase_of(a["id"]) == "idle" for a in accounts)
        running_any = any(self._phase_of(a["id"]) == "running" for a in accounts)
        self.btn_connect_all.setEnabled(bool(accounts) and idle_any)
        if not accounts:
            ca_tip = "还没有账号：请先点「＋ 新增账号」"
        elif not idle_any:
            ca_tip = "所有账号都已在连接或处理中"
        else:
            ca_tip = ("对所有待机账号依次发起连接（每个账号使用独立浏览器会话，"
                      "首次登录需人工验证码，之后自动复用登录会话）")
        self.btn_connect_all.setToolTip(ca_tip)

        any_runner = bool(all_runners())
        self.btn_stop_all.setEnabled(running_any)
        self.btn_stop_all.setToolTip(
            "对所有正在处理的账号发送停止请求" if running_any
            else ("没有账号在处理任务" if any_runner else "还没有可停止的账号"))

        # 运行中禁用配置类控件（控件 + 文字一起弱化靠 QSS:disabled）
        editable = phase == "idle"
        for w in (self.ed_user, self.ed_pass, self.ed_token, self.btn_save):
            w.setEnabled(editable)
        conf_tip = "" if editable else "当前账号已连接或在运行中：请先「断开」再修改配置"
        self.ed_user.setToolTip(conf_tip)
        self.ed_pass.setToolTip(conf_tip)
        self.ed_token.setToolTip(conf_tip)
        self.btn_save.setToolTip(conf_tip or "保存当前账号的账密 / token")

        self.acc_bar.btn_add.setEnabled(editable)
        self.acc_bar.btn_add.setToolTip(
            "" if editable else "当前账号在运行中：请先「断开」再新增账号")
        can_remove = editable and len(self.store.accounts) > 1
        self.acc_bar.btn_remove.setEnabled(can_remove)
        if len(self.store.accounts) <= 1:
            rm_tip = "至少保留一个账号"
        elif not editable:
            rm_tip = "当前账号在运行中：请先「断开」再删除"
        else:
            rm_tip = "删除当前账号的档案（会关闭它的 Edge 窗口，不影响其它账号）"
        self.acc_bar.btn_remove.setToolTip(rm_tip)

    # ------------------------------------------------------------------ 日志
    def append_log(self, level: str, text: str):
        bar = self.log.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 6

        fmt = QTextCharFormat()
        fmt.setForeground(QColor(T.LOG_COLORS.get(level, T.TEXT)))
        f = QFont(self.log.font())
        if level in ("ERROR", "CRITICAL"):
            f.setBold(True)
        fmt.setFont(f)

        cur = self.log.textCursor()
        cur.movePosition(QTextCursor.End)
        import time as _t
        stamp = _t.strftime("%H:%M:%S ")
        cur.insertText(stamp + str(text).rstrip() + "\n", fmt)
        self.log.setTextCursor(cur)

        if at_bottom:
            bar.setValue(bar.maximum())

    def showEvent(self, e):
        # 只刷新账号条 / 摘要 / 按钮态：不动账号卡里的输入框，
        # 免得用户切走再切回来时未保存的账密被覆盖。
        super().showEvent(e)
        if self._current and not self.store.get(self._current):
            self._current = str(self.store.active or "")
        for aid in [a["id"] for a in self.store.accounts]:
            self._wire_runner(get_runner(aid))
        self._refresh_bar()
        self._refresh_login_state()
        self.refresh_ai_info()
        self._update_summary()
        self._sync_buttons()

    def _toggle_browser_visible(self):
        visible = not self.browser.isHidden()
        self.browser.set_browser_visible(not visible)
        self.browser_canvas.setVisible(not visible)
        self.btn_browser_visible.setText('显示浏览器' if visible else '隐藏浏览器')

    def _toggle_browser_mode(self):
        if self.browser.isHidden():
            self._toggle_browser_visible()
        session = self.browser.currentWidget()
        if session:
            session.toggle_external()

    def shutdown(self):
        """窗口退出：关掉所有账号的浏览器会话（多账号都要收尾）。"""
        self.browser.release_all()
        try:
            shutdown_all()
        except Exception:
            pass
