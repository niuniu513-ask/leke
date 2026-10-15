# -*- coding: utf-8 -*-
"""运行页：多账号并行刷课。

版式
----
顶部选账号 + 开始/停止，中间每个账号一张实时进度卡（纵向排列、可滚动），
底部是共享日志。账号卡里精确显示到「正在处理哪个视频、章节内第几个任务点」。
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from gui_app import store
from gui_app import theme as T
from gui_app.widgets import Badge, Card, Divider, EmptyState, SmoothBar

#: 引擎上报的播放秒数比当前显示值回退超过这个量 → 判定为真·重播（而不是取整抖动）
VIDEO_REWIND_TOL = 5.0

STATE_BADGE = {
    "idle": ("待机", "idle"),
    "running": ("运行中", "info"),
    "stopping": ("停止中", "warn"),
    "ok": ("已完成", "ok"),
    "incomplete": ("仍有未完成", "warn"),
    "unverified": ("完成状态未确认", "warn"),
    "stopped": ("已停止", "warn"),
    "error": ("出错", "error"),
}


def _fmt_hms(sec: float) -> str:
    sec = max(0, int(sec))
    if sec >= 3600:
        return f"{sec // 3600:d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"
    return f"{sec // 60:02d}:{sec % 60:02d}"


class AccountProgressCard(QFrame):
    """单个账号的实时进度卡。"""

    def __init__(self, acc_id: str, name: str, parent=None):
        super().__init__(parent)
        self.acc_id = str(acc_id)
        self.setObjectName("AccCard")

        self._plan = 0
        self._done = 0
        self._failed = 0
        self._total_done = 0
        self._pass = 0
        self._t0 = 0.0
        self._active = False
        # 并行任务点槽（v1.5.1f）：key -> {row, chip, title, meta, bar, video, ts, t0, index, total}
        # 多章节并发时每个任务点各占一行，不再互相覆盖。
        self._slots: dict[str, dict] = {}
        self._slot_seq: list[str] = []       # 插入顺序（LRU 上限用）
        self._last_done_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 15)
        lay.setSpacing(9)

        # ---- 头部 ----
        head = QHBoxLayout()
        head.setSpacing(9)
        self.dot = QLabel()
        self.dot.setObjectName("AccDot")
        self.dot.setFixedSize(9, 9)
        head.addWidget(self.dot)
        self.lb_name = QLabel(name)
        self.lb_name.setObjectName("AccCardName")
        self.lb_name.setWordWrap(True)
        self.lb_name.setToolTip(name)
        head.addWidget(self.lb_name)
        self.lb_pass = QLabel("自动续刷")
        self.lb_pass.setObjectName("PassTag")
        self.lb_pass.setMinimumHeight(22)
        head.addWidget(self.lb_pass)
        head.addStretch(1)
        self.lb_time = QLabel("00:00")
        self.lb_time.setObjectName("AccTime")
        head.addWidget(self.lb_time)
        self.badge = Badge("待机", "idle")
        head.addWidget(self.badge)
        lay.addLayout(head)

        # ---- 进度条 + 文案 ----
        # 章节粒度：每完成一个章节前进一格，所以步进必然较大 —— 这里用自绘的
        # SmoothBar 做 ~250ms 缓动过渡，让"跳一格"看起来是滑过去的而不是硬跳。
        self.bar = SmoothBar(height=8, radius=6)
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        lay.addWidget(self.bar)

        self.lb_stats = QLabel("尚未开始")
        self.lb_stats.setObjectName("CardHint")
        self.lb_stats.setWordWrap(True)
        lay.addWidget(self.lb_stats)

        # ---- 当前正在处理（并行槽：每个任务点一行，多章节并行各占一行） ----
        self.now_box = QFrame()
        self.now_box.setObjectName("NowBox")
        # 高度交给内容自己决定：整个页面外部套了滚动区，
        # 用 setFixedHeight 反而会把 5 行内容挤在一起（实测会叠字）。
        self.now_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        now = QVBoxLayout(self.now_box)
        now.setContentsMargins(13, 10, 13, 11)
        now.setSpacing(6)

        # 空态：没跑起来时只显示这一行，不摆一堆「—」占位符。
        # 完整的“并行时每行各显各的”说明改挂 tooltip —— 常驻一行就够，
        # 详说明属于“想知道才看”的内容，不该一直占着版面（v1.6.2）。
        self.lb_now_empty = QLabel("等待开始…")
        self.lb_now_empty.setObjectName("NowEmpty")
        self.lb_now_empty.setWordWrap(True)
        self.lb_now_empty.setToolTip(
            "开始后这里会逐行显示正在处理的章节与任务点，\n"
            "多章节并行时每行各显示各的进度。")
        now.addWidget(self.lb_now_empty)

        self.slots_box = QVBoxLayout()
        self.slots_box.setSpacing(5)
        now.addLayout(self.slots_box)

        self.lb_last = QLabel("")
        self.lb_last.setObjectName("NowMeta")
        self.lb_last.setWordWrap(True)
        self.lb_last.setVisible(False)
        now.addWidget(self.lb_last)

        lay.addWidget(self.now_box)
        self._set_now_state("idle")

    # ------------------------------------------------------------------ 样式
    @staticmethod
    def _repolish(w):
        w.style().unpolish(w)
        w.style().polish(w)

    def _set_now_state(self, state: str):
        """切换「当前处理」区块的样式态。

        QSS 用的是 `QFrame#NowBox[state="run"]` 这种祖先属性选择器，
        Qt 只在**自身**被 polish 时才重算匹配，所以父子要一起刷。
        """
        self.now_box.setProperty("state", state)
        self._repolish(self.now_box)

    def set_state(self, state: str):
        text, kind = STATE_BADGE.get(state, STATE_BADGE["idle"])
        self.badge.set_kind(kind, text)
        self.setProperty("state", state)
        # 祖先属性选择器（AccCard[state] #AccDot）不会自动重算，父子一起刷
        for w in (self, self.dot, self.lb_name, self.lb_pass, self.lb_time, self.badge):
            self._repolish(w)
        self._active = state in ("running", "stopping")
        if state == "running":
            self._t0 = self._t0 or time.time()

    # ------------------------------------------------------------------ 数据
    def reset(self):
        self._plan = self._done = self._failed = self._total_done = 0
        self._pass = 0
        self._t0 = 0.0
        self.lb_pass.setText("自动续刷")
        self.lb_time.setText("00:00")
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self._refresh_stats()
        self._clear_slots()
        self.lb_last.setVisible(False)
        self.lb_last.setText("")
        self._last_done_text = ""
        self.set_state("idle")

    def _refresh_stats(self):
        if self._plan <= 0 and self._done <= 0 and self._total_done <= 0:
            self.lb_stats.setText("尚未开始")
            return
        pct = int(round(self._done * 100.0 / self._plan)) if self._plan else 0
        pct = max(0, min(100, pct))
        txt = (f"待刷 {self._plan} 个章节　·　本遍已处理 {self._done}"
               f"（{pct}%）　·　累计本地处理成功 {self._total_done} 个（待平台核对）")
        if self._failed:
            txt += f"　·　失败 {self._failed} 个"
        self.lb_stats.setText(txt)

    def _set_now_empty(self, empty: bool):
        """空态切换：True = 只显示占位文案，藏起并行槽行。"""
        self.lb_now_empty.setVisible(bool(empty))
        for slot in self._slots.values():
            slot["row"].setVisible(not empty)
        if empty:
            self.lb_last.setVisible(False)

    def set_total(self, total: int):
        self._plan = int(total or 0)
        self.bar.setRange(0, max(1, self._plan))
        self.bar.setValue(min(self._done, self._plan))
        self._refresh_stats()

    def set_pass(self, pass_no: int):
        self._pass = int(pass_no or 1)
        self.lb_pass.setText("自动续刷" if self._pass <= 1
                             else f"自动续刷 · 第 {self._pass} 遍")
        if self._pass > 1:
            # 新一遍开始：上一遍的槽位全部清空（scheduler 同步跑完一遍才进下一遍，安全）
            self._clear_slots()

    def set_cumulative(self, total_done: int, failed: int):
        self._total_done = int(total_done or 0)
        self._failed = int(failed or 0)
        self._refresh_stats()

    def update_progress(self, done: int, failed: int, current: str):
        self._done = int(done or 0)
        self._failed = int(failed or 0)
        self.bar.setValue(min(self._done, self.bar.maximum()))
        if current:
            self._last_done_text = f"上次完成：{current}"
            self.lb_last.setText(self._last_done_text)
            self.lb_last.setVisible(not self.lb_now_empty.isVisible())
        self._refresh_stats()

    # ------------------------------------------------------------------ 并行槽
    SLOT_STALE_SEC = 120        # 超过该时长无更新 → 行灰显（无完成信号，只能靠时间判）
    SLOT_REMOVE_SEC = 600       # 超过该时长无更新 → 行移除
    SLOT_MAX_ROWS = 12          # 行数上限（LRU，超出挤掉最旧一行）
    #: 心跳外推窗口：引擎正常情况下每秒一帧，超过这么久没帧说明任务卡住了。
    #: 再往外推就是凭空编进度，必须停（否则界面会显示一个根本不存在的播放进度）。
    SLOT_EXTRAPOLATE_SEC = 10.0

    @staticmethod
    def _slot_key(info: dict) -> str:
        """槽位键：课程 + 任务点名（视频心跳与任务点信号同构，靠它对齐同一行）。"""
        return f"{info.get('course') or ''}\x1f{info.get('job') or ''}"

    @staticmethod
    def _slot_title(info: dict) -> str:
        chapter = str(info.get("chapter") or "").strip()
        job = str(info.get("job") or "").strip()
        parts = [p for p in (chapter, job) if p and p != "—"]
        txt = " · ".join(parts) if parts else "—"
        return txt if len(txt) <= 64 else txt[:61] + "…"

    def _make_slot_row(self, info: dict) -> dict:
        row = QFrame()
        row.setObjectName("SlotRow")
        row.setProperty("state", "run")
        v = QVBoxLayout(row)
        v.setContentsMargins(10, 7, 10, 8)
        v.setSpacing(4)
        h = QHBoxLayout()
        h.setSpacing(8)
        chip = QLabel(str(info.get("job_type") or "任务点"))
        chip.setObjectName("JobChip")
        chip.setAlignment(Qt.AlignCenter)
        title = QLabel(self._slot_title(info))
        title.setObjectName("SlotTitle")
        title.setWordWrap(True)
        h.addWidget(chip, 0, Qt.AlignTop)
        h.addWidget(title, 1)
        meta = QLabel("")
        meta.setObjectName("SlotMeta")
        h.addWidget(meta, 0, Qt.AlignTop)
        v.addLayout(h)
        # 视频迷你条：浮点自绘（QProgressBar 的 chunk 只走整数像素，
        # 一个 30 分钟的视频每秒只前进 0.4px，看起来就是"卡住→跳一格"）
        bar = SmoothBar(height=5, radius=2.5, variant="video", animate=False)
        bar.setTextVisible(False)
        bar.setRange(0, 1)
        bar.setValue(0)
        bar.setVisible(False)
        v.addWidget(bar)

        self.slots_box.addWidget(row)
        for w in (row, chip, title, meta):
            self._repolish(w)
        return {"row": row, "chip": chip, "title": title, "meta": meta,
                "bar": bar, "video": None, "ts": time.time(), "t0": time.time(),
                "index": 0, "total": 0}

    def _drop_slot(self, key: str):
        slot = self._slots.pop(key, None)
        if slot:
            slot["row"].setParent(None)
            try:
                self._slot_seq.remove(key)
            except ValueError:
                pass

    def _upsert_slot(self, info: dict) -> str:
        key = self._slot_key(info)
        slot = self._slots.get(key)
        if slot is None:
            while len(self._slots) >= self.SLOT_MAX_ROWS and self._slot_seq:
                self._drop_slot(self._slot_seq[0])
            slot = self._make_slot_row(info)
            self._slots[key] = slot
            self._slot_seq.append(key)
            slot["row"].setToolTip(str(info.get("course") or ""))
        slot["ts"] = time.time()
        try:
            slot["index"] = int(info.get("job_index") or 0)
            slot["total"] = int(info.get("job_total") or 0)
        except (TypeError, ValueError):
            pass
        if slot["row"].property("state") != "run":
            slot["row"].setProperty("state", "run")
            self._repolish(slot["row"])
        self._refresh_slot_text(key)
        return key

    def _refresh_slot_text(self, key: str):
        slot = self._slots.get(key)
        if not slot:
            return
        i, n = slot["index"], slot["total"]
        v = slot["video"]
        if v:
            sp = float(v.get("speed") or 1.0)
            sp_txt = f"{sp:g}" if sp != 1.0 else "1.0"
            # 用显示值（插值后）而不是引擎原始值，条子和文案才是同一个数
            pt = slot["bar"].shown()
            du = float(v["duration"])
            pct = int(round(100.0 * pt / du)) if du > 0 else 0
            txt = (f"播放中 {_fmt_hms(pt)} / {_fmt_hms(du)}"
                   f"　·　{sp_txt}x　·　{pct}%")
            if n > 0:
                txt = f"第 {max(1, i)}/{n} 个　·　" + txt
        else:
            txt = f"第 {max(1, i)}/{n} 个" if n > 0 else "处理中…"
            txt += f"　·　已用 {_fmt_hms(time.time() - slot['t0'])}"
        slot["meta"].setText(txt)

    def _clear_slots(self):
        for key in list(self._slots.keys()):
            self._drop_slot(key)
        self._slot_seq.clear()

    def set_current(self, info):
        if not info:
            self._clear_slots()
            self._set_now_empty(True)
            self._set_now_state("idle")
            return
        self._set_now_empty(False)
        self._set_now_state("run")
        self._upsert_slot(info)

    def set_video_progress(self, info):
        """视频播放心跳：更新所属槽位的迷你进度条与文案。

        为什么不能直接把心跳数字塞进进度条
        ----------------------------------
        引擎的心跳是「一秒一帧 + 整秒取整」，而且每 30~90 秒会有一次上报请求把
        循环阻塞几百毫秒。把这串数字直接喂给进度条，画面上就是「原地不动 →
        突然跳一格」，也就是用户反馈的「进度条总是一跳一跳」。

        所以这里只把心跳当**锚点**：记下「第几秒时本地时钟是多少」，
        显示值 = anchor_pt + (now - anchor_ts) * speed，由 RunPage 的 100ms
        定时器推进（见 animate()），chunk 边缘因此是连续滑动的。

        另外，同一个播放会话内 displayed **只增不减**：引擎上报的整秒取整、
        网络往返抖动都不会让进度条倒退。只有真·重播/换视频（引擎给了新的
        session，或播放秒数大幅回退）才重锚并允许归零。
        """
        if not isinstance(info, dict) or not info:
            return
        try:
            pt = max(0.0, float(info.get("play_time") or 0.0))
            du = max(1.0, float(info.get("duration") or 1.0))
            speed = float(info.get("speed") or 1.0)
        except (TypeError, ValueError):
            return
        if speed <= 0:
            speed = 1.0
        key = self._slot_key(info)
        slot = self._slots.get(key)
        if slot is None:
            return                       # 槽还没建（心跳先于任务点信号），忽略

        now = time.monotonic()
        session = info.get("session")
        v = slot.get("video")
        restart = v is None
        if not restart and session is not None and v.get("session") is not None:
            restart = str(session) != str(v["session"])
        if not restart and pt + VIDEO_REWIND_TOL < float(v["displayed"]):
            restart = True               # 没打招呼就大幅倒退 → 按重播处理

        if restart:
            v = {"session": session, "anchor_pt": pt, "anchor_ts": now,
                 "displayed": min(pt, du), "duration": du, "speed": speed,
                 "text_sec": -1}
            slot["video"] = v
        else:
            v["speed"] = speed
            v["duration"] = du
            if session is not None:
                v["session"] = session
            if pt >= float(v["anchor_pt"]):
                v["anchor_pt"] = pt
                v["anchor_ts"] = now
            v["displayed"] = max(float(v["displayed"]), min(pt, du))

        slot["ts"] = time.time()
        if slot["row"].property("state") != "run":
            slot["row"].setProperty("state", "run")
            self._repolish(slot["row"])
        slot["bar"].setRange(0.0, du)
        slot["bar"].set_display(float(v["displayed"]), force=True)
        slot["bar"].setVisible(True)
        self._set_now_empty(False)
        self._refresh_slot_text(key)

    def animate(self) -> bool:
        """把视频迷你条按墙钟连续推进（RunPage 的 100ms 定时器调用）。

        返回是否还有槽在推进；全停了就让上层把定时器歇掉，避免空转。
        """
        now = time.monotonic()
        busy = False
        for key, slot in self._slots.items():
            v = slot.get("video")
            if not v:
                continue
            du = float(v["duration"])
            if float(v["displayed"]) >= du:
                continue
            gap = now - float(v["anchor_ts"])
            if gap > self.SLOT_EXTRAPOLATE_SEC:
                # 心跳断了：冻结显示值，不再凭墙钟外推（宁可停住也不能编进度）
                continue
            busy = True
            target = min(du, float(v["anchor_pt"]) + gap * float(v["speed"]))
            if target > float(v["displayed"]):
                v["displayed"] = target
                slot["bar"].set_display(target)
                sec = int(target)
                if sec != v.get("text_sec"):
                    v["text_sec"] = sec
                    self._refresh_slot_text(key)
        return busy

    def set_stopping(self):
        self.set_state("stopping")
        self.lb_last.setText("正在停止…当前任务点会先收尾")
        self.lb_last.setVisible(not self.lb_now_empty.isVisible())

    def set_finished(self, status: str, message: str):
        self.set_state(status if status in STATE_BADGE else "idle")
        self._t0 = 0.0
        self._clear_slots()
        self._set_now_empty(True)
        self._set_now_state("idle")
        if status == "ok" and self._plan:
            self.bar.setValue(self.bar.maximum())
        if message:
            self.lb_stats.setText(message)

    def tick(self):
        if self._t0:
            self.lb_time.setText(_fmt_hms(time.time() - self._t0))
        now = time.time()
        for key in list(self._slots.keys()):
            slot = self._slots.get(key)
            if slot is None:
                continue
            self._refresh_slot_text(key)
            idle = now - slot["ts"]
            if idle > self.SLOT_REMOVE_SEC:
                self._drop_slot(key)
                continue
            want = "stale" if idle > self.SLOT_STALE_SEC else "run"
            if slot["row"].property("state") != want:
                slot["row"].setProperty("state", want)
                self._repolish(slot["row"])


class RunPage(QWidget):
    sig_start = Signal(list)     # 选中的账号 id 列表
    sig_stop = Signal()          # 空 = 停止全部
    sig_restudy = Signal(bool)   # 从头重刷开关变化（True = 含已完成章节）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._u = store.ui_load()
        self.cards: dict[str, AccountProgressCard] = {}
        self._accounts: list[tuple[str, str]] = []
        self._boxes: dict[str, QCheckBox] = {}

        # 页面控件密度最高，窗口一矮就会被压到破版。统一放进滚动区。
        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        root.addWidget(self._build_control())

        # ---------------- 账号进度 ----------------
        # 副标题（“每个账号独立一栏…”）删了：与卡片里的实际内容一眼可见，
        # 属复述型小字；真正需要解释的行为挂在「从头重刷」的 tooltip 里。
        prog = Card(
            "账号进度", icon="chart", margins=(20, 16, 20, 18),
        )
        self.cards_box = QVBoxLayout()
        self.cards_box.setSpacing(11)
        prog.add(self.cards_box)
        self.empty = EmptyState(
            "play", "还没有选择账号",
            "在「课程列表」页读好课程后，回到这里勾选要刷课的账号，再点「开始刷课」。")
        self.empty.set_visible(True)
        prog.add(self.empty)
        root.addWidget(prog)

        # ---------------- 日志 ----------------
        log_card = Card("运行日志", icon="clipboard", margins=(20, 14, 20, 16))
        log_card.setMaximumHeight(260)
        head2 = QHBoxLayout()
        head2.setSpacing(10)
        head2.addStretch(1)

        self.cb_verbose = QCheckBox("显示详细日志")
        self.cb_verbose.setChecked(bool(self._u.get("verbose_log", False)))
        self.cb_verbose.toggled.connect(
            lambda v: store.ui_save({"verbose_log": bool(v)}))
        self.cb_scroll = QCheckBox("自动滚动")
        self.cb_scroll.setChecked(bool(self._u.get("auto_scroll", True)))
        self.cb_scroll.toggled.connect(
            lambda v: store.ui_save({"auto_scroll": bool(v)}))
        b_clear = QPushButton("清空")
        b_clear.setProperty("variant", "ghost")
        b_clear.clicked.connect(self.clear_log)
        b_export = QPushButton("导出")
        b_export.setProperty("variant", "ghost")
        b_export.clicked.connect(self.export_log)

        head2.addWidget(self.cb_verbose)
        head2.addWidget(self.cb_scroll)
        head2.addWidget(b_clear)
        head2.addWidget(b_export)
        log_card.add_header(head2)

        self.log = QPlainTextEdit()
        self.log.setObjectName("LogView")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log.setPlaceholderText("刷课日志会实时显示在这里…")
        self.log.setMinimumHeight(150)
        log_card.add(self.log)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        # 视频迷你条的插值驱动（100ms，约 10 帧/秒）。引擎每秒只给一帧、
        # 而且上报请求会把循环阻塞住；靠这只定时器按墙钟推进显示值，
        # 进度条才是连续滑动而不是一跳一跳（见 AccountProgressCard.animate）。
        self._anim = QTimer(self)
        self._anim.setInterval(100)
        self._anim.timeout.connect(self._anim_tick)
        self._anim_budget = 0

        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.NoFrame)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroller.setWidget(inner)

        # 版式：上半段（控制卡 + 账号进度卡）可滚动，日志**常驻底部**。
        # 之前日志也塞在同一个 QScrollArea 里 —— 页面滚动条和日志自己的滚动条
        # 叠在一起，鼠标停在日志上滚滚轮经常把整页滚走，日志还随时会被滚出视野。
        # 现在日志是页面的固定成员，滚动只发生在上面那段。
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(12)
        shell.addWidget(scroller, 1)
        shell.addWidget(log_card)

        # 初始态（待机）在构建各控件时已设好：开始可用 / 停止禁用 / 徽标待机

    # ------------------------------------------------------------------ 构建
    def _build_control(self) -> Card:
        # 整页套在 QScrollArea 里，不要用 elevated：QGraphicsDropShadowEffect
        # 会强制离屏渲染，滚动时闪（widgets.soft_shadow 的明文告诫）
        card = Card(margins=(20, 14, 20, 14))

        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_start = QPushButton("开始刷课")
        self.btn_start.setProperty("variant", "primary")
        self.btn_start.setMinimumHeight(38)
        self.btn_start.setMinimumWidth(140)
        self.btn_start.clicked.connect(self._on_start)

        self.btn_stop = QPushButton("停止全部")
        self.btn_stop.setProperty("variant", "danger")
        self.btn_stop.setMinimumHeight(38)
        self.btn_stop.setMinimumWidth(110)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.sig_stop.emit)

        self.badge = Badge("待机", "idle")
        self.lb_mode = QLabel("")
        self.lb_mode.setObjectName("CardHint")

        # 从头重刷：与设置页「只处理未完成的任务点」是同一配置的反向视图
        # （gui_settings.json 的 skip_finished）。勾上 = 已完成章节也重新处理。
        self.cb_restudy = QCheckBox("从头重刷（含已完成章节）")
        self.cb_restudy.setToolTip(
            "勾选后，已经刷完的章节和视频也会重新处理一遍，\n"
            "用于补学习时长（建议配合设置页的「真实节奏」模式）。\n"
            "不勾 = 只处理未完成的任务点（默认，快）。")
        self.cb_restudy.setChecked(not store.ui_load().get("skip_finished", True))
        self.cb_restudy.toggled.connect(self._on_restudy_toggled)

        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        row.addWidget(self.badge)
        row.addSpacing(6)
        row.addWidget(self.cb_restudy)
        row.addStretch(1)
        row.addWidget(self.lb_mode)
        card.add(row)

        # 分隔线：上半是“操作”（开始/停止/模式开关），下半是“选择对象”（账号），
        # 不分隔时两行控件看起来是一回事（v1.6.2）。
        card.add(Divider())

        sel = QHBoxLayout()
        sel.setSpacing(9)
        lab = QLabel("参与刷课的账号")
        lab.setObjectName("FieldLabel")
        sel.addWidget(lab)
        self.acc_row = QHBoxLayout()
        self.acc_row.setSpacing(9)
        self.acc_row.setContentsMargins(0, 0, 0, 0)
        account_choices = QWidget()
        account_choices.setLayout(self.acc_row)
        account_scroll = QScrollArea()
        account_scroll.setWidgetResizable(True)
        account_scroll.setFrameShape(QFrame.NoFrame)
        account_scroll.setMinimumWidth(0)
        account_scroll.setFixedHeight(48)
        account_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        account_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        account_scroll.setWidget(account_choices)
        self.account_scroll = account_scroll
        sel.addWidget(account_scroll, 1)
        self.lb_sel = QLabel("")
        self.lb_sel.setObjectName("CardHint")
        sel.addWidget(self.lb_sel)
        card.add(sel)
        return card

    # ------------------------------------------------------------------ 账号
    def set_accounts(self, accounts: list[tuple[str, str]]):
        """重建账号勾选行与进度卡。accounts = [(acc_id, 显示名), …]"""
        for b in self._boxes.values():
            b.setParent(None)
        self._boxes = {}
        self._accounts = list(accounts)

        for acc_id, name in self._accounts:
            b = QCheckBox(name)
            b.setChecked(True)
            b.toggled.connect(self._refresh_sel)
            self.acc_row.addWidget(b)
            self._boxes[acc_id] = b
            self._ensure_card(acc_id, name)

        for acc_id in list(self.cards.keys()):
            if acc_id not in self._boxes:
                c = self.cards.pop(acc_id)
                c.setParent(None)
        self._refresh_sel()

    def _ensure_card(self, acc_id: str, name: str) -> AccountProgressCard:
        card = self.cards.get(acc_id)
        if card is None:
            card = AccountProgressCard(acc_id, name)
            self.cards[acc_id] = card
            self.cards_box.addWidget(card)
        else:
            card.lb_name.setText(name)
        return card

    def current_account(self) -> str:
        for acc_id, b in self._boxes.items():
            if b.isChecked():
                return acc_id
        return ""

    def selected_accounts(self) -> list[str]:
        return [a for a, b in self._boxes.items() if b.isChecked()]

    def _refresh_sel(self):
        n = len(self.selected_accounts())
        self.lb_sel.setText(f"已选 {n} / {len(self._boxes)} 个账号" if self._boxes else "")
        self.empty.set_visible(not self._boxes)

    def _on_start(self):
        self.sig_start.emit(self.selected_accounts())

    # ------------------------------------------------------------------ 从头重刷
    def _on_restudy_toggled(self, checked: bool):
        """勾选变化 → 落盘到 gui_settings.json（与设置页共享同一配置）。"""
        store.ui_save({"skip_finished": not bool(checked)})
        self.sig_restudy.emit(bool(checked))

    def set_restudy(self, checked: bool):
        """外部（设置页）改了配置 → 同步勾选框，不触发回写。"""
        self.cb_restudy.blockSignals(True)
        self.cb_restudy.setChecked(bool(checked))
        self.cb_restudy.blockSignals(False)

    # ------------------------------------------------------------------ 日志
    def append_log(self, level: str, text: str):
        if level == "DEBUG" and not self.cb_verbose.isChecked():
            return
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
        cur.insertText(text.rstrip() + "\n", fmt)
        self.log.setTextCursor(cur)

        if self.cb_scroll.isChecked() and at_bottom:
            bar.setValue(bar.maximum())

    def clear_log(self):
        self.log.clear()

    def export_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出日志",
            f"leke-log-{time.strftime('%Y%m%d-%H%M%S')}.txt",
            "文本文件 (*.txt)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.log.toPlainText())
            QMessageBox.information(self, "导出成功", f"日志已保存到：\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    # ------------------------------------------------------------------ 转发
    def reset(self, acc_id: str):
        card = self.cards.get(acc_id)
        if card:
            card.reset()

    def set_total(self, acc_id: str, total: int):
        card = self.cards.get(acc_id)
        if card:
            card.set_total(total)

    def set_pass(self, acc_id: str, pass_no: int):
        card = self.cards.get(acc_id)
        if card:
            card.set_pass(pass_no)

    def set_cumulative(self, acc_id: str, total_done: int, failed: int):
        card = self.cards.get(acc_id)
        if card:
            card.set_cumulative(total_done, failed)

    def update_progress(self, acc_id: str, done: int, failed: int, current: str):
        card = self.cards.get(acc_id)
        if card:
            card.update_progress(done, failed, current)

    def set_current(self, acc_id: str, info):
        card = self.cards.get(acc_id)
        if card:
            card.set_current(info)
            self._wake_anim()

    def set_video_progress(self, acc_id: str, info):
        card = self.cards.get(acc_id)
        if card:
            card.set_video_progress(info)
            self._wake_anim()

    def set_stopping(self, acc_id: str = ""):
        for c in self._targets(acc_id):
            c.set_stopping()

    def set_finished(self, acc_id: str, status: str, msg: str):
        card = self.cards.get(acc_id)
        if card:
            card.set_finished(status, msg)

    def _targets(self, acc_id: str = "") -> list[AccountProgressCard]:
        if acc_id:
            c = self.cards.get(acc_id)
            return [c] if c else []
        return [c for c in self.cards.values() if c._active]

    # ------------------------------------------------------------------ 状态
    def set_mode_hint(self, text: str):
        self.lb_mode.setText(text or "")

    def sync_run_state(self, any_running: bool):
        """根据实际连接的后台状态同步按钮（多账号各自独立启停）。

        运行中所有「配置类」控件一律禁用：正在跑的任务用的是开始时的快照，
        中途改了也不会生效，留着能点只会让人以为生效了（v1.5.3 闭环修复）。
        """
        self.btn_start.setEnabled(not any_running)
        self.btn_stop.setEnabled(any_running)
        self.btn_start.setText("刷课中…" if any_running else "开始刷课")
        self.badge.set_kind("info" if any_running else "idle",
                            "运行中" if any_running else "待机")
        if any_running and not self._timer.isActive():
            self._timer.start()
            self._wake_anim()
        elif not any_running:
            self._timer.stop()
            self._anim.stop()
        self.cb_restudy.setEnabled(not any_running)
        self.cb_restudy.setToolTip("运行中不可修改；停止后再改，下一轮生效" if any_running else "")
        for b in self._boxes.values():
            b.setEnabled(not any_running)

    def _tick(self):
        for c in self.cards.values():
            c.tick()

    def _wake_anim(self):
        """把插值定时器唤醒（并给它 3 秒的观察窗口）。"""
        self._anim_budget = 30
        if not self._anim.isActive():
            self._anim.start()

    def _anim_tick(self):
        """100ms 一帧推进视频进度条。

        所有槽都播到片尾或没有心跳时自动停表（3 秒退避），下一次任务点/心跳
        信号会经 _wake_anim() 重新唤醒 —— 避免运行页挂着一只空转的定时器。
        """
        busy = False
        for c in self.cards.values():
            if c.animate():
                busy = True
        if busy:
            self._anim_budget = 30
        elif self._anim_budget > 0:
            self._anim_budget -= 1
        else:
            self._anim.stop()
