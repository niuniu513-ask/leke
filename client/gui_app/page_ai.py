# -*- coding: utf-8 -*-
"""AI 大模型页：服务商选择、密钥、连通性检测（从参数设置页拆出独立成页）。

AiConfigPanel 面板本体在 gui_app/ai_panel.py；本页是它的宿主，负责：
- 面板的加载（load）与「未保存」提示；
- 「保存并使用」经由主窗口协调：AI 字段 + 启用后的题库 provider 一起落盘
  （main_window._on_save_ai 调 settings_page.append_ai_provider 后统一写 config.ini）；
- 「一键启用 AI」跨页操作：发信号给主窗口，转给参数设置页改题库来源。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from gui_app import store
from gui_app.ai_panel import AiConfigPanel
from gui_app.widgets import Badge


class AiPage(QWidget):
    sig_save_requested = Signal()   # 面板点「保存并使用」→ 主窗口协调写盘
    sig_enable_requested = Signal()  # 面板点「启用到题库」→ 主窗口转设置页

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        # 面板内容比一屏高（三步向导 + 高级项 + 汇总），必须套 QScrollArea，
        # 否则窗口矮时布局被硬压缩、标签与输入框叠字（v1.5.3 实测踩坑）
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(14)

        self.panel = AiConfigPanel()
        self.panel.request_save.connect(self.sig_save_requested.emit)
        self.panel.request_enable_ai.connect(self.sig_enable_requested.emit)
        self.panel.changed.connect(self._mark_dirty)
        lay.addWidget(self.panel)

        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        self.badge = Badge("尚未保存", "idle")
        self.lb_result = QLabel("")
        self.lb_result.setObjectName("CardHint")
        bottom.addWidget(self.badge)
        bottom.addWidget(self.lb_result, 1)
        lay.addLayout(bottom)
        lay.addStretch(1)

        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(inner)
        sa.setFrameShape(QScrollArea.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(sa, 1)

        self.load()

    # ------------------------------------------------------------------ 数据
    def load(self):
        """从 config.ini 读 AI 字段（加载期间屏蔽信号，避免误标未保存）。"""
        self.panel.blockSignals(True)
        self.panel.load(store.get)
        self.panel.blockSignals(False)
        self.badge.set_kind("idle", "已加载")
        self.lb_result.setText("")

    def collect_ai(self) -> dict:
        """AI 字段（tiku 段的 endpoint / key / model 等），供写盘与运行合并。"""
        return dict(self.panel.current())

    def values(self) -> dict:
        """AI 字段的待写盘形态（tiku 段键值）。"""
        return dict(self.panel.values())

    # ------------------------------------------------------------------ 状态
    def set_provider_hint(self, enabled: bool):
        """题库来源里是否已含 AI（参数设置页改 provider 后由主窗口转发）。"""
        self.panel.set_provider_enabled_hint(bool(enabled))

    def notify_saved(self, ok: bool, message: str):
        self.panel.notify_saved(bool(ok), message)
        self.badge.set_kind("ok" if ok else "error", "已保存" if ok else "保存失败")

    def _mark_dirty(self):
        self.badge.set_kind("warn", "AI 配置未保存")
        self.lb_result.setText("AI 配置已修改，点「保存并使用」写回 config.ini")
