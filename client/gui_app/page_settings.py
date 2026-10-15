# -*- coding: utf-8 -*-
"""设置页：运行参数 / 题库 / 高级 —— 读写 config.ini（保留注释）。

AI 大模型配置独立在「AI 大模型」页（gui_app/page_ai.py）；
通知设置已移除（v1.5.3：实际使用率低，且远端推送存在隐私顾虑）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from gui_app import store
from gui_app.widgets import Badge, Card, Divider, SegmentedBar

# 默认使用云端共享题库；第三方题库和 AI 按需启用。
PROVIDER_PRESET_GROUPS = [
    ("推荐：开箱即用（无需配置）", [
        ("官方聚合题库 + 免费源 + 云端共享题库 + AI 兜底（推荐）", "TikuAdapter,TikuGo,TikuLocal,AI"),
        ("云端共享题库 + AI（未命中的题由 AI 补答并沉淀）", "TikuLocal,AI"),
    ]),
    ("其他来源（按需选用）", [
        ("AI 大模型（直连，答案存入云端题库）", "AI"),
        ("硅基流动 SiliconFlow（直连大模型）", "SiliconFlow"),
        ("云端共享题库 → AI（登录乐课账号使用）", "TikuLocal"),
        ("言溪题库", "TikuYanxi"),
        ("LIKE 知识库（题目上传 datam.site，不推荐）", "TikuLike"),
        ("TikuAdapter（自定义自建服务）", "TikuAdapter"),
        ("GO 题（网课小工具）", "TikuGo"),
    ]),
    ("组合回退示例", [
        ("GO → 言溪 → LIKE 依次回退", "TikuGo,TikuYanxi,TikuLike"),
        ("言溪 → 云端题库 → AI 依次回退", "TikuYanxi,TikuLocal,AI"),
    ]),
]

def _scroll(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(inner)
    sa.setFrameShape(QScrollArea.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return sa


def _spin(rng=(0, 100), value=0, step=1, suffix="", decimals=0) -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setRange(*rng)
    sp.setSingleStep(step)
    sp.setDecimals(decimals)
    sp.setValue(value)
    if suffix:
        sp.setSuffix(suffix)
    sp.setMinimumHeight(32)
    sp.setMaximumWidth(160)
    return sp


def _ispin(rng=(0, 100), value=0, suffix="") -> QSpinBox:
    sp = QSpinBox()
    sp.setRange(*rng)
    sp.setValue(value)
    if suffix:
        sp.setSuffix(suffix)
    sp.setMinimumHeight(32)
    sp.setMaximumWidth(160)
    return sp


def _line(placeholder="", password=False, width=None) -> QLineEdit:
    ed = QLineEdit()
    ed.setPlaceholderText(placeholder)
    ed.setMinimumHeight(32)
    if password:
        ed.setEchoMode(QLineEdit.Password)
    if width:
        ed.setMaximumWidth(width)
    return ed


def _row(*pairs) -> QGridLayout:
    """pairs: (label_text, widget)；label 或 widget 传 None 表示该格留空。"""
    g = QGridLayout()
    g.setHorizontalSpacing(14)
    g.setVerticalSpacing(10)
    for i, (text, w) in enumerate(pairs):
        if text:
            lb = QLabel(text)
            lb.setObjectName("FieldLabel")
            g.addWidget(lb, 0, i * 2)
        if w is not None:
            g.addWidget(w, 0, i * 2 + 1)
    g.setColumnStretch(len(pairs) * 2, 1)
    return g


class SettingsPage(QWidget):
    sig_kb_status = Signal(str)
    sig_diagnostics = Signal(list)
    sig_saved = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        self.seg = SegmentedBar(["运行参数", "题库", "高级"])
        root.addWidget(self.seg)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self._build_basic()
        self._build_tiku()
        self._build_advanced()
        self.sig_diagnostics.connect(self._show_diagnostics)
        self.seg.sig_changed.connect(self.stack.setCurrentIndex)
        self.seg.sig_changed.connect(self._remember_tab)
        # 恢复上次停留的页签，省得每次进来都要重新点一次「题库」
        try:
            last = int(store.ui_load().get("settings_tab", 0))
        except (TypeError, ValueError):
            last = 0
        if 0 <= last < 3:
            self.seg.set_index(last)

        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        self.btn_save = QPushButton("保存设置")
        self.btn_save.setProperty("variant", "primary")
        self.btn_save.setMinimumHeight(34)
        self.btn_save.setMinimumWidth(120)
        self.btn_save.clicked.connect(self.save)
        self.badge = Badge("尚未保存", "idle")
        self.lb_result = QLabel("")
        self.lb_result.setObjectName("CardHint")
        bottom.addWidget(self.btn_save)
        bottom.addWidget(self.badge)
        bottom.addWidget(self.lb_result, 1)
        root.addLayout(bottom)

        #: 加载期间屏蔽「未保存」标记（load() 里逐个 setValue 会触发 changed）
        self._loading = False
        self.load()
        self._wire_dirty()

    def _wire_dirty(self):
        """任何输入变化 → 徽标改「未保存」，避免改完切页静默丢失（v1.5.7）。

        与「AI 大模型」页的 dirty 行为统一：那边有未保存徽标，这边以前没有，
        同一件事两套行为的坑。cb_skip 除外 —— 它与运行页共享、改动即时落盘。
        """
        for w in self.findChildren(QLineEdit):
            w.textChanged.connect(self._mark_dirty)
        for w in self.findChildren(QComboBox):
            w.currentIndexChanged.connect(self._mark_dirty)
        # ⚠️ findChildren 只接单个类型，不接 tuple；QDoubleSpinBox 也不是 QSpinBox 子类
        for w in self.findChildren(QSpinBox):
            w.valueChanged.connect(self._mark_dirty)
        for w in self.findChildren(QDoubleSpinBox):
            w.valueChanged.connect(self._mark_dirty)
        for w in self.findChildren(QCheckBox):
            if w is self.cb_skip:
                continue
            w.toggled.connect(self._mark_dirty)

    def _mark_dirty(self, *_):
        if self._loading:
            return
        self.badge.set_kind("warn", "未保存")
        self.lb_result.setText("有改动还没保存 —— 点「保存设置」写回 config.ini")

    # ================================================================ 基本
    def _build_basic(self):
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(16)

        c1 = Card("运行参数", icon="sliders")
        self.sp_speed = _spin((1.0, 2.0), 1.0, 0.1, " 倍", 1)
        self.sp_speed.setToolTip("上限 2.0：过高容易被平台风控。")
        self.sp_jobs = _ispin((1, 16), 4, " 个")
        self.cb_notopen = QComboBox()
        self.cb_notopen.addItem("自动重试（retry）", "retry")
        self.cb_notopen.addItem("自动跳过（continue）", "continue")
        self.cb_notopen.addItem("询问（命令行专用）", "ask")
        self.cb_notopen.setMinimumHeight(32)
        self.sp_retry = _spin((0.1, 30.0), 1.0, 0.5, " 秒", 1)
        c1.add(_row(("视频倍速", self.sp_speed), ("同时处理的章节数", self.sp_jobs)))

        # 视频学习模式：决定「任务点」和「学习时长」能不能同时拿到。
        # 平台是按「两次上报的真实时间间隔 vs 播放进度增量」算学习时长的，
        # 一次跳到结尾只会拿到任务点，时长几乎为 0。
        self.cb_vmode = QComboBox()
        self.cb_vmode.addItem("快速完成 — 只刷任务点，平台不记学习时长", "fast")
        self.cb_vmode.addItem("真实节奏 — 任务点 + 学习时长（耗时≈视频总长÷倍速）", "timed")
        self.cb_vmode.setMinimumHeight(32)
        c1.add(_row(("视频学习模式", self.cb_vmode), (None, None)))
        self.lb_vmode = QLabel("")
        self.lb_vmode.setObjectName("CardHint")
        self.lb_vmode.setWordWrap(True)
        c1.add(self.lb_vmode)
        self.cb_vmode.currentIndexChanged.connect(self._on_vmode_changed)

        c1.add(_row(("遇到未开启章节", self.cb_notopen), ("失败重试等待", self.sp_retry)))

        self.lb_pass_hint = QLabel(
            "自动续刷：每遍重新读取章节列表，把后续解锁的章节接着处理，"
            "不需要再手动跑第二遍。"
        )
        self.lb_pass_hint.setObjectName("CardHint")
        self.lb_pass_hint.setWordWrap(True)
        c1.add(self.lb_pass_hint)

        self.cb_skip = QCheckBox("只处理未完成的任务点（推荐，已完成的章节会直接跳过统计）")
        c1.add(self.cb_skip)
        lay.addWidget(c1)

        c2 = Card("章节检测", "开启自动重做需要题库支持；部分课程只允许作答一次，请确认后再开。",
                  icon="check-circle")
        self.cb_redo = QCheckBox("答错后自动重做（会向 AI/题库反馈错误答案并重新提交）")
        self.sp_redo = _ispin((1, 10), 3, " 次")
        c2.add(self.cb_redo)
        c2.add(_row(("最大重做次数", self.sp_redo), (None, None)))
        lay.addWidget(c2)

        c3 = Card("章节学习次数", "轮询访问章节以增加「学习次数」，不是任务点，平时可以不开。",
                  icon="refresh")
        self.cb_count = QCheckBox("刷完任务点后，继续增加章节学习次数")
        self.sp_target = _ispin((1, 100000), 100, " 次")
        c3.add(self.cb_count)
        c3.add(_row(("目标总次数", self.sp_target), (None, None)))
        lay.addWidget(c3)

        lay.addStretch(1)
        self.stack.addWidget(_scroll(inner))

    # ================================================================ 题库
    def _build_tiku(self):
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(16)

        head = Card("题库来源", "默认已配置好（官方聚合题库 → 免费源 → 云端共享题库 → AI 兜底，自动回退），"
                                "一般无需改动。多个 provider 用英文逗号分隔，按顺序依次回退；"
                                "答案统一保存到乐课服务器，登录后所有乐课用户共享。留空 = 不使用题库。",
                    icon="search")
        self.ed_provider = _line("TikuAdapter,TikuGo,TikuLocal,AI（默认，无需改动）")
        btn_menu = QPushButton("常用")
        btn_menu.setMinimumHeight(32)
        menu = QMenu(btn_menu)
        for group_name, items in PROVIDER_PRESET_GROUPS:
            # 用禁用 action 当分组标题（addSection 在部分样式下只画分隔线）
            sep = menu.addAction(group_name)
            sep.setEnabled(False)
            for label, val in items:
                menu.addAction(label, lambda checked=False, v=val: self.ed_provider.setText(v))
        menu.addSeparator()
        menu.addAction("清空（不使用题库）", lambda: self.ed_provider.setText(""))
        btn_menu.setMenu(menu)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.ed_provider, 1)
        row.addWidget(btn_menu)
        head.body.addLayout(row)
        self.ed_provider.textChanged.connect(self._refresh_groups)
        lay.addWidget(head)

        # ---- 本地知识库（v1.5.1f）：程序自带的答案沉淀库，多账号共享 ----
        self.card_kb = Card(
            "云端共享题库",
            "所有乐课用户共享同一份服务器题库。题库 / AI 的答案自动存入云端，"
            "平台批改确认的答案优先；已有本地题库可导入，原文件保留为备份。",
            icon="book")
        self.lb_kb_stat = QLabel("")
        self.lb_kb_stat.setObjectName("CardHint")
        self.lb_kb_stat.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.card_kb.add(self.lb_kb_stat)
        row_kb = QHBoxLayout()
        row_kb.setSpacing(8)
        self.sig_kb_status.connect(self.lb_kb_stat.setText)
        self._kb_busy = False
        b_kb_refresh = QPushButton("刷新云端题库")
        b_kb_refresh.setMinimumHeight(32)
        b_kb_refresh.clicked.connect(lambda: self._refresh_kb_stat(force=True))
        row_kb.addWidget(b_kb_refresh)
        b_kb_import = QPushButton("导入旧本地题库")
        b_kb_import.setMinimumHeight(32)
        b_kb_import.clicked.connect(lambda: self._refresh_kb_stat(force=True, migrate=True))
        row_kb.addWidget(b_kb_import)
        row_kb.addStretch(1)
        self.card_kb.add(row_kb)
        lay.addWidget(self.card_kb)

        common = Card("答题与提交", "先查题库，未命中直接调用大模型；模型无有效答案时停止作答，不猜题。全部题目填写完成后才允许自动提交。", icon="check")
        self.cb_submit = QCheckBox("全部题目有有效答案后自动提交（关闭则仅保存答案）")
        self.sp_cover = _spin((0.0, 1.0), 0.9, 0.05, "", 2)
        self.sp_delay = _spin((0.0, 10.0), 1.0, 0.5, " 秒", 1)
        self.cb_llmcheck = QCheckBox("启动时校验 AI 题库连接（仅 AI / 硅基流动生效，会消耗少量 token）")
        common.add(self.cb_submit)
        self.sp_cover.setValue(1.0)
        self.sp_cover.hide()
        common.add(_row(("每次查询间隔", self.sp_delay)))
        common.add(self.cb_llmcheck)
        lay.addWidget(common)

        # ---- AI 大模型：独立成页（v1.5.3），见 gui_app/page_ai.py ----

        # ---- 言溪 / LIKE 的 token ----
        self.card_token = Card("题库 Token（远端知识库：言溪 / LIKE）",
                               "远端知识库会把题目发送到第三方服务器。言溪在 "
                               "https://tk.enncy.cn/ 获取 token，LIKE 在 https://www.datam.site/ 获取，"
                               "多个用英文逗号分隔。", icon="key")
        self.ed_tokens = _line("token1,token2")
        self.card_token.add(self.ed_tokens)
        lay.addWidget(self.card_token)

        # ---- LIKE 专属 ----
        self.card_like = Card("LIKE 知识库专属配置（远端，不再推荐）",
                              "⚠️ LIKE 会把题目原文上传到 datam.site 并消耗其 token，"
                              "推荐改用「云端共享题库 + AI」组合。", icon="layers")
        self.cb_like_search = QCheckBox("启用模型联网搜索")
        self.cb_like_vision = QCheckBox("启用模型视觉能力（可识别图片题）")
        self.cb_like_retry = QCheckBox("启用自动重试")
        self.ed_like_model = _line("glm-4.5-air")
        self.sp_like_retry = _ispin((1, 10), 3, " 次")
        self.card_like.add(self.cb_like_search)
        self.card_like.add(self.cb_like_vision)
        self.card_like.add(_row(("模型", self.ed_like_model), ("最大重试次数", self.sp_like_retry)))
        self.card_like.add(self.cb_like_retry)
        lay.addWidget(self.card_like)

        # ---- TikuAdapter ----
        self.card_adapter = Card("TikuAdapter 配置",
                                 "默认已接入官方聚合服务，无需改动；仅当你自建 TikuAdapter 时才需填写地址。",
                                 icon="link")
        self.ed_adapter_url = _line("http://127.0.0.1:5000/")
        self.card_adapter.add(self.ed_adapter_url)
        lay.addWidget(self.card_adapter)

        # ---- 硅基流动 ----
        self.card_sf = Card("硅基流动 SiliconFlow",
                            "在 https://cloud.siliconflow.cn/account/ak 获取 key。", icon="zap")
        self.ed_sf_key = _line("sk-...", password=True)
        self.ed_sf_model = _line("deepseek-ai/DeepSeek-V3")
        self.ed_sf_ep = _line("https://api.siliconflow.cn/v1/chat/completions")
        self.card_sf.add(self.ed_sf_key)
        self.card_sf.add(_row(("模型", self.ed_sf_model), (None, None)))
        self.card_sf.add(self.ed_sf_ep)
        lay.addWidget(self.card_sf)

        # ---- GO 题 ----
        self.card_go = Card("GO 题（网课小工具）",
                            "可选填在公众号「一之哥哥」申请到的解除限流 token。", icon="zap")
        self.ed_go_auth = _line("可选")
        self.sp_go_int = _spin((0.0, 60.0), 1.0, 0.5, " 秒", 1)
        self.sp_go_times = _ispin((1, 10), 3, " 次")
        self.sp_go_backoff = _spin((1.0, 3.0), 1.2, 0.1, " ×", 1)
        self.card_go.add(self.ed_go_auth)
        self.card_go.add(_row(("请求间隔", self.sp_go_int), ("重试次数", self.sp_go_times)))
        self.card_go.add(_row(("退避基数", self.sp_go_backoff), (None, None)))
        lay.addWidget(self.card_go)

        lay.addStretch(1)
        self.stack.addWidget(_scroll(inner))
        self._refresh_groups()

    # ================================================================ 高级
    def _build_advanced(self):
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(16)

        diagnostics = Card("连接检查", "遇到登录、题库或用量问题时，先检查各项连接。不会提交课程或调用 AI。", icon="settings")
        self.btn_diagnostics = QPushButton("开始检查")
        self.btn_diagnostics.setAccessibleName("开始检查服务、题库和课程平台连接")
        self.btn_diagnostics.clicked.connect(self._run_diagnostics)
        diagnostics.add_header(self.btn_diagnostics)
        self.diagnostics_summary = QLabel("尚未检查。出现连接问题时，可在这里查看原因。")
        self.diagnostics_summary.setObjectName("CardHint")
        self.diagnostics_summary.setWordWrap(True)
        diagnostics.add(self.diagnostics_summary)
        self.diagnostics_rows = QVBoxLayout()
        self.diagnostics_rows.setSpacing(10)
        diagnostics.add(self.diagnostics_rows)
        self.lb_diagnostics = QLabel("尚未检查")
        self.lb_diagnostics.setTextFormat(Qt.PlainText)
        self.lb_diagnostics.setWordWrap(True)
        self.lb_diagnostics.setTextInteractionFlags(Qt.TextSelectableByMouse)
        diagnostics.add(self.lb_diagnostics)
        self.lb_diagnostics.hide()  # 保留纯文本结果，供自检及辅助读取。
        lay.addWidget(diagnostics)

        c1 = Card("判断题词表", "程序据此判断题目中的「对 / 错」选项，一般不需要改动。", icon="list")
        self.ed_true = _line("正确,对,√,是")
        self.ed_false = _line("错误,错,×,否,不对,不正确")
        c1.add(_row(("视为「正确」", self.ed_true)))
        c1.add(_row(("视为「错误」", self.ed_false)))
        lay.addWidget(c1)

        c2 = Card("文件位置", "所有配置与日志都在程序目录下。", icon="folder")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        entries = [
            ("config.ini", "账号、题库与全部运行参数", store.config_path()),
            ("accounts.json", "多账号档案（备注名 / 手机号 / 勾选课程）",
             os.path.join(store.app_dir(), "accounts.json")),
            ("cookies.txt", "cookies 登录用的凭证文件",
             os.path.join(store.app_dir(), "cookies.txt")),
            ("leke.log", "完整运行日志（含 DEBUG）",
             os.path.join(store.app_dir(), "leke.log")),
        ]
        for i, (name, desc, path) in enumerate(entries):
            lb = QLabel(f"<b>{name}</b>")
            lb.setMinimumWidth(110)
            d = QLabel(desc)
            d.setObjectName("CardHint")
            d.setWordWrap(True)
            b = QPushButton("打开所在位置")
            b.clicked.connect(lambda _=False, p=path: self._reveal(p))
            grid.addWidget(lb, i, 0)
            grid.addWidget(d, i, 1)
            grid.addWidget(b, i, 2)
        grid.setColumnStretch(1, 1)
        c2.body.addLayout(grid)
        c2.add(Divider())
        b_open = QPushButton("打开程序目录")
        b_open.clicked.connect(lambda: self._reveal(store.app_dir()))
        c2.add(b_open)
        lay.addWidget(c2)

        c3 = Card("关于乐课", "课程平台登录信息保存在本机；共享题库、乐课账号和用量统计保存在云端。", icon="info")
        info = QLabel(
            "· 界面框架：PySide6（原生桌面组件，离线运行）<br>"
            "· 多账号：每个账号一份独立会话与 cookies，可并行互不干扰<br>"
            "· 配置读写：直接改写程序目录下的 config.ini，保留原有注释<br>"
            "· 打包方式：PyInstaller，绿色免安装"
        )
        info.setObjectName("CardHint")
        info.setWordWrap(True)
        c3.add(info)
        lay.addWidget(c3)

        lay.addStretch(1)
        self.stack.addWidget(_scroll(inner))

    # ================================================================ 交互
    def _run_diagnostics(self):
        self.btn_diagnostics.setEnabled(False)
        self.btn_diagnostics.setText("检查中…")
        self.diagnostics_summary.setText("正在检查各项连接，请稍候…")
        self.lb_diagnostics.setText("正在检查连接，请稍候…")
        def worker():
            from api.diagnostics import run_diagnostics
            try:
                results = run_diagnostics()
            except Exception:
                results = [{'name': '连接检查', 'status': '未通过', 'detail': '暂时无法完成，请稍后重试'}]
            try:
                self.sig_diagnostics.emit(results)
            except RuntimeError:
                pass  # 页面已关闭。
        threading.Thread(target=worker, daemon=True).start()

    def _show_diagnostics(self, results):
        self.btn_diagnostics.setEnabled(True)
        self.btn_diagnostics.setText("重新检查")
        while self.diagnostics_rows.count():
            item = self.diagnostics_rows.takeAt(0)
            item.widget().deleteLater()
        passed = sum(r['status'] == '通过' for r in results)
        self.diagnostics_summary.setText(f"检查完成：{passed}/{len(results)} 项通过。未通过时，请按对应提示处理。")
        for result in results:
            host = QWidget()
            host.setObjectName("DiagnosticRow")
            row = QHBoxLayout(host)
            row.setContentsMargins(12, 10, 12, 10)
            copy = QVBoxLayout()
            copy.setSpacing(3)
            title = QLabel(result['name'])
            title.setObjectName("DiagnosticTitle")
            detail = QLabel(result['detail'])
            detail.setObjectName("CardHint")
            detail.setWordWrap(True)
            detail.setTextFormat(Qt.PlainText)
            detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
            copy.addWidget(title)
            copy.addWidget(detail)
            row.addLayout(copy, 1)
            kind = {'通过': 'ok', '待登录': 'idle', '需处理': 'warn', '未通过': 'error'}.get(result['status'], 'idle')
            row.addWidget(Badge(result['status'], kind))
            host.setAccessibleName(f"{result['name']}：{result['status']}。{result['detail']}")
            self.diagnostics_rows.addWidget(host)
        self.lb_diagnostics.setText('\n'.join(f"{r['name']}：{r['status']} · {r['detail']}" for r in results))

    def _reveal(self, path: str):
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if not os.path.exists(folder):
            folder = store.app_dir()
        try:
            if sys.platform.startswith("win") and os.path.exists(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        except Exception:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _active_providers(self) -> list[str]:
        return [p.strip() for p in self.ed_provider.text().split(",") if p.strip()]

    @staticmethod
    def _kb_cache_path() -> str:
        """本地知识库的 cache.json 真实路径（与 api.answer.CacheDAO 同源）。"""
        try:
            from api.answer import get_cache_dao
            return str(get_cache_dao().cache_file)
        except Exception:
            return os.path.join(store.app_dir(), "知识库", "cache.json")

    def _refresh_kb_stat(self, *, force=False, migrate=False):
        """后台查询统计与导入，避免网络操作阻塞界面。"""
        if self._kb_busy or (not force and self.lb_kb_stat.text()):
            return
        self._kb_busy = True
        self.lb_kb_stat.setText("正在连接云端题库…")
        def fetch():
            try:
                from api import kc_client
                from api.answer import get_cache_dao
                prefix = ''
                if migrate:
                    count = get_cache_dao().migrate_legacy()
                    prefix = f'已导入 {count} 条旧题库记录。'
                stats = kc_client.knowledge_stats()
                text = f"{prefix}云端共 {stats['count']} 条答案，其中平台确认 {stats['verified_count']} 条"
            except Exception as exc:
                text = f'云端题库：{exc}'
            finally:
                self._kb_busy = False
            try:
                self.sig_kb_status.emit(text)
            except RuntimeError:
                pass  # 页面已销毁。
        threading.Thread(target=fetch, daemon=True).start()

    def _refresh_groups(self):
        provs = set(self._active_providers())
        # 本地知识库卡片常显：它是程序自带的基础设施，展示条目数即可
        self.card_kb.setVisible(True)
        self._refresh_kb_stat()
        self.card_token.setVisible(bool(provs & {"TikuYanxi", "TikuLike"}))
        self.card_like.setVisible("TikuLike" in provs)
        self.card_adapter.setVisible("TikuAdapter" in provs)
        self.card_sf.setVisible("SiliconFlow" in provs)
        self.card_go.setVisible("TikuGo" in provs)

    # ================================================================ 状态记忆
    def _remember_tab(self, index: int):
        """记住停留的页签（存 gui_settings.json，不污染 config.ini）。"""
        try:
            store.ui_save({"settings_tab": int(index)})
        except Exception:
            pass

    def _on_vmode_changed(self, *_):
        """切换视频学习模式时更新下方提示。"""
        timed = (self.cb_vmode.currentData() == "timed")
        if timed:
            self.lb_vmode.setText(
                "按「1 秒真实时间 : 1 秒播放进度」逐段上报，任务点和学习时长一起拿。"
                "倍速高于 1.0 时平台通常只认其中一部分时长，想要时长建议配 1.0 倍。"
                "任务点已经完成的视频会从头重播一遍来补时长。"
            )
        else:
            self.lb_vmode.setText(
                "⚠️ 一次上报到视频结尾，任务点会立刻变绿，但平台按「真实间隔 vs 播放进度」"
                "判定有效学习时长，跳过去的部分不会计入 —— 这就是「任务点完成了却没时长」的原因。"
            )

    def append_ai_provider(self):
        """把 AI 接入题库 provider 列表（AI 页「保存并使用」时由主窗口调用）。

        列表为空时直接写入推荐组合「TikuLocal,AI」：云端共享题库优先，
        没答过的题由 AI 大模型补答并沉淀进知识库；已有其他题库时把 AI 追加到末尾兜底。
        """
        raw = self.ed_provider.text().strip()
        names = [n.strip() for n in raw.split(",") if n.strip()]
        if "AI" not in names:
            if not names:
                self.ed_provider.setText("TikuLocal,AI")
            else:
                names.append("AI")
                self.ed_provider.setText(",".join(names))
            self._refresh_groups()

    # ------------------------------------------------------------------ 给 AI 页
    def provider_names(self) -> list[str]:
        """当前题库来源里启用的 provider 名（去空格）。"""
        return self._active_providers()

    def provider_collect(self) -> dict:
        """只收集题库来源一项 —— AI 页落盘时与 AI 字段合并成一次写盘。"""
        return {("tiku", "provider"): self.ed_provider.text().strip()}

    # ================================================================ 读写
    def load(self):
        self._loading = True
        u = store.ui_load()
        self.sp_speed.setValue(store.get_float("common", "speed", 1.0))
        self.sp_jobs.setValue(store.get_int("common", "jobs", 4))
        _vm = (store.get("common", "video_mode", "fast") or "fast").strip().lower()
        _vi = self.cb_vmode.findData(_vm)
        self.cb_vmode.setCurrentIndex(_vi if _vi >= 0 else 0)
        self._on_vmode_changed()
        action = store.get("common", "notopen_action", "retry").strip().lower() or "retry"
        idx = max(0, self.cb_notopen.findData(action))
        self.cb_notopen.setCurrentIndex(idx)
        self.sp_retry.setValue(store.get_float("common", "retry_interval", 1.0))

        self.cb_skip.setChecked(bool(u.get("skip_finished", True)))
        self.cb_redo.setChecked(store.get_bool("common", "work_redo_enabled", False))
        self.sp_redo.setValue(store.get_int("common", "work_max_retries", 3))
        self.cb_count.setChecked(store.get_bool("common", "add_learning_count", False))
        self.sp_target.setValue(store.get_int("common", "target_count", 100))

        self.ed_provider.setText(store.get("tiku", "provider", ""))
        self.cb_submit.setChecked(store.get_bool("tiku", "submit", False))
        self.sp_cover.setValue(1.0)
        self.sp_delay.setValue(store.get_float("tiku", "delay", 1.0))
        self.cb_llmcheck.setChecked(store.get_bool("tiku", "check_llm_connection", True))
        self.ed_tokens.setText(store.get("tiku", "tokens", ""))

        self.cb_like_search.setChecked(store.get_bool("tiku", "likeapi_search", False))
        self.cb_like_vision.setChecked(store.get_bool("tiku", "likeapi_vision", True))
        self.cb_like_retry.setChecked(store.get_bool("tiku", "likeapi_retry", True))
        self.ed_like_model.setText(store.get("tiku", "likeapi_model", "glm-4.5-air"))
        self.sp_like_retry.setValue(store.get_int("tiku", "likeapi_retry_times", 3))

        self.ed_adapter_url.setText(store.get("tiku", "url", ""))

        self.ed_sf_key.setText(store.get("tiku", "siliconflow_key", ""))
        self.ed_sf_model.setText(store.get("tiku", "siliconflow_model", "deepseek-ai/DeepSeek-V3"))
        self.ed_sf_ep.setText(store.get("tiku", "siliconflow_endpoint",
                                        "https://api.siliconflow.cn/v1/chat/completions"))

        self.ed_go_auth.setText(store.get("tiku", "go_authorization", ""))
        self.sp_go_int.setValue(store.get_float("tiku", "go_min_interval", 1.0))
        self.sp_go_times.setValue(store.get_int("tiku", "go_retry_times", 3))
        self.sp_go_backoff.setValue(store.get_float("tiku", "go_retry_backoff", 1.2))

        self.ed_true.setText(store.get("tiku", "true_list", "正确,对,√,是"))
        self.ed_false.setText(store.get("tiku", "false_list", "错误,错,×,否,不对,不正确"))

        self._refresh_groups()
        self._loading = False
        self.badge.set_kind("idle", "已加载")

    def _collect(self) -> dict:
        return {
            ("common", "speed"): f"{self.sp_speed.value():.1f}",
            ("common", "jobs"): str(self.sp_jobs.value()),
            ("common", "video_mode"): str(self.cb_vmode.currentData() or "fast"),
            ("common", "notopen_action"): self.cb_notopen.currentData(),
            ("common", "retry_interval"): f"{self.sp_retry.value():.1f}",
            ("common", "work_redo_enabled"): "true" if self.cb_redo.isChecked() else "false",
            ("common", "work_max_retries"): str(self.sp_redo.value()),
            ("common", "add_learning_count"): "true" if self.cb_count.isChecked() else "false",
            ("common", "target_count"): str(self.sp_target.value()),

            ("tiku", "provider"): self.ed_provider.text().strip(),
            ("tiku", "submit"): "true" if self.cb_submit.isChecked() else "false",
            ("tiku", "cover_rate"): "1.00",
            ("tiku", "delay"): f"{self.sp_delay.value():.1f}",
            ("tiku", "check_llm_connection"): "true" if self.cb_llmcheck.isChecked() else "false",
            ("tiku", "tokens"): self.ed_tokens.text().strip(),
            ("tiku", "likeapi_search"): "true" if self.cb_like_search.isChecked() else "false",
            ("tiku", "likeapi_vision"): "true" if self.cb_like_vision.isChecked() else "false",
            ("tiku", "likeapi_retry"): "true" if self.cb_like_retry.isChecked() else "false",
            ("tiku", "likeapi_model"): self.ed_like_model.text().strip(),
            ("tiku", "likeapi_retry_times"): str(self.sp_like_retry.value()),
            ("tiku", "url"): self.ed_adapter_url.text().strip(),
            ("tiku", "siliconflow_key"): self.ed_sf_key.text().strip(),
            ("tiku", "siliconflow_model"): self.ed_sf_model.text().strip(),
            ("tiku", "siliconflow_endpoint"): self.ed_sf_ep.text().strip(),
            ("tiku", "go_authorization"): self.ed_go_auth.text().strip(),
            ("tiku", "go_min_interval"): f"{self.sp_go_int.value():.1f}",
            ("tiku", "go_retry_times"): str(self.sp_go_times.value()),
            ("tiku", "go_retry_backoff"): f"{self.sp_go_backoff.value():.1f}",
            ("tiku", "true_list"): self.ed_true.text().strip(),
            ("tiku", "false_list"): self.ed_false.text().strip(),
        }

    def save(self):
        updates = self._collect()
        try:
            changed = store.update_values(updates)
        except Exception as e:
            self.badge.set_kind("error", "保存失败")
            self.lb_result.setText(f"{type(e).__name__}: {e}")
            return
        store.ui_save({"skip_finished": self.cb_skip.isChecked()})
        self.badge.set_kind("ok", "已保存")
        if changed:
            shown = "；".join(changed[:4]) + ("…" if len(changed) > 4 else "")
            self.lb_result.setText(shown)
        else:
            self.lb_result.setText("没有检测到变化")
        # 保存成功但配置不完整时给出明确提示：这样配跑起来题库会被整体跳过，
        # 用户不提前知道的话，只能等刷完课看到「章节检测跳过」才发现（v1.5.7）
        warn = self._config_warnings()
        if warn:
            self.badge.set_kind("warn", "已保存 · 有隐患")
            self.lb_result.setText(warn)
        self.sig_saved.emit(changed)

    def _config_warnings(self) -> str:
        """返回「当前这样配跑起来会无效」的提示（仅提示，不阻断保存）。"""
        provs = set(self._active_providers())
        msgs: list[str] = []
        if provs & {"TikuYanxi", "TikuLike"} and not self.ed_tokens.text().strip():
            msgs.append("言溪 / LIKE 需要 token，当前为空 → 该题库会被跳过")
        if "TikuAdapter" in provs and not self.ed_adapter_url.text().strip():
            msgs.append("TikuAdapter 需要服务地址，当前为空 → 会被跳过")
        if "SiliconFlow" in provs and not self.ed_sf_key.text().strip():
            msgs.append("硅基流动需要 key，当前为空 → 会被跳过")
        if "AI" in provs and not (store.get("tiku", "key", "") or "").strip():
            msgs.append("题库来源含 AI，但还没有密钥 → 到「AI 大模型」页填写")
        if not provs:
            msgs.append("题库来源为空 → 章节检测会被整体跳过")
        return "⚠️ " + "；".join(msgs) if msgs else ""

    # ------------------------------------------------------------------ 给运行页
    def current_common(self) -> dict:
        """返回运行刷课需要的 common 配置字典（不落盘，直接用于本次运行）。"""
        c = {
            "speed": self.sp_speed.value(),
            "jobs": self.sp_jobs.value(),
            "notopen_action": self.cb_notopen.currentData(),
            "retry_interval": self.sp_retry.value(),
            "work_redo_enabled": self.cb_redo.isChecked(),
            "work_max_retries": self.sp_redo.value(),
            "add_learning_count": self.cb_count.isChecked(),
            "target_count": self.sp_target.value(),
            "skip_finished": self.cb_skip.isChecked(),
            "video_mode": str(self.cb_vmode.currentData() or "fast"),
        }
        return c

    def current_tiku(self) -> dict:
        d = {
            "provider": self.ed_provider.text().strip(),
            "submit": "true" if self.cb_submit.isChecked() else "false",
            "cover_rate": self.sp_cover.value(),
            "delay": self.sp_delay.value(),
            "check_llm_connection": "true" if self.cb_llmcheck.isChecked() else "false",
            "tokens": self.ed_tokens.text().strip(),
            "likeapi_search": "true" if self.cb_like_search.isChecked() else "false",
            "likeapi_vision": "true" if self.cb_like_vision.isChecked() else "false",
            "likeapi_retry": "true" if self.cb_like_retry.isChecked() else "false",
            "likeapi_model": self.ed_like_model.text().strip(),
            "likeapi_retry_times": str(self.sp_like_retry.value()),
            "url": self.ed_adapter_url.text().strip(),
            "siliconflow_key": self.ed_sf_key.text().strip(),
            "siliconflow_model": self.ed_sf_model.text().strip(),
            "siliconflow_endpoint": self.ed_sf_ep.text().strip(),
            "go_authorization": self.ed_go_auth.text().strip(),
            "go_min_interval": f"{self.sp_go_int.value():.1f}",
            "go_retry_times": str(self.sp_go_times.value()),
            "go_retry_backoff": f"{self.sp_go_backoff.value():.1f}",
            "true_list": self.ed_true.text().strip(),
            "false_list": self.ed_false.text().strip(),
        }
        return d
