# -*- coding: utf-8 -*-
"""主窗口：侧栏导航 + 各页面 + 状态栏 + 日志分发。"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt, Signal, QThread, QTimer
from PySide6.QtGui import (
    QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from gui_app import APP_NAME
from gui_app import __version__ as APP_VERSION
from gui_app import icons as I
from gui_app import profiles
from gui_app import store
from gui_app import theme as T
from gui_app.page_account import AccountPage
from gui_app.page_ai import AiPage
from gui_app.page_courses import CoursesPage
from gui_app.page_live import LivePage
from gui_app.page_profile import ProfilePage
from gui_app.page_run import RunPage
from gui_app.page_settings import SettingsPage
from gui_app.page_unipus import UnipusPage
from gui_app.page_yuketang import YuketangPage
from gui_app.page_zhihuishu import ZhihuishuPage
from gui_app.widgets import Badge
from gui_app.worker import Runner

NAV = [
    ("学习通", [
        ("account", "账号登录", "管理学习通账号", "user"),
        ("courses", "课程列表", "查看并选择课程", "book"),
        ("run", "刷课", "运行与查看学习进度", "play"),
        ("live", "课堂互动", "签到 / 讨论 / 投票 / 随堂练习", "clipboard"),
    ]),
    ("U校园", [
        ("unipus", "U校园", "管理 U校园课程", "unipus"),
    ]),
    ("雨课堂", [
        ("yuketang", "雨课堂", "管理雨课堂课程", "yuketang"),
    ]),
    ("智慧树", [
        ("zhihuishu", "智慧树", "管理智慧树课程", "zhihuishu"),
    ]),
    ("应用设置", [
        ("settings", "参数设置", "配置运行参数", "settings"),
        ("ai", "AI 大模型", "配置 AI 服务", "sparkles"),
    ]),
    ("账户", [
        ("profile", "用户中心", "用户名 / 头像 / 设备", "profile"),
    ]),
]

#: 页面 key -> 所属分组（= 平台名）
PAGE_GROUP = {item[0]: group for group, items in NAV for item in items}
#: 平台名 -> 品牌 key（QSS 属性选择器用这个）
GROUP_BRAND = {"学习通": "chaoxing", "U校园": "unipus",
               "雨课堂": "yuketang", "智慧树": "zhihuishu",
               "应用设置": "settings", "账户": "account"}
#: 品牌 key -> 主色（侧栏分组圆点 + 文字用它）
BRAND_COLOR = {"chaoxing": T.PRIMARY, "unipus": T.UNIPUS,
               "yuketang": T.YUKETANG, "zhihuishu": T.ZHIHUISHU,
               "settings": T.TEXT_WEAK, "account": T.PRIMARY}

#: 顶栏平台切换（v1.6.7）：带品牌色的平台组排在左边，功能组（设置 / 账户）排在右边。
PLATFORM_GROUPS = [g for g, _ in NAV if GROUP_BRAND.get(g) not in ("settings", "account")]
UTILITY_GROUPS = [g for g, _ in NAV if GROUP_BRAND.get(g) in ("settings", "account")]
#: 顶栏标签短名（「应用设置」四个字会把顶栏挤满，显示为「设置」）
GROUP_TAB_LABEL = {"应用设置": "设置"}

#: 扁平页面 key 顺序（= 页面栈下标顺序）
PAGE_KEYS = [item[0] for _group, items in NAV for item in items]
#: key -> 页面栈下标
PAGE_INDEX = {key: i for i, key in enumerate(PAGE_KEYS)}
#: 扁平 (标题, 说明, 图标名) 表，保持历史三元组形状，兼容既有调用方
PAGES = [item[1:] for _group, items in NAV for item in items]


def app_icon() -> QIcon:
    """应用图标（窗口/任务栏/托盘）。

    实现在 `gui_app/brand.py`：内置 SVG，按尺寸分两档渲染（≥32px 用完整品牌笔触，
    16/24px 用同源简化骨架），零资源文件依赖。保留本函数名兼容既有调用方。
    """
    from gui_app.brand import app_icon as _brand_icon
    return _brand_icon()


class MainWindow(QMainWindow):
    sig_line = Signal(str, str)      # level, text
    sig_activity = Signal(str)

    def __init__(self):
        super().__init__()
        self.runner = Runner(self)
        self.book = profiles.load()
        self._ui = store.ui_load()
        # 每个账号一份课程缓存：切换账号时不用重新联网
        self.courses_cache: dict[str, list[dict]] = {}

        self.setWindowTitle(f"{APP_NAME} · 在线课程学习助手")
        self.setWindowIcon(app_icon())
        self.resize(1440, 900)
        self.setMinimumSize(1080, 720)

        central = QWidget()
        central.setObjectName("Root")
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_sidebar())

        right = QWidget()
        right.setObjectName("Root")
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(0)
        rlay.addWidget(self._build_topbar())

        self.stack = QStackedWidget()
        self.stack.setObjectName("MainStack")
        self.account_page = AccountPage(self.book)
        self.courses_page = CoursesPage()
        self.settings_page = SettingsPage()
        self.ai_page = AiPage()
        self.run_page = RunPage()
        self.live_page = LivePage()
        self.unipus_page = UnipusPage()
        self.yuketang_page = YuketangPage()
        self.zhihuishu_page = ZhihuishuPage()
        self.profile_page = ProfilePage()
        # 页面栈**按 PAGE_KEYS 顺序**装配（不再手写一遍顺序）：
        # stack 下标 == PAGE_INDEX[key] 由构造方式保证，插页面/调顺序都不可能错位。
        _pages = {
            "account": self.account_page, "courses": self.courses_page,
            "run": self.run_page, "unipus": self.unipus_page,
            "live": self.live_page,
            "yuketang": self.yuketang_page, "zhihuishu": self.zhihuishu_page,
            "settings": self.settings_page,
            "ai": self.ai_page,
            "profile": self.profile_page,
        }
        for _key in PAGE_KEYS:
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(20, 16, 20, 16)
            wl.addWidget(_pages[_key])
            self.stack.addWidget(wrap)
        rlay.addWidget(self.stack, 1)
        rlay.addWidget(self._build_statusbar())
        outer.addWidget(right, 1)

        self._wire()
        self._restore_geometry()
        self._refresh_accounts()

        # 回到上次停留的页面（首次启动停在第一页）
        last = str(self._ui.get("last_page") or "")
        self.goto(last if last in PAGE_INDEX else PAGE_KEYS[0])
        self.status("就绪")

    # ------------------------------------------------------------ 界面构建
    def _build_sidebar(self) -> QWidget:
        """固定顶端的品牌、平台入口和当前平台子页，剩余空间留在底部。"""
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(176)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(12, 20, 12, 16)
        lay.setSpacing(8)
        brand = QLabel("乐课")
        brand.setObjectName("RailBrand")
        lay.addWidget(brand)
        caption = QLabel("课程工作台")
        caption.setObjectName("RailCaption")
        lay.addWidget(caption)
        lay.addSpacing(18)
        self._group_tabs = {}
        for group in PLATFORM_GROUPS + UTILITY_GROUPS:
            btn = QPushButton(GROUP_TAB_LABEL.get(group, group))
            btn.setObjectName("RailTab")
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.StrongFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(44)
            btn.clicked.connect(lambda _=False, g=group: self._goto_group(g))
            self._group_tabs[group] = btn
            lay.addWidget(btn)
        lay.addSpacing(18)
        self.rail_section = QLabel("当前工作区")
        self.rail_section.setObjectName("RailCaption")
        lay.addWidget(self.rail_section)

        # ---- 导航（每项带矢量图标，选中后整项变品牌色）----
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setFocusPolicy(Qt.StrongFocus)
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # 行号 → key / key → 行号：导航里混了「分组标题」行，
        # currentRow() 不等于页面栈下标，一切跳转都走映射（见 goto）。
        # 分组标题行保留在 nav 里（页面键↔行号要保持全量），但过滤后一律隐藏 ——
        # 分组现在由顶栏平台标签表达，侧栏不再重复。
        self._nav_items: dict[str, QListWidgetItem] = {}
        self._row_key: dict[int, str] = {}
        self._key_row: dict[str, int] = {}
        self._group_rows: dict[str, list[int]] = {}
        row = 0
        for group, items in NAV:
            head = QListWidgetItem(group)
            head.setFlags(Qt.NoItemFlags)      # 标题行不可点、不可选、不进键盘导航
            brand = GROUP_BRAND.get(group, "settings")
            color = BRAND_COLOR.get(brand, T.TEXT_WEAK)
            if brand != "settings":
                head.setIcon(I.icon("dot", color, 9))
            head.setForeground(QColor(color))
            self.nav.addItem(head)
            row += 1
            rows: list[int] = []
            for key, title, _desc, icon_name in items:
                it = QListWidgetItem(title)
                it.setIcon(I.icon(icon_name, T.TEXT_SUB, 18))
                it.setData(Qt.UserRole, key)
                hot = PAGE_INDEX[key] + 1
                it.setToolTip(f"{title}　Ctrl+{hot}")
                self.nav.addItem(it)
                self._nav_items[key] = it
                self._row_key[row] = key
                self._key_row[key] = row
                rows.append(row)
                row += 1
            self._group_rows[group] = rows
        # 单页平台隐藏子导航时，底部 stretch 仍吸收余量，避免 Qt
        # 将空余高度分给品牌标签与导航按钮、把整个侧栏拉散。
        lay.addWidget(self.nav)
        lay.addStretch(1)
        self.btn_support_author = QPushButton("♡  支持作者 · 永久免费")
        self.btn_support_author.setMinimumHeight(40)
        self.btn_support_author.setToolTip("自愿支持作者，不影响任何功能或使用权限")
        self.btn_support_author.setStyleSheet(
            "QPushButton { background:#EEF3FF; color:#2448AA; border:1px solid #CCD9FF;"
            "border-radius:10px; font-weight:600; padding:8px; }"
            "QPushButton:hover { background:#E2EBFF; border-color:#7F9FEF; }"
            "QPushButton:focus { border:2px solid #345FE5; }")
        self.btn_support_author.clicked.connect(self._show_support_author)
        lay.addWidget(self.btn_support_author)
        self.btn_export_diagnostic = QPushButton('导出诊断')
        self.btn_export_diagnostic.setToolTip('导出当前工作区状态和数量，不包含凭据、课程内容或日志。')
        self.btn_export_diagnostic.clicked.connect(self._export_diagnostic)
        lay.addWidget(self.btn_export_diagnostic)
        self.sidebar = side
        return side

    def _show_support_author(self):
        self.goto("settings")
        self.settings_page.seg.set_index(3)

    def _export_diagnostic(self):
        filename, _ = QFileDialog.getSaveFileName(self, '保存工作区诊断',
            'leke-workspace-diagnostic.json', 'JSON (*.json)')
        if not filename:
            return
        try:
            from gui_app.workspace_diagnostics import save_snapshot
            save_snapshot(self, filename)
            self.status('诊断文件已保存，仅包含本地工作区状态。')
        except Exception:
            QMessageBox.warning(self, '导出诊断', '保存失败，请选择可写入的目录后重试。')

    def _refresh_nav_icons(self):
        """选中项图标变色，其余保持中性灰。

        选中色走 NAV_BRAND（v1.6.8 起四平台共用主色青绿，一屏只有一个强调色，
        平台由顶栏标签与图标区分，不再各自一个色相）。
        """
        current = getattr(self, "_current_key", "")
        for key, it in getattr(self, "_nav_items", {}).items():
            icon_name = PAGES[PAGE_INDEX[key]][2]
            if key == current:
                color = T.PRIMARY
            else:
                color = T.TEXT_SUB
            it.setIcon(I.icon(icon_name, color, 18))

    def _sync_nav_brand(self, key: str):
        """把导航整行的选中色切到当前平台的品牌色。

        QSS 的属性选择器只在控件被 polish 时才重算，所以改完属性必须
        unpolish + polish 再 update（只改 property 不刷新是经典坑）。
        """
        icon_name = PAGES[PAGE_INDEX[key]][2]
        brand = icon_name if icon_name in T.NAV_BRAND else ""
        if self.nav.property("brand") == brand:
            return
        self.nav.setProperty("brand", brand)
        self.nav.style().unpolish(self.nav)
        self.nav.style().polish(self.nav)
        self.nav.viewport().update()

    def _sync_platform_brand(self, group: str):
        """把顶栏底部 3px 色条切到当前平台（v1.6.7 去掉了冗余的平台 pill）。

        属性选择器只在控件被 polish 时才重算，所以改完 property 必须
        unpolish + polish（和侧栏选中色是同一个坑）。
        """
        brand = GROUP_BRAND.get(group, "settings")
        widget = self.top_accent
        if widget.property("brand") == brand:
            return
        widget.setProperty("brand", brand)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _apply_group_filter(self, group: str):
        """侧栏只显示当前组的子页；单页组直接收起侧栏。

        分组标题行始终隐藏 —— 分组现在由顶栏标签表达，侧栏不再重复一遍。
        """
        rows = set(self._group_rows.get(group) or [])
        for row in range(self.nav.count()):
            key = self._row_key.get(row)
            self.nav.item(row).setHidden(not (key and row in rows))
        self.sidebar.setVisible(True)
        self.nav.setVisible(len(rows) > 1)
        self.rail_section.setVisible(len(rows) > 1)
        self.nav.setFixedHeight(42 * len(rows) + 8 if len(rows) > 1 else 0)

    def _switch_group(self, group: str):
        """切到某个平台 / 功能组：高亮标签 + 过滤侧栏 + 色条换色。"""
        if group not in self._group_tabs:
            return
        for name, btn in self._group_tabs.items():
            btn.setChecked(name == group)
        self._sync_platform_brand(group)
        self._apply_group_filter(group)

    def _goto_group(self, group: str):
        """顶栏标签点击：跳到该组第一个页面（侧栏与标签跟着联动）。"""
        for key in PAGE_KEYS:
            if PAGE_GROUP.get(key) == group:
                self.goto(key)
                return

    def _build_topbar(self) -> QWidget:
        """顶栏（v1.6.7）：品牌标记 + 页面标题 + 平台切换标签 + 状态徽标。

        平台切换从侧栏上移到顶栏：四个课程平台各占一个标签（选中时染自己的
        品牌色），设置 / 账户作为功能组排在右侧；侧栏只留当前组的子页，
        单页组直接收起侧栏，横向空间全部给内容。
        """
        host = QWidget()
        host.setObjectName("TopBarHost")
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(0, 0, 0, 0)
        host_lay.setSpacing(0)

        bar = QWidget()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(60)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 9, 20, 9)
        lay.setSpacing(0)

        # ---- 品牌标记（图形即品牌，应用名交给窗口标题栏）----
        logo = QLabel()
        logo.setObjectName("BrandMark")
        logo.setFixedSize(30, 30)
        logo.setToolTip(f"{APP_NAME} · 在线课程学习助手")
        try:
            from gui_app.brand import render_pixmap, svg_for
            dpr = self.devicePixelRatioF() or 1.0
            px = max(1, int(round(30 * dpr)))
            pm = render_pixmap(svg_for(64), px, px)
            pm.setDevicePixelRatio(dpr)
            logo.setPixmap(pm)
        except Exception:
            # 兜底：退回文字标记（QSS 里仍留了对应样式）
            logo.setText("乐")
            logo.setAlignment(Qt.AlignCenter)
            logo.setProperty("textMark", True)
        logo.hide()


        # ---- 当前页面标题（副标题是对侧栏名的同义复述，删掉省一行高度）----
        self.lb_title = QLabel(PAGES[0][0])
        self.lb_title.setObjectName("PageTitle")
        lay.addWidget(self.lb_title, 0, Qt.AlignVCenter)
        lay.addSpacing(18)

        self.badge_login = Badge("未登录", "idle")

        # 刷课状态常驻顶栏：运行中的任务用的是「开始那一刻」的配置快照，
        # 在别的页面改了参数本轮不会生效 —— 不提示的话用户会以为改了个寂寞。
        self.badge_run = Badge("刷课中", "info")
        self.badge_run.setVisible(False)
        self.badge_run.setToolTip(
            "有账号正在刷课。\n"
            "运行中的任务用开始时读到的配置快照，在「参数设置 / AI 大模型」页"
            "临时改的值本轮不会生效，停止后重新开始才会应用。")

        lay.addStretch(1)
        self.btn_study_workspace = QPushButton("学习通刷课")
        self.btn_study_workspace.setCursor(Qt.PointingHandCursor)
        self.btn_study_workspace.setToolTip("打开学习通刷课工作台（Ctrl+3）")
        self.btn_study_workspace.clicked.connect(lambda: self.goto("run"))
        lay.addWidget(self.btn_study_workspace)
        lay.addSpacing(12)
        lay.addWidget(self.badge_run)
        lay.addSpacing(8)
        lay.addWidget(self.badge_login)

        # 顶栏下方 3px 品牌色条：切平台时整条横线换色，比看小图标快得多
        self.top_accent = QFrame()
        self.top_accent.setObjectName("TopAccent")
        self.top_accent.setProperty("brand", "settings")
        self.top_accent.setFixedHeight(1)

        host_lay.addWidget(bar)
        host_lay.addWidget(self.top_accent)
        return host

    def _build_statusbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("StatusBar")
        bar.setFixedHeight(32)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(24, 0, 24, 0)
        lay.setSpacing(14)
        self.lb_status = QLabel("就绪")
        self.lb_status.setObjectName("StatusText")
        self.lb_activity = QLabel("")
        self.lb_activity.setObjectName("StatusActivity")
        self.lb_activity.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.ver_chip = QLabel(f"v{APP_VERSION}")
        self.ver_chip.setObjectName("VersionChip")
        lay.addWidget(self.lb_status)
        lay.addStretch(1)
        lay.addWidget(self.lb_activity, 3)
        lay.addWidget(self.ver_chip)
        return bar

    # ------------------------------------------------------------ 信号连接
    def _wire(self):
        self.yuketang_page.sig_login_changed.connect(self._refresh_platform_login_badge)
        self.unipus_page.sig_login_changed.connect(self._refresh_platform_login_badge)
        self.nav.currentRowChanged.connect(self._on_nav)

        # Ctrl+1..9 直达页面，第 10 页用 Ctrl+0 —— Qt 不认 "Ctrl+10"
        # （QKeySequence("Ctrl+10").toString() 是空串，快捷键会静默失效），
        # 所以序号必须取模成单个数字再拼。
        # nav 设了 NoFocus（避免和表单抢 Tab），没有快捷键的话键盘就完全切不了页
        # —— 这是可访问性硬伤。
        for _i, _key in enumerate(PAGE_KEYS):
            _sc = QShortcut(QKeySequence(f"Ctrl+{(_i + 1) % 10}"), self)
            _sc.setContext(Qt.ApplicationShortcut)
            _sc.activated.connect(lambda _k=_key: self.goto(_k))
            self._shortcuts = getattr(self, "_shortcuts", [])
            self._shortcuts.append(_sc)

        self.account_page.sig_request_login.connect(self._on_login)
        self.account_page.sig_request_logout.connect(self._on_logout)
        self.account_page.sig_accounts_changed.connect(self._refresh_accounts)
        self.account_page.sig_current_changed.connect(self._on_account_switched)

        self.courses_page.sig_request_courses.connect(self._on_load_courses)
        self.courses_page.sig_account_changed.connect(self._on_courses_account_changed)
        self.courses_page.sig_selection_committed.connect(self._on_selection_committed)
        self.courses_page.sig_selection_changed.connect(self._on_selection_changed)
        self.courses_page.sig_chapters_request.connect(self.runner.load_chapters)
        self.runner.sig_chapters_versioned.connect(self.courses_page.on_chapters_versioned)
        self.courses_page.sig_chapters_saved.connect(self._on_chapters_saved)

        self.run_page.sig_start.connect(self._on_start_study)
        self.run_page.sig_stop.connect(lambda: self.runner.request_stop(""))
        # 「从头重刷」与设置页「只处理未完成的任务点」是同一配置，双向同步
        self.run_page.sig_restudy.connect(self._on_restudy_changed)
        self.settings_page.cb_skip.toggled.connect(self._on_skip_changed)

        # AI 页 ↔ 设置页：AI 页不直接碰设置页控件，一律经主窗口中介（v1.5.3）
        self.ai_page.sig_save_requested.connect(self._on_save_ai)
        self.ai_page.sig_enable_requested.connect(
            lambda: self.settings_page.append_ai_provider())
        # 设置页改题库来源后，把「AI 是否已启用」同步给 AI 页第 3 步提示
        self.settings_page.ed_provider.textChanged.connect(self._sync_ai_provider_hint)

        self.runner.sig_login.connect(self._on_login_result)
        self.runner.sig_courses.connect(self._on_courses)
        self.runner.sig_course_progress.connect(self._on_course_progress)
        self.runner.sig_study_total.connect(self.run_page.set_total)
        self.runner.sig_study_pass.connect(self.run_page.set_pass)
        self.runner.sig_study_update.connect(self.run_page.update_progress)
        self.runner.sig_study_current.connect(self.run_page.set_current)
        self.runner.sig_study_totals.connect(self._on_study_totals)
        self.runner.sig_state.connect(self._on_state)
        self.runner.sig_status.connect(self.status)
        self.runner.sig_finished.connect(self._on_finished)

        self.sig_line.connect(self.run_page.append_log)
        self.runner.sig_video_progress.connect(self.run_page.set_video_progress)
        self.sig_activity.connect(self._set_activity)

        # 用户中心：纯乐课账号域，不碰课程平台凭据
        self.profile_page.sig_status.connect(self.status)
        self.profile_page.sig_profile_changed.connect(self._on_kc_profile_changed)
        self.profile_page.sig_logout.connect(self._on_kc_logout)

    # ------------------------------------------------------------ 日志桥
    def on_stream_line(self, raw: str):
        """由 StreamProxy 在任意线程调用 —— 只做信号转发。"""
        from gui_app.stream import parse_level, strip_ansi
        text = strip_ansi(raw).rstrip()
        if not text:
            return
        self.sig_line.emit(parse_level(text), text)

    def on_stream_activity(self, text: str):
        self.sig_activity.emit(text)

    def _set_activity(self, text: str):
        if len(text) > 120:
            text = "…" + text[-119:]
        self.lb_activity.setText(text)

    # ------------------------------------------------------------ 导航
    def _on_nav(self, row: int):
        key = self._row_key.get(row)
        if not key:
            return                      # 分组标题行（不可选）——忽略
        self._current_key = key
        index = PAGE_INDEX[key]
        self.stack.setCurrentIndex(index)
        self.lb_title.setText(PAGES[index][0])
        self._sync_nav_brand(key)
        self._sync_platform_brand(PAGE_GROUP.get(key, ""))
        self._refresh_nav_icons()
        self._refresh_platform_login_badge()
        # 用户中心：每次进来重新拉一次 /me，网站那边改的用户名/头像立刻可见
        if key == "profile":
            try:
                self.profile_page.refresh()
            except Exception:
                pass
        # 记住最后停留的页面，下次启动直接回到这里
        try:
            store.ui_save({"last_page": key})
        except Exception:
            pass

    def goto(self, key: str):
        """跳到指定页面。

        key 用 NAV 里的字符串（如 "run"/"courses"/"yuketang"）。
        为兼容历史调用也接受 int，但 int 指的是**扁平 PAGE_KEYS 下标**，
        不是旧 PAGES 的顺序 —— 新代码一律传 key，别再传数字。
        """
        if isinstance(key, int):
            key = PAGE_KEYS[key] if 0 <= key < len(PAGE_KEYS) else PAGE_KEYS[0]
        key = str(key)
        row = self._key_row.get(key)
        if row is None:
            return
        # 先切组（高亮顶栏标签 + 过滤侧栏），再选中行 ——
        # 否则目标行可能还处于「上一组的隐藏态」，setCurrentRow 会落到空处。
        self._switch_group(PAGE_GROUP.get(key, ""))
        # 使用统计：只记页面切换动作，失败静默（P3 看板数据源）
        try:
            from api import kc_client

            kc_client.track("nav", "goto_page", extra={"page": key})
        except Exception:
            pass
        self.nav.setCurrentRow(row)
        self.nav.scrollToItem(self.nav.item(row))

    def status(self, text: str):
        self.lb_status.setText(text)

    # ------------------------------------------------------------ 账号
    def _account_name(self, acc_id: str) -> str:
        acc = self.book.get(acc_id)
        return self.book.display_name(acc) if acc else acc_id

    def _refresh_accounts(self):
        """账号增删 / 改名后，刷新各页面的账号选择器。"""
        accounts = [(str(a.get("id")), self.book.display_name(a))
                    for a in self.book.accounts]
        current = self.book.active or (accounts[0][0] if accounts else "")
        self.courses_page.set_accounts(accounts, current)
        self.run_page.set_accounts(accounts)
        self.courses_page.set_saved(self.book.courses(current))
        self._restore_courses(current)

        logged = sum(1 for a in self.book.accounts
                     if self.runner.is_logged_in(str(a.get("id"))))
        if logged:
            self.badge_login.set_kind("ok", f"{logged}/{len(accounts)} 个账号已登录")
        else:
            self.badge_login.set_kind("idle", "未登录")
        self._refresh_run_badge()
        self._refresh_platform_login_badge()

    def _refresh_platform_login_badge(self):
        if getattr(self, '_current_key', '') == 'unipus':
            from gui_app.unipus_engine import all_runners
            accounts = self.unipus_page.store.accounts
            runners = all_runners()
            connected = sum(bool(runners.get(a['id']) and runners[a['id']].connected) for a in accounts)
            self.badge_login.set_kind('ok' if connected else 'idle',
                f'{connected}/{len(accounts)} 个账号已连接' if connected else '未连接')
        elif getattr(self, '_current_key', '') == 'yuketang':
            accounts = self.yuketang_page._store.accounts
            logged = sum(bool(account.get('cookie')) for account in accounts)
            self.badge_login.set_kind('ok' if logged else 'idle',
                f'{logged}/{len(accounts)} 个账号已登录' if logged else '未登录')
        else:
            logged = sum(self.runner.is_logged_in(str(a.get('id'))) for a in self.book.accounts)
            self.badge_login.set_kind('ok' if logged else 'idle',
                f'{logged}/{len(self.book.accounts)} 个账号已登录' if logged else '未登录')

    def _on_account_switched(self, acc_id: str):
        """账号页切换了当前账号 —— 课程页跟着切。"""
        self.book.active = acc_id
        self.book.save()
        idx = self.courses_page.cb_account.findData(acc_id)
        if idx >= 0:
            self.courses_page.set_saved(self.book.courses(acc_id))
            self.courses_page.cb_account.setCurrentIndex(idx)
            self._restore_courses(acc_id)

    def _on_login(self, acc_id: str, payload: dict, also_courses: bool):
        self.book.update(acc_id, name=payload.get("name", ""),
                         username=payload.get("username", ""),
                         password=payload.get("password", ""),
                         use_cookies=bool(payload.get("use_cookies", False)))
        self.account_page.update_accounts()
        self.run_page.set_accounts(
            [(str(a.get("id")), self.book.display_name(a)) for a in self.book.accounts])
        self._pending_courses_account = acc_id if also_courses else ""
        self.runner.login(acc_id, payload)

    def _on_restudy_changed(self, checked: bool):
        """刷课页勾了「从头重刷」→ 反向同步设置页勾选框（仅视图，不触发保存）。"""
        cb = self.settings_page.cb_skip
        cb.blockSignals(True)
        cb.setChecked(not checked)
        cb.blockSignals(False)

    def _on_skip_changed(self, checked: bool):
        """设置页改了「只处理未完成的任务点」→ 同步刷课页（仅视图）。"""
        self.run_page.set_restudy(not checked)

    def _on_logout(self, acc_id: str):
        self.runner.logout(acc_id)
        self.account_page.set_account_state(acc_id, "idle")
        self._refresh_accounts()

    # ------------------------------------------------------------ 用户中心（乐课账号）
    def _on_kc_profile_changed(self, me: dict):
        """用户名/头像改了 —— 标题栏与状态栏跟着换，别让用户以为没生效。"""
        name = me.get("display_name") or me.get("username") or ""
        if name:
            self.setWindowTitle(f"{APP_NAME} · {name}")
        self.status(f"用户名已更新为「{name}」")

    def _on_kc_logout(self):
        """退出乐课账号：清本地登录态后关程序（下次启动回到登录闸门）。"""
        self.profile_page.apply_logout()
        QMessageBox.information(self, "已退出登录",
                                "已退出乐课账号，程序将关闭。\n下次启动需要重新登录。")
        self.close()

    def _on_login_result(self, acc_id: str, ok: bool, msg: str, realname: str):
        if ok:
            # 先把学习通用户名落库，再刷新界面 —— 否则列表刷出来还是旧名字
            self.book.mark_logged_in(acc_id, realname)
        self.account_page.on_login_result(acc_id, ok, msg, realname)
        self._refresh_accounts()
        if ok:
            self.status(f"{self._account_name(acc_id)}：登录成功")
            if getattr(self, "_pending_courses_account", "") == acc_id:
                self._pending_courses_account = ""
                self.goto("courses")
                idx = self.courses_page.cb_account.findData(acc_id)
                if idx >= 0:
                    self.courses_page.cb_account.setCurrentIndex(idx)
                self._on_load_courses(True)
        else:
            self.status(f"{self._account_name(acc_id)}：登录失败 —— {msg}")

    # ------------------------------------------------------------ 课程
    def _restore_courses(self, acc_id: str):
        """把已缓存的课程灌回课程页（切账号时用）。"""
        if acc_id != self.courses_page.current_account():
            return
        cache = self.courses_cache.get(acc_id)
        if cache:
            self.courses_page.fill(cache)

    def _on_courses_account_changed(self, acc_id: str):
        self.book.active = acc_id
        self.book.save()
        self.courses_page.set_saved(self.book.courses(acc_id))
        if acc_id in self.courses_cache:
            self.courses_page.fill(self.courses_cache[acc_id])
        elif self.runner.is_logged_in(acc_id):
            self._on_load_courses(True)
        else:
            self.status(f"{self._account_name(acc_id)}：尚未登录，请先到账号页登录")

    def _on_load_courses(self, with_progress: bool = True, *, force_refresh: bool = False):
        acc_id = self.courses_page.current_account()
        if not acc_id:
            QMessageBox.information(self, "还没有账号",
                                    "请先在「账号登录」页面添加并登录账号。")
            self.goto("account")
            return
        if not self.runner.is_logged_in(acc_id):
            QMessageBox.information(
                self, "尚未登录",
                f"账号「{self._account_name(acc_id)}」还没有登录。\n\n"
                "请先在「账号登录」页面登录它。")
            self.goto("account")
            return
        self.courses_page.set_loading(True)
        self.lb_activity.setText("")
        self.runner.load_courses(acc_id, bool(with_progress), force_refresh=force_refresh)

    def _on_courses(self, acc_id: str, courses: list):
        self.courses_cache[acc_id] = list(courses)
        if acc_id == self.courses_page.current_account():
            self.courses_page.fill(courses)
            self._restore_chapter_selections(acc_id)

    def _restore_chapter_selections(self, acc_id: str):
        """把档案里的章节选择恢复到课程页按钮文案上。"""
        for c in self.courses_cache.get(acc_id) or []:
            cid = str(c.get("courseId", ""))
            self.courses_page.set_chapter_selection(cid, self.book.chapters(acc_id, cid))

    def _on_chapters_saved(self, acc_id: str, course_id: str, point_ids):
        self.book.set_chapters(acc_id, course_id, point_ids)
        n = len(point_ids) if point_ids else 0
        name = next((c.get("title", "") for c in self.courses_cache.get(acc_id) or []
                     if str(c.get("courseId", "")) == course_id), course_id)
        if point_ids:
            self.status(f"{self._account_name(acc_id)}：「{name}」将只刷选中的 {n} 个章节")
        else:
            self.status(f"{self._account_name(acc_id)}：「{name}」恢复刷全部章节")

    def _on_course_progress(self, acc_id: str, row: int, info: dict):
        courses = self.courses_cache.get(acc_id) or []
        if row < 0 or row >= len(courses):
            return
        course = courses[row]
        if info.get('course_id') is not None and (
                str(course.get('courseId')) != info['course_id']
                or str(course.get('clazzId')) != info.get('clazz_id')):
            return
        course['_progress'] = dict(info)
        if acc_id == self.courses_page.current_account():
            self.courses_page.update_progress(row, info)

    def _on_selection_changed(self, selected: list):
        n = len(selected)
        acc_id = self.courses_page.current_account()
        if n:
            self.status(f"{self._account_name(acc_id)}：已选择 {n} 门课程")
        elif self.courses_cache.get(acc_id):
            self.status(f"{self._account_name(acc_id)}：未选择课程"
                        f"（开始刷课时将处理全部 {len(self.courses_cache[acc_id])} 门）")

    def _on_selection_committed(self, acc_id: str, course_ids: list):
        if acc_id:
            self.book.set_courses(acc_id, course_ids)

    # ------------------------------------------------------------ 刷课
    def _courses_for(self, acc_id: str) -> list[dict]:
        """确定某个账号这次要处理哪些课程（含每门课的章节选择）。"""
        cache = self.courses_cache.get(acc_id) or []
        if not cache:
            return []
        if self.courses_page.current_account() == acc_id:
            sel = self.courses_page.selected_courses()
        else:
            sel = []
        if not sel:
            saved = set(self.book.courses(acc_id))
            sel = [c for c in cache if str(c.get("courseId", "")) in saved] or list(cache)
        # 章节选择：有选择才挂 selected_points（worker 按它过滤）
        out = []
        for c in sel:
            cc = dict(c)
            ids = self.book.chapters(acc_id, str(c.get("courseId", "")))
            if ids:
                cc["selected_points"] = ids
            out.append(cc)
        return out

    def _on_start_study(self, acc_ids: list):
        if not acc_ids:
            QMessageBox.information(self, "还没有选择账号",
                                    "请先勾选要刷课的账号。")
            return

        ready = [a for a in acc_ids if self.runner.is_logged_in(a)]
        skipped = [a for a in acc_ids if a not in ready]
        if not ready:
            QMessageBox.information(
                self, "尚未登录",
                "选中的账号都还没有登录。\n\n请先在「账号登录」页面登录。")
            self.goto("account")
            return
        for a in skipped:
            self.run_page.append_log(
                "WARNING", f"跳过 {self._account_name(a)}：尚未登录")

        common = self.settings_page.current_common()
        tiku = self.settings_page.current_tiku()
        # AI 字段（endpoint/key/model 等）现在归「AI 大模型」页管（v1.5.3）
        tiku.update(self.ai_page.collect_ai())

        if common.get("notopen_action") == "ask":
            common["notopen_action"] = "retry"
            self.run_page.append_log(
                "INFO", "「询问」模式只在命令行下有效，桌面版已自动按「重试」处理")

        jobs: list[tuple[str, list]] = []
        for acc_id in ready:
            courses = self._courses_for(acc_id)
            if not courses:
                self.run_page.append_log(
                    "WARNING",
                    f"跳过 {self._account_name(acc_id)}：还没有课程数据，"
                    "请先到「课程列表」页读取")
                continue
            jobs.append((acc_id, courses))

        if not jobs:
            QMessageBox.information(
                self, "还没有课程数据",
                "没有可处理的课程。\n\n请先到「课程列表」页面读取课程，"
                "并勾选要处理的课程。")
            self.goto("courses")
            return

        if not tiku.get("provider"):
            r = QMessageBox.question(
                self, "未配置答题来源",
                "你还没有配置答题来源（章节测验会因此被跳过，"
                "可能导致后续章节无法解锁）。\n\n仍要继续吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                self.goto("settings")
                return

        # 配了题库但全都是「缺 token / 缺地址」的 → 等于没有题库。
        # 提前说清楚，别让用户刷完一轮才发现章节检测被整体跳过（v1.5.7）
        warn = self._tiku_ready_warning(tiku)
        if warn:
            r = QMessageBox.question(
                self, "题库配置不完整",
                warn + "\n\n现在开跑的话，章节检测会被整体跳过（后续章节可能无法解锁）。"
                       "仍要继续吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                self.goto("settings")
                return

        _vm = str(common.get("video_mode", "fast") or "fast").lower()
        self.run_page.set_mode_hint(
            ("真实节奏" if _vm == "timed" else "快速完成") + " · 自动续刷")
        self.run_page.append_log(
            "INFO",
            f"===== 开始任务：{len(jobs)} 个账号并行，"
            f"倍速 {common.get('speed')}，并发 {common.get('jobs')} =====")
        self.run_page.append_log(
            "INFO",
            "视频模式：" + ("真实节奏（任务点 + 学习时长）" if _vm == "timed"
                        else "快速完成（只刷任务点，平台不计学习时长）"))
        self.run_page.append_log(
            "INFO",
            "刷课方式：自动续刷（每一遍重新读取章节列表，把新解锁的章节接着处理，"
            "连续两遍无新进展即停）")
        if not common.get("skip_finished", True):
            self.run_page.append_log(
                "INFO",
                "已开启「从头重刷」：已完成章节也会重新处理一遍"
                + ("（真实节奏模式下会从头重播、补学习时长）" if _vm == "timed"
                   else "（注意：当前是快速完成模式，只刷任务点、平台不计学习时长）"))
        elif _vm == "timed":
            self.run_page.append_log(
                "INFO",
                "当前只处理未完成的任务点，已完成视频会被跳过。"
                "要补学习时长请勾选上方「从头重刷（含已完成章节）」。")

        for acc_id, courses in jobs:
            hint = "（含已完成章节）" if not common.get("skip_finished", True) else ""
            self.run_page.append_log(
                "INFO",
                f"→ {self._account_name(acc_id)}：{len(courses)} 门课程{hint}")
            chap = [(c.get("title", ""), len(c["selected_points"]))
                    for c in courses if c.get("selected_points")]
            if chap:
                self.run_page.append_log(
                    "INFO",
                    f"   章节选择："
                    + "；".join(f"「{t}」只刷 {n} 章" for t, n in chap))
            self.run_page.reset(acc_id)
            self.run_page.set_cumulative(acc_id, 0, 0)
            self.runner.start_study(acc_id, courses, common, tiku)

        self.run_page.sync_run_state(True)
        self.goto("run")

    # ------------------------------------------------------------ AI 页中介
    def _sync_ai_provider_hint(self, *_):
        """题库来源变化后，同步 AI 页第 3 步的「是否已启用」提示。"""
        self.ai_page.set_provider_hint("AI" in self.settings_page.provider_names())

    def _on_save_ai(self):
        """AI 页点「保存并使用」：AI 字段 + 启用后的 provider 一次性落盘。

        旧版 `_save_ai_config` 先用旧 provider 写盘再启用，导致启用后的
        provider 没落盘（v1.5.3 顺手修正）；这里先改设置页 provider 再合并写盘。
        """
        self.settings_page.append_ai_provider()
        updates = self.settings_page.provider_collect()
        updates.update(self.ai_page.values())
        try:
            changed = store.update_values(updates)
        except Exception as e:
            self.ai_page.notify_saved(False, f"保存失败：{type(e).__name__}: {e}")
            return
        # 保存后把最新值重新读一遍，确认真的落到文件里
        fresh = store.get("tiku", "endpoint", "")
        ok = fresh.strip() == str(updates.get(("tiku", "endpoint"), "")).strip()
        self.ai_page.notify_saved(
            ok,
            "已保存到 config.ini，题库来源已启用 AI" if ok else "写入后回读不一致，请检查 config.ini",
        )
        self._sync_ai_provider_hint()
        self.status("AI 配置已保存")

    # ------------------------------------------------------------ 题库预检
    @staticmethod
    def _tiku_ready_warning(tiku: dict) -> str:
        """开跑前检查「配了题库但一项都跑不起来」的情况，返回提示（空 = 没问题）。

        与 `api.answer.Tiku._ready_check` 同源规则：远端题库缺 token / 服务地址、
        AI / 硅基流动缺密钥时会在发请求前被停用 —— 这里提前告知用户，
        免得刷完一轮才发现章节检测被整体跳过（v1.5.7）。
        """
        provs = [p.strip() for p in str(tiku.get("provider") or "").split(",") if p.strip()]
        if not provs:
            return ""      # 完全没配题库：另有专门的「未配置答题来源」询问
        bad: list[str] = []
        if any(p in ("TikuYanxi", "TikuLike") for p in provs) \
                and not str(tiku.get("tokens") or "").strip():
            bad.append("言溪 / LIKE 缺 token")
        if "TikuAdapter" in provs and not str(tiku.get("url") or "").strip():
            bad.append("TikuAdapter 缺服务地址")
        if "SiliconFlow" in provs and not str(tiku.get("siliconflow_key") or "").strip():
            bad.append("硅基流动缺 key")
        if "AI" in provs and not str(tiku.get("key") or "").strip():
            bad.append("AI 缺密钥")
        # 只有「一个能用的都不剩」才算整体跳过（有 TikuLocal / TikuGo 兜底就照跑）
        usable = len(provs) - len(bad)
        if bad and usable <= 0:
            return "题库来源里没有一项配置完整：" + "、".join(bad) + "。"
        return ""

    def _on_study_totals(self, acc_id: str, info: dict):
        """累计统计（每遍结束时汇总一次）。"""
        if not isinstance(info, dict):
            return
        self.run_page.set_cumulative(
            acc_id, info.get("total_done", 0), info.get("failed", 0))
        ps = int(info.get("pass", 0) or 0)
        if ps > 0:
            self.status(f"{self._account_name(acc_id)}：第 {ps} 遍结束，"
                        f"累计完成 {info.get('total_done', 0)} 个章节")

    def _refresh_run_badge(self):
        """顶栏「N 个账号刷课中」提示（任何页面都能看到运行态）。"""
        n = sum(1 for a in self.book.accounts
                if self.runner.is_studying(str(a.get("id"))))
        if n:
            self.badge_run.set_kind("info", f"{n} 个账号刷课中")
            self.badge_run.setVisible(True)
        else:
            self.badge_run.setVisible(False)

    def _on_state(self, acc_id: str, state: str):
        self._refresh_run_badge()
        if state == "logging":
            self.account_page.set_busy(True, acc_id)
        elif state == "courses":
            if acc_id == self.courses_page.current_account():
                self.courses_page.set_loading(True)
        elif state == "studying":
            self.account_page.set_account_state(acc_id, "studying")
            card = self.run_page.cards.get(acc_id)
            if card:
                card.set_state("running")
            self.run_page.sync_run_state(self.runner.studying)
        elif state == "stopping":
            self.run_page.set_stopping(acc_id)
        elif state == "idle":
            self.account_page.set_busy(False, acc_id)
            if acc_id == self.courses_page.current_account():
                self.courses_page.set_loading(False)
            st = self.runner.session(acc_id)
            if not st.studying:
                self.account_page.set_account_state(
                    acc_id, "logged_in" if st.logged_in else "idle")
            self.run_page.sync_run_state(self.runner.studying)

    def _on_finished(self, acc_id: str, kind: str, status: str, msg: str):
        if kind == "courses":
            if acc_id == self.courses_page.current_account():
                self.courses_page.set_loading(False)
                self.lb_activity.setText("")
        elif kind == "study":
            if acc_id == self.courses_page.current_account():
                self.courses_page.invalidate_chapter_cache()
            for course in self.courses_cache.get(acc_id) or []:
                course.pop('_progress', None)
            if acc_id == self.courses_page.current_account() and self.runner.is_logged_in(acc_id):
                self._on_load_courses(True, force_refresh=True)
            self.run_page.set_finished(acc_id, status, msg)
            if status == "ok":
                self.run_page.append_log("SUCCESS", f"===== {msg} =====")
            elif status == "stopped":
                self.run_page.append_log("WARNING", f"===== {msg} =====")
            else:
                self.run_page.append_log("ERROR", f"===== {msg} =====")
            self.run_page.sync_run_state(self.runner.studying)
            self._refresh_run_badge()

    # ------------------------------------------------------------ 窗口
    def _restore_geometry(self):
        geo = self._ui.get("geometry") or ""
        if geo:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
            except Exception:
                pass

    def closeEvent(self, event):
        # Qt forbids destroying a QThread while its network request is running.
        # Stop periodic refreshes and wait for bounded requests to return.
        live = getattr(self, 'live_page', None)
        from gui_app.ai_panel import TestWorker
        pending = [task for task in self.findChildren(TestWorker) if task.isRunning()]
        if live is not None:
            watch = getattr(live, '_watch', None)
            if watch is not None:
                watch.stop()
            pending.extend(task for task in live.findChildren(QThread) if task.isRunning())
        if pending:
            for task in pending:
                task.requestInterruption()
            self.status('正在等待连接结束，请稍候…')
            if not getattr(self, '_close_retry', None):
                self._close_retry = QTimer(self)
                self._close_retry.setSingleShot(True)
                self._close_retry.timeout.connect(self.close)
            self._close_retry.start(250)
            event.ignore()
            return
        if self.runner.studying:
            r = QMessageBox.question(
                self, "任务仍在运行",
                "还有账号在刷课。\n\n确定要退出吗？当前任务点会被中断。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                event.ignore()
                return
        # 平台页各自收尾 + 兜底：各平台都改成了「每账号一个 runner」，
        # 页面 shutdown() 未必覆盖到全部实例（例如账号已被删、页面刚重建），
        # 所以再直接调一次模块级 shutdown_all()，确保没有残留的后台线程。
        for page in (self.unipus_page, self.yuketang_page, self.zhihuishu_page):
            try:
                page.shutdown()
            except Exception:
                pass
        for module_name in ("gui_app.unipus_engine", "gui_app.yuketang_engine",
                            "gui_app.zhihuishu_engine"):
            try:
                module = __import__(module_name, fromlist=["shutdown_all"])
                shutdown_all = getattr(module, "shutdown_all", None)
                if callable(shutdown_all):
                    shutdown_all()
            except Exception:
                pass
        try:
            store.ui_save({
                "geometry": self.saveGeometry().toBase64().data().decode(),
            })
        except Exception:
            pass
        event.accept()
