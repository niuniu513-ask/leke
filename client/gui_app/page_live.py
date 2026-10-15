# -*- coding: utf-8 -*-
"""学习通「课堂互动」页：签到 / 主题讨论 / 评分 / 投票 / 问卷 / 随堂练习。

为什么单独一页
--------------
乐课原本只做「课后刷课」（章节 / 视频 / 文档 / 章节测验，走 mooc2 网页协议）。
课堂内那些**实时活动**（老师临时发的签到、投票、随堂练习）走的是移动端协议
（mobilelearn / groupyd），是另一条链路 —— 见 ``api/cx_live.py``。
这一页把它们收在一处：选一门课 → 看到当前活动 → 按类型操作。

桌面端适配
----------
移动端才有的能力在桌面端换成等价输入：

* 二维码签到 → 不支持：enc 是老师端动态令牌，客户端无法获取，请用手机端完成
* 位置签到 → **手填地址 + 经纬度**（可保存为常用位置）
* 拍照签到 → **选本地图片**上传拿 objectId
* 人脸签到 → 用账号已采集的人脸（``face_id`` + ``face_enc``）

多账号
------
顶部账号条切账号，活动多账号可批量（签到 / 讨论支持一次给多个账号做）。
"""
from __future__ import annotations

import json
import os
import re

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from api.base import Account, Chaoxing
from api.cx_live import (
    SIGN_TYPE_LABEL, build_from_chaoxing, active_type_name,
)
from gui_app import profiles
from gui_app import theme as T
from gui_app.widgets import AccountBar, Badge, Card, EmptyState

#: 活动类型 → 操作按钮文案
_ACTION_TEXT = {
    2: "去签到", 54: "去签到", 74: "去签退",
    5: "参与讨论", 23: "去评分", 43: "去投票", 14: "去填写", 42: "去答题",
}

#: 支持操作的活动类型
_SUPPORTED = set(_ACTION_TEXT) | {4}


class _Task(QThread):
    """把一段可调用逻辑丢到后台线程跑（网络请求不走 UI 线程）。"""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):  # noqa: D102
        try:
            self.done.emit(self._fn())
        except Exception as exc:  # 任何异常都回传成文案，别让线程静默死掉
            self.failed.emit(f"{type(exc).__name__}: {exc}")


def parse_quiz_html(html: str):
    """从随堂练习页面 HTML 抠 ``quizList`` / ``active``。

    Returns ``(quiz_list, active, submitted)``；``submitted=True`` 表示已交过
    （页面里 ``activeStatus`` 不是 ``undefined``）。
    """
    status_match = re.search(r"activeStatus:\s*(.+?),", html or "")
    submitted = bool(status_match and status_match.group(1).strip() != "undefined")
    quiz_match = re.search(r"quizList\s*=\s*(\[.*\]);", html or "", re.S)
    active_match = re.search(r"active\s*=\s*(\{.*\});", html or "", re.S)
    quiz_list = []
    active = {}
    if quiz_match:
        try:
            quiz_list = json.loads(quiz_match.group(1))
        except ValueError:
            quiz_list = []
    if active_match:
        try:
            active = json.loads(active_match.group(1))
        except ValueError:
            active = {}
    return quiz_list, active, submitted


def _autofill_quiz(quiz_list: list) -> list:
    """把题目里的正确答案填进 ``personAnswer``（能抄就抄）。"""
    for quiz in quiz_list:
        if not isinstance(quiz, dict):
            continue
        pa = quiz.get("personAnswer")
        if not isinstance(pa, dict):
            pa = {}
            quiz["personAnswer"] = pa
        answers = quiz.get("answer") or []
        qtype = quiz.get("type")
        if qtype in (0, 3, 16):
            pa["myoption"] = pa.get("myoption") or _first_correct(answers)
        elif qtype == 1:
            if not pa.get("myoption"):
                pa["myoption"] = "".join(
                    str(o.get("name")) for o in answers if o.get("isanswer") is True)
        elif qtype == 2:
            blanks = pa.get("blankAnswer")
            if not isinstance(blanks, list):
                blanks = [{"content": ""} for _ in answers]
                pa["blankAnswer"] = blanks
            for i, blank in enumerate(blanks):
                if not blank.get("content") and i < len(answers):
                    blank["content"] = _strip_html(answers[i].get("content", "")).split(";")[0].strip()
        elif qtype in (4, 5, 6, 7):
            if not pa.get("content") and answers:
                pa["content"] = _strip_html(answers[0].get("answer", "")).split(";")[0].strip()
                pa.setdefault("recs", [])
                pa.setdefault("imgs", [])
    return quiz_list


def _first_correct(answers: list) -> str:
    for opt in answers:
        if opt.get("isanswer") is True:
            return str(opt.get("name", ""))
    return str(answers[0].get("name", "")) if answers else ""


def _strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html or "")).strip()


# ====================================================================== 主页面

class LivePage(QWidget):
    """学习通课堂互动页。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.book = profiles.load()
        self._cx = None            # 当前账号的 Chaoxing
        self._live = None          # 当前账号的 CXLive
        self._courses: list = []
        self._tasks: list = []     # 防 GC

        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(self._build_hero())
        root.addWidget(self._build_accounts())
        root.addWidget(self._build_course())
        root.addWidget(self._build_watch())
        root.addWidget(self._build_activities())
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

        self._shown_acc = None
        self._inited = False
        self._refresh_accounts()
        self._start_watch_timer()

    def showEvent(self, event):
        """每次进页面都重读账号档案；首次显示时才联网拉课程（别在主窗口启动时打请求）。"""
        super().showEvent(event)
        rebound = self._refresh_accounts()
        if not self._inited:
            self._inited = True
            self._load_courses()
        elif rebound:
            # 停留在别处时账号被换了（比如账号页切了账号）→ 回来顺手把课程重读一遍
            self._load_courses()

    # ------------------------------------------------------------ 构建
    def _build_hero(self) -> QFrame:
        hero = QFrame()
        hero.setObjectName("ZhihuishuHero")   # 复用同款渐变（品牌色已统一为主色）
        lay = QHBoxLayout(hero)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(14)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel("课堂互动 · 学习通")
        title.setObjectName("ZhihuishuHeroTitle")
        col.addWidget(title)
        sub = QLabel("签到 / 主题讨论 / 评分 / 投票 / 问卷 / 随堂练习")
        sub.setObjectName("ZhihuishuHeroSub")
        col.addWidget(sub)
        lay.addLayout(col, 1)

        self.btn_refresh = QPushButton("刷新活动")
        self.btn_refresh.setProperty("variant", "zhihuishu")
        self.btn_refresh.setMinimumHeight(38)
        self.btn_refresh.clicked.connect(self._load_activities)
        lay.addWidget(self.btn_refresh, 0, Qt.AlignVCenter)
        return hero

    def _build_accounts(self) -> QWidget:
        self.acc_bar = AccountBar()
        self.acc_bar.sig_switch.connect(self._switch_account)
        self.acc_bar.sig_add.connect(lambda: self._goto_account_page())
        return self.acc_bar

    def _build_course(self) -> QWidget:
        card = Card("课程", "选一门课，看它当前的课堂活动", icon="book")
        row = QHBoxLayout()
        row.setSpacing(10)
        self.course_box = QComboBox()
        self.course_box.setMinimumHeight(34)
        self.course_box.setMinimumWidth(320)
        self.course_box.currentIndexChanged.connect(self._on_course_changed)
        row.addWidget(QLabel("课程"))
        row.addWidget(self.course_box, 1)
        self.btn_courses = QPushButton("读取课程")
        self.btn_courses.setProperty("variant", "ghost")
        self.btn_courses.setMinimumHeight(34)
        self.btn_courses.clicked.connect(self._load_courses)
        row.addWidget(self.btn_courses)
        card.add(row)
        return card

    def _build_watch(self) -> QWidget:
        """新活动监控。

        移动端靠环信 IM 把老师的活动**实时推**到手机上（参考项目 push/easemob.dart）。
        Python 侧没有环信 SDK，硬搬要么自己实现 XMPP/WebSocket 握手（脆弱且无库支持），
        要么引入闭源 SDK —— 桌面端改用等价手段：**定时轮询活动列表 + 差集判定**，
        发现新 id 就提示。代价是延迟最多一个轮询周期，换来零额外依赖、无隐私外泄。
        """
        card = Card("新活动监控", "老师一发签到就能知道（桌面端用轮询替代手机会员推送）",
                    icon="info")
        row = QHBoxLayout()
        row.setSpacing(10)
        self.chk_watch = QCheckBox("开启监控")
        self.chk_watch.toggled.connect(self._on_watch_toggled)
        row.addWidget(self.chk_watch)
        row.addWidget(QLabel("间隔"))
        self.sp_watch = QSpinBox()
        self.sp_watch.setRange(10, 600)
        self.sp_watch.setSingleStep(10)
        self.sp_watch.setValue(30)
        self.sp_watch.setSuffix(" 秒")
        self.sp_watch.setMinimumHeight(30)
        self.sp_watch.setMinimumWidth(98)   # 三位数 + 后缀不被裁
        self.sp_watch.valueChanged.connect(self._apply_watch_interval)
        row.addWidget(self.sp_watch)
        row.addStretch(1)
        card.add(row)

        self.lb_watch = QLabel("未开启。开启后每 30 秒自动读一次当前课程的活动列表。")
        self.lb_watch.setObjectName("CardHint")
        self.lb_watch.setWordWrap(True)
        card.add(self.lb_watch)
        return card

    def _start_watch_timer(self):
        self._seen_ids: dict[str, set] = {}
        self._watch_busy = False
        self._poll_count = 0
        self._watch = QTimer(self)
        self._watch.timeout.connect(self._watch_tick)
        self._apply_watch_interval(self.sp_watch.value())
        self._watch.stop()

    def _apply_watch_interval(self, seconds: int):
        self._watch.setInterval(max(10, int(seconds)) * 1000)

    def _on_watch_toggled(self, on: bool):
        if on:
            course = self._current_course()
            if not course:
                self.chk_watch.setChecked(False)
                QMessageBox.information(self, "提示", "先读取并选中一门课程，再开启监控")
                return
            # 开启瞬间先记一份基线，避免把「已经存在的活动」全当成新活动报一遍
            self._seen_ids.pop(str(course.get("courseId", "")), None)
            self._watch.start()
            self.lb_watch.setText(
                f"监控中：每 {self.sp_watch.value()} 秒读一次，首个周期建立基线后开始提示。")
            self.log("✓ 已开启新活动监控")
            self._watch_tick()
        else:
            self._watch.stop()
            self.lb_watch.setText("未开启。开启后定时读取当前课程的活动列表。")
            self.log("已关闭新活动监控")

    def _watch_tick(self):
        if self._watch_busy:
            return
        course = self._current_course()
        if not course:
            return
        self._watch_busy = True

        def work():
            self._ensure_client()
            return self._live.active_list(course)

        def done(acts):
            self._watch_busy = False
            self._poll_count += 1
            key = str(course.get("courseId", ""))
            ids = {a.id for a in (acts or [])}
            seen = self._seen_ids.get(key)
            self._seen_ids[key] = ids
            if seen is None:
                self.lb_watch.setText(
                    f"监控中（已轮询 {self._poll_count} 次）｜基线 {len(ids)} 个活动")
                return
            fresh = [a for a in (acts or []) if a.id not in seen]
            self.lb_watch.setText(
                f"监控中（已轮询 {self._poll_count} 次）｜基线 {len(seen)} 个，"
                f"本次新增 {len(fresh)} 个")
            if fresh:
                self._alert_new(course, fresh)
                self._load_activities()   # 立刻把列表刷成最新，省得手动点

        def failed(msg):
            self._watch_busy = False
            self.log(f"✗ 监控轮询失败：{msg}")

        task = _Task(work, self)
        self._tasks.append(task)

        def finish(_r=None):
            if task in self._tasks:
                self._tasks.remove(task)

        task.done.connect(lambda r: (done(r), finish()))
        task.failed.connect(lambda m: (failed(m), finish()))
        task.start()

    def _alert_new(self, course: dict, fresh: list):
        """有新课内活动：日志 + 卡片高亮 + 任务栏闪烁（不弹模态挡人操作）。"""
        for act in fresh:
            self.log(f"🔔 新活动：{act.label}｜{act.name}（{act.type_name}）")
        first = fresh[0]
        self.lb_watch.setText(
            f"🔔 {len(fresh)} 个新活动：{first.label}｜{first.name}"
            f"  ——《{course.get('title', '')}》")
        win = self.window()
        try:
            QApplication.alert(win, 0)      # Windows 任务栏闪烁；非 Windows 平台自动忽略
        except Exception:
            pass

    def _build_activities(self) -> QWidget:
        card = Card("活动列表", "点右侧按钮参与；签到支持多种类型", icon="play")
        self.act_host = QWidget()
        self.act_lay = QVBoxLayout(self.act_host)
        self.act_lay.setContentsMargins(0, 0, 0, 0)
        self.act_lay.setSpacing(8)
        self.empty = EmptyState("inbox", "还没有活动",
                                "先选账号 → 读取课程 → 选课程，再点「刷新活动」")
        card.add(self.empty)
        card.add(self.act_host)
        return card

    def _build_log(self) -> QWidget:
        card = Card("运行日志", icon="info")
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(120)
        self.log_box.setMaximumBlockCount(500)
        card.add(self.log_box)
        return card

    # ------------------------------------------------------------ 账号
    def _refresh_accounts(self) -> bool:
        """重新读盘刷新账号条。返回是否发生了账号切换。

        账号没变就不动课程/活动（避免白清一遍）；变了要告诉调用方，好顺手重读课程。
        """
        self.book = profiles.load()
        acc_id = str(self.book.active or "")
        self.acc_bar.set_accounts(
            [(a.get("id"), self.book.display_name(a)) for a in self.book.accounts], acc_id)
        if getattr(self, "_shown_acc", None) == acc_id:
            return False
        self._shown_acc = acc_id
        self._rebind_account(acc_id, announce=False)
        return True

    def _switch_account(self, acc_id: str, load: bool = True):
        self._shown_acc = str(acc_id or "")
        self._rebind_account(acc_id, announce=True)
        if load:
            self._load_courses()

    def _rebind_account(self, acc_id: str, announce: bool):
        """把当前账号换成 acc_id 并落盘。

        ⚠ ``accounts.json`` 是**全 app 共用的文件**（账号页新增账号、课程页改勾选
        都会重写它），而本页 __init__ 时读的是一份**快照**。若直接
        ``snapshot.active = x; snapshot.save()``，会把别的页面此后的改动整个抹掉
        —— 所以必须先重新读盘、只改 active，再写回。
        """
        book = profiles.load()
        acc_id = str(acc_id or "")
        # 只有真的换了账号才落盘：主窗口一启动就会构造本页，无脑 save() 等于
        # 每次开程序都重写一遍用户档案（多余且有风险）
        if str(book.active or "") != acc_id:
            book.active = acc_id
            book.save()
        self.book = book
        self.acc_bar.set_accounts(
            [(a.get("id"), book.display_name(a)) for a in book.accounts], acc_id)
        self._cx = None
        self._live = None
        self._courses = []
        self._watch_busy = False
        self._seen_ids = {}
        self.course_box.blockSignals(True)
        self.course_box.clear()
        self.course_box.blockSignals(False)
        self._clear_activities()
        if announce:
            self.log(f"切换账号：{book.display_name(book.get(acc_id) or {})}")

    def _goto_account_page(self):
        win = self.window()
        if hasattr(win, "goto"):
            win.goto("account")

    def log(self, text: str):
        self.log_box.appendPlainText(text)

    def _spawn(self, fn, on_done, busy: bool = True):
        if busy:
            self.btn_refresh.setEnabled(False)
        task = _Task(fn, self)
        self._tasks.append(task)

        def _finish():
            self.btn_refresh.setEnabled(True)
            if task in self._tasks:
                self._tasks.remove(task)

        task.done.connect(lambda result: (on_done(result), _finish()))
        task.failed.connect(lambda msg: (self.log(f"✗ {msg}"), _finish()))
        task.start()

    # ------------------------------------------------------------ 会话
    def _ensure_client(self):
        acc_id = str(self.book.active or "")
        if self._cx is not None:
            return
        payload = profiles.account_payload(self.book, acc_id)
        acc = Account(payload.get("username", ""), payload.get("password", ""))
        cx = Chaoxing(acc, session_key=acc_id,
                      cookie_path=payload.get("cookie_path", ""))
        cx.sync_cookies_from_file()
        self._cx = cx
        self._live = build_from_chaoxing(cx)

    def _load_courses(self):
        def work():
            self._ensure_client()
            return self._cx.get_course_list()

        self.log("读取课程列表…")

        def done(courses):
            self._courses = courses or []
            self.course_box.blockSignals(True)
            self.course_box.clear()
            for c in self._courses:
                self.course_box.addItem(f"{c.get('title', '')}", c)
            self.course_box.blockSignals(False)
            self.log(f"✓ 读到 {len(self._courses)} 门课")
            if self._courses:
                self._load_activities()

        self._spawn(work, done)

    def _current_course(self):
        data = self.course_box.currentData()
        return data if isinstance(data, dict) else None

    def _on_course_changed(self, _index: int = 0):
        """换课程：把该课的活动基线丢掉重建，避免把老活动当新活动报一遍。"""
        course = self._current_course()
        if course:
            self._seen_ids.pop(str(course.get("courseId", "")), None)
        # 构造期（_watch 还没建好）也可能被 clear() 的索引变化信号打进来，得能扛住
        watch = getattr(self, "_watch", None)
        if watch is not None and watch.isActive():
            self._watch_busy = False
        self._load_activities()

    # ------------------------------------------------------------ 活动
    def _clear_activities(self):
        while self.act_lay.count():
            item = self.act_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self.empty.set_visible(True)

    def _load_activities(self):
        course = self._current_course()
        if not course:
            return
        self._clear_activities()
        self.log(f"读取《{course.get('title', '')}》活动…")

        def work():
            self._ensure_client()
            return self._live.active_list(course)

        def done(acts):
            self._clear_activities()
            if not acts:
                self.empty.set_text("暂无活动", "这门课当前没有进行中的课堂活动")
                self.empty.set_visible(True)
                self.log("✓ 没有活动")
                return
            self.empty.set_visible(False)
            for act in acts:
                self.act_lay.addWidget(self._activity_row(act, course))
            self.log(f"✓ 读到 {len(acts)} 个活动")

        self._spawn(work, done)

    def _activity_row(self, act, course) -> QWidget:
        row = QFrame()
        row.setObjectName("CardSub")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)

        kind = "info" if act.status else "idle"
        lay.addWidget(Badge(act.label if act.is_sign else act.type_name, kind), 0)

        col = QVBoxLayout()
        col.setSpacing(2)
        name = QLabel(act.name or "(无标题)")
        name.setWordWrap(True)
        col.addWidget(name)
        meta = " · ".join(x for x in [
            _fmt_time(act.start_time),
            act.description,
            f"ID {act.id}",
        ] if x)
        hint = QLabel(meta)
        hint.setObjectName("CardHint")
        hint.setWordWrap(True)
        col.addWidget(hint)
        lay.addLayout(col, 1)

        if act.type in _SUPPORTED:
            btn = QPushButton(_ACTION_TEXT.get(act.type, "参与"))
            btn.setProperty("variant", "ghost")
            btn.setMinimumHeight(32)
            btn.clicked.connect(lambda _=False, a=act, c=course: self._open_action(a, c))
            lay.addWidget(btn, 0, Qt.AlignVCenter)
        else:
            tip = QLabel("暂不支持")
            tip.setObjectName("CardHint")
            lay.addWidget(tip, 0, Qt.AlignVCenter)
        return row

    def _open_action(self, act, course):
        self._ensure_client()
        ctx = {"cx": self._cx, "live": self._live, "course": course,
               "active": act, "log": self.log}
        if act.type in (2, 54, 74):
            dlg = SignDialog(ctx, self)
        elif act.type == 5:
            dlg = DiscussDialog(ctx, self)
        else:
            dlg = ActionDialog(ctx, self)
        dlg.exec()


def _fmt_time(ms: int) -> str:
    if not ms:
        return ""
    try:
        import time as _t
        return _t.strftime("%m-%d %H:%M", _t.localtime(ms / 1000))
    except Exception:
        return ""


# ====================================================================== 签到对话框

class SignDialog(QDialog):
    """签到对话框：按 ``otherId`` 渲染对应输入，支持多账号批量。"""

    def __init__(self, ctx: dict, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.live = ctx["live"]
        self.course = ctx["course"]
        self.active = ctx["active"]
        self._tasks: list = []
        self.info: dict = {}
        self.sign_type = self.active.sign_type or ""
        self.face_id = ""
        self.object_id = ""
        self._photo_path = ""

        self.setWindowTitle("课堂签到")
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        root.setSpacing(12)

        head = QLabel(self.active.name or "签到")
        head.setWordWrap(True)
        root.addWidget(head)

        self.tip = QLabel("正在读取签到要求…")
        self.tip.setObjectName("CardHint")
        self.tip.setWordWrap(True)
        root.addWidget(self.tip)

        # ---- 各类型输入区（按需显示）----
        self.code_wrap, self.code_edit = self._input_row("签到码", "输入数字签到码")
        self.pattern_wrap, self.pattern_edit = self._input_row(
            "手势", "按九宫格顺序输入数字，如 1478")
        root.addWidget(self.code_wrap)
        root.addWidget(self.pattern_wrap)

        self.loc_row = QWidget()
        ll = QVBoxLayout(self.loc_row)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        self.addr_edit = QLineEdit()
        self.addr_edit.setPlaceholderText("地址描述（如：石家庄铁道大学）")
        self.lat_edit = QLineEdit()
        self.lat_edit.setPlaceholderText("纬度 latitude，如 38.0338")
        self.lng_edit = QLineEdit()
        self.lng_edit.setPlaceholderText("经度 longitude，如 114.5100")
        ll.addWidget(QLabel("位置签到：手填地址 + 经纬度"))
        ll.addWidget(self.addr_edit)
        ll.addWidget(self.lat_edit)
        ll.addWidget(self.lng_edit)
        root.addWidget(self.loc_row)

        self.photo_row = QWidget()
        pl = QHBoxLayout(self.photo_row)
        pl.setContentsMargins(0, 0, 0, 0)
        self.photo_label = QLabel("拍照签到：未选择图片")
        self.photo_label.setObjectName("CardHint")
        self.btn_photo = QPushButton("选择图片")
        self.btn_photo.setProperty("variant", "ghost")
        self.btn_photo.clicked.connect(self._pick_photo)
        pl.addWidget(self.photo_label, 1)
        pl.addWidget(self.btn_photo)
        root.addWidget(self.photo_row)

        self.face_check = QCheckBox("使用已采集人脸（人脸签到）")
        root.addWidget(self.face_check)

        # ---- 多账号 ----
        self.acc_checks: dict = {}
        acc_box = QWidget()
        al = QVBoxLayout(acc_box)
        al.setContentsMargins(0, 6, 0, 0)
        al.setSpacing(4)
        al.addWidget(QLabel("参与账号"))
        book = profiles.load()
        for acc in book.accounts:
            cb = QCheckBox(book.display_name(acc))
            cb.setChecked(acc.get("id") == book.active)
            self.acc_checks[str(acc.get("id"))] = cb
            al.addWidget(cb)
        root.addWidget(acc_box)

        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumBlockCount(200)
        self.out.setMinimumHeight(90)
        root.addWidget(self.out)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_do = QPushButton("签到")
        self.btn_do.setEnabled(False)
        self.btn_do.clicked.connect(self._do_sign)
        close = QPushButton("关闭")
        close.setProperty("variant", "ghost")
        close.clicked.connect(self.reject)
        btns.addWidget(close)
        btns.addWidget(self.btn_do)
        root.addLayout(btns)

        self._load_info()

    # ---------------------------------------------------------------
    def _input_row(self, title: str, placeholder: str):
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        lay.addWidget(QLabel(title))
        lay.addWidget(edit)
        return wrap, edit

    def _load_info(self):
        def work():
            info = self.live.active_info_web(self.active.id)
            other = info.get("otherId")
            if other is not None and not self.sign_type:
                try:
                    from api.cx_live import SIGN_TYPE_INDEX
                    self.sign_type = SIGN_TYPE_INDEX.get(int(other), "")
                except (TypeError, ValueError):
                    pass
            return info

        def done(info):
            self.info = info or {}
            self._apply_info()
            self.btn_do.setEnabled(True)

        task = _Task(work, self)
        self._tasks.append(task)
        task.done.connect(done)
        task.failed.connect(lambda m: (self.out.appendPlainText(f"✗ {m}"),
                                       self.btn_do.setEnabled(True)))
        task.start()

    def _apply_info(self):
        info = self.info
        label = SIGN_TYPE_LABEL.get(self.sign_type, "签到")
        bits = [f"签到类型：{label}"]
        if info.get("numberCount"):
            bits.append(f"签到码 {info['numberCount']} 位")
        if info.get("locationText"):
            bits.append(f"指定地点：{info['locationText']}")
        if info.get("locationRange"):
            bits.append(f"范围 {info['locationRange']} 米")
        if info.get("ifphoto") == 1:
            bits.append("需要拍照")
        if info.get("openCheckFaceFlag") == 1:
            bits.append("需要人脸")
            self.face_check.setChecked(True)
        if info.get("showVCode") == 1:
            bits.append("需要验证码（建议先用手机端完成一次）")
        self.tip.setText(" ｜ ".join(bits))

        self.code_wrap.setVisible(self.sign_type == "code")
        self.pattern_wrap.setVisible(self.sign_type == "pattern")
        self.loc_row.setVisible(self.sign_type == "location")
        self.photo_row.setVisible(self.sign_type == "normal" and info.get("ifphoto") == 1)
        # 二维码签到不提供入口：enc 是老师端动态令牌，客户端拿不到，做了也只能手动粘贴
        if self.sign_type == "qrcode":
            bits.append("二维码签到需扫老师投屏的动态码，桌面端无法代签，请用手机端完成")
            self.tip.setText(" ｜ ".join(bits))
            self.btn_do.setEnabled(False)

    def _pick_photo(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择签到图片", "",
                                              "图片 (*.jpg *.jpeg *.png)")
        if not path:
            return
        self._photo_path = path
        self.photo_label.setText(f"拍照签到：{os.path.basename(path)}")

    def _identity_of(self, acc_id: str):
        book = profiles.load()
        return book.get(acc_id) or {}

    def _client_for(self, acc_id: str):
        book = profiles.load()
        payload = profiles.account_payload(book, acc_id)
        acc = Account(payload.get("username", ""), payload.get("password", ""))
        cx = Chaoxing(acc, session_key=acc_id, cookie_path=payload.get("cookie_path", ""))
        cx.sync_cookies_from_file()
        return cx, build_from_chaoxing(cx)

    def _do_sign(self):
        targets = [aid for aid, cb in self.acc_checks.items() if cb.isChecked()]
        if not targets:
            QMessageBox.information(self, "提示", "请至少勾选一个账号")
            return
        code = self.code_edit.text().strip()
        pattern = self.pattern_edit.text().strip()
        if self.sign_type == "code" and not code:
            QMessageBox.information(self, "提示", "请先输入数字签到码")
            return
        if self.sign_type == "pattern" and not pattern:
            QMessageBox.information(self, "提示", "请先输入手势顺序（如 1478）")
            return
        addr = self.addr_edit.text().strip()
        lat = self.lat_edit.text().strip()
        lng = self.lng_edit.text().strip()
        use_face = self.face_check.isChecked()
        photo = getattr(self, "_photo_path", "")
        sign_type = self.sign_type
        course = self.course
        active_id = self.active.id
        info = self.info
        btn = self.btn_do
        btn.setEnabled(False)

        def work():
            lines = []
            for acc_id in targets:
                cx, live = self._client_for(acc_id)
                name = profiles.load().display_name(
                    profiles.load().get(acc_id) or {})
                face_id = face_enc = None
                if use_face:
                    face_id = live.face_id() or ""
                    if face_id:
                        face_enc = live.face_enc(active_id, face_id)
                object_id = None
                if photo:
                    object_id = live.upload_image(photo)
                try:
                    if sign_type == "location":
                        res = live.sign_location(
                            course, active_id, addr or "未知位置",
                            float(lat or 0), float(lng or 0),
                            face_id=face_id, face_enc=face_enc)
                    elif sign_type in ("code", "pattern"):
                        code_text = pattern if sign_type == "pattern" else code
                        err = live.check_sign_code(active_id, code_text)
                        if err:
                            lines.append(f"  {name}: 校验未通过 - {err}")
                            continue
                        res = live.sign_code(course, active_id, code_text)
                    else:
                        res = live.sign_normal(course, active_id, object_id=object_id)
                except ValueError:
                    lines.append(f"  {name}: 经纬度格式不对")
                    continue
                lines.append(f"  {name}: {_sign_result_text(res)}")
            return "\n".join(lines)

        def done(lines):
            self.out.appendPlainText(lines or "(无输出)")
            btn.setEnabled(True)

        task = _Task(work, self)
        self._tasks.append(task)
        task.done.connect(done)
        task.failed.connect(lambda m: (self.out.appendPlainText(f"✗ {m}"),
                                       btn.setEnabled(True)))
        task.start()


def _sign_result_text(res) -> str:
    text = str(res or "").strip()
    if not text:
        return "无响应（可能网络异常）"
    if text == "success":
        return "✓ 签到成功"
    if text == "success2":
        return "已过截止时间"
    if text.startswith("validate"):
        return f"需要验证码：{text}"
    return text


# ====================================================================== 主题讨论

class DiscussDialog(QDialog):
    """主题讨论：看帖 + 发帖（多账号）。"""

    def __init__(self, ctx: dict, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.live = ctx["live"]
        self.active = ctx["active"]
        self._tasks: list = []
        self.topic_uuid = ""
        self.topic_id = ""
        try:
            self.topic_id = str((self.active.raw or {}).get("extraInfo", {}).get("topicId")
                                or self.active.raw.get("topicId") or "")
        except AttributeError:
            self.topic_id = ""

        self.setWindowTitle("主题讨论")
        self.setMinimumWidth(560)
        self.setMinimumHeight(560)
        root = QVBoxLayout(self)
        root.setSpacing(10)

        title = QLabel(self.active.name or "主题讨论")
        title.setWordWrap(True)
        root.addWidget(title)

        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        root.addWidget(self.body, 1)

        self.comment = QLineEdit()
        self.comment.setPlaceholderText("输入回复内容（至少 10 字）")
        root.addWidget(self.comment)

        self.anon = QCheckBox("匿名回复")
        root.addWidget(self.anon)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_post = QPushButton("发布回复")
        self.btn_post.clicked.connect(self._post)
        close = QPushButton("关闭")
        close.setProperty("variant", "ghost")
        close.clicked.connect(self.reject)
        btns.addWidget(close)
        btns.addWidget(self.btn_post)
        root.addLayout(btns)

        self._load()

    def _load(self):
        def work():
            if not self.topic_id:
                return "（这条活动没带 topicId，可能不是主题讨论）", []
            detail = self.live.topic_detail(self.topic_id)
            self.topic_uuid = str(detail.get("uuid") or "")
            replies = self.live.topic_replies(self.topic_uuid) if self.topic_uuid else []
            head = f"【{detail.get('createrName', '')}】{detail.get('title', '')}\n{detail.get('text_content', '')}"
            items = [f"— {r.get('creater_name', '')}：{r.get('content', '')}" for r in replies]
            return head, items

        task = _Task(work, self)
        self._tasks.append(task)
        task.done.connect(lambda r: self.body.setPlainText(
            r[0] + "\n\n" + ("\n".join(r[1]) if r[1] else "(暂无回复)")))
        task.failed.connect(lambda m: self.body.setPlainText(f"✗ {m}"))
        task.start()

    def _post(self):
        content = self.comment.text().strip()
        if len(content) < 10:
            QMessageBox.information(self, "提示", "回复内容至少 10 个字")
            return
        if not self.topic_uuid:
            QMessageBox.information(self, "提示", "还没拿到话题 uuid，稍后再试")
            return
        anon = self.anon.isChecked()

        def work():
            err = self.live.topic_add_reply(self.topic_uuid, content, anon)
            return "✓ 发布成功" if err is None else f"✗ {err}"

        self.btn_post.setEnabled(False)
        task = _Task(work, self)
        self._tasks.append(task)
        task.done.connect(lambda m: (self.body.appendPlainText(m), self.btn_post.setEnabled(True)))
        task.failed.connect(lambda m: (self.body.appendPlainText(f"✗ {m}"),
                                       self.btn_post.setEnabled(True)))
        task.start()


# ====================================================================== 其它互动

class ActionDialog(QDialog):
    """评分 / 投票 / 问卷 / 随堂练习的通用操作面板。"""

    def __init__(self, ctx: dict, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.live = ctx["live"]
        self.course = ctx["course"]
        self.active = ctx["active"]
        self._tasks: list = []
        self.data: dict = {}
        self.inputs: list = []      # [(key, widget, kind)]

        self.setWindowTitle(active_type_name(self.active.type))
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        root = QVBoxLayout(self)
        root.setSpacing(10)

        self.title = QLabel(self.active.name or active_type_name(self.active.type))
        self.title.setWordWrap(True)
        root.addWidget(self.title)

        self.form_host = QWidget()
        self.form = QVBoxLayout(self.form_host)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setSpacing(8)
        holder = QScrollArea()
        holder.setWidgetResizable(True)
        holder.setFrameShape(QFrame.NoFrame)
        holder.setWidget(self.form_host)
        root.addWidget(holder, 1)

        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumBlockCount(200)
        self.out.setMinimumHeight(80)
        root.addWidget(self.out)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_submit = QPushButton("提交")
        self.btn_submit.clicked.connect(self._submit)
        close = QPushButton("关闭")
        close.setProperty("variant", "ghost")
        close.clicked.connect(self.reject)
        btns.addWidget(close)
        btns.addWidget(self.btn_submit)
        root.addLayout(btns)

        self._load()

    def _load(self):
        kind = self.active.type

        def work():
            if kind == 42:   # 随堂练习：从活动页 HTML 里抠题目
                html = self.live.fetch_html(self.active.url)
                quiz_list, active_data, submitted = parse_quiz_html(html)
                return {"kind": "quiz", "list": quiz_list, "active": active_data,
                        "submitted": submitted}
            if kind == 23:   # 评分
                return {"kind": "evaluate", "info": self.live.active_info_web(self.active.id)}
            return {"kind": "quiz_detail",
                    "detail": self.live.quiz_detail(self.active.id, v2=True)}

        task = _Task(work, self)
        self._tasks.append(task)
        task.done.connect(self._render)
        task.failed.connect(lambda m: self.out.appendPlainText(f"✗ {m}"))
        task.start()

    # ---------------------------------------------------------------
    def _render(self, data):
        self.data = data or {}
        kind = self.data.get("kind")
        if kind == "quiz":
            if self.data.get("submitted"):
                self.out.appendPlainText("您已提交过本次测验")
            self._render_quiz(self.data.get("list") or [])
        elif kind == "evaluate":
            self._render_evaluate(self.data.get("info") or {})
        else:
            self._render_quiz_detail(self.data.get("detail") or {})

    def _add_label(self, text: str):
        lab = QLabel(text)
        lab.setWordWrap(True)
        self.form.addWidget(lab)

    def _render_quiz(self, quiz_list: list):
        if not quiz_list:
            self._add_label("（没解析到题目，可能活动已结束）")
            return
        for i, quiz in enumerate(quiz_list, 1):
            answers = quiz.get("answer") or []
            options = "；".join(
                f"{o.get('name')}. {_strip_html(o.get('content', ''))}"
                + ("✔" if o.get("isanswer") is True else "")
                for o in answers)
            self._add_label(f"{i}. [{_quiz_type_name(quiz.get('type'))}] "
                            f"{_strip_html(quiz.get('content', ''))}")
            if options:
                self._add_label(f"    选项：{options}")
            edit = QLineEdit()
            edit.setPlaceholderText("留空 = 用页面里的正确答案")
            self.form.addWidget(edit)
            self.inputs.append((i - 1, edit, "quiz"))

    def _render_quiz_detail(self, detail: dict):
        questions = detail.get("questionlist") or []
        if not questions:
            self._add_label("（没读到题目，可能活动已结束或不是这类活动）")
            return
        for i, q in enumerate(questions, 1):
            answers = q.get("answer") or []
            options = "；".join(f"{o.get('name')}. {_strip_html(o.get('content', ''))}"
                                for o in answers)
            self._add_label(f"{i}. [{_quiz_type_name(q.get('type'))}] "
                            f"{_strip_html(q.get('content', ''))}")
            if options:
                self._add_label(f"    选项：{options}")
            edit = QLineEdit()
            edit.setPlaceholderText("填选项字母（多选连写）或答案文字")
            self.form.addWidget(edit)
            self.inputs.append((str(q.get("id")), edit, q.get("type")))

    def _render_evaluate(self, info: dict):
        norms = info.get("normList") or []
        if norms:
            for i, norm in enumerate(norms, 1):
                self._add_label(f"评分标准 {i}：{_strip_html(norm.get('content', ''))}"
                                f"（0-{norm.get('score')}）")
                edit = QLineEdit()
                edit.setPlaceholderText(f"0-{norm.get('score')}")
                self.form.addWidget(edit)
                self.inputs.append((i - 1, edit, "norm"))
        else:
            self._add_label("请输入总分（0-100）")
            edit = QLineEdit()
            self.form.addWidget(edit)
            self.inputs.append(("total", edit, "total"))
        self.comment = QLineEdit()
        self.comment.setPlaceholderText("评语（可留空）")
        self.form.addWidget(self.comment)

    # ---------------------------------------------------------------
    def _submit(self):
        kind = self.data.get("kind")
        self.btn_submit.setEnabled(False)

        def work():
            if kind == "quiz":
                return self._submit_quiz()
            if kind == "evaluate":
                return self._submit_evaluate()
            return self._submit_quiz_detail()

        task = _Task(work, self)
        self._tasks.append(task)
        task.done.connect(lambda m: (self.out.appendPlainText(m),
                                     self.btn_submit.setEnabled(True)))
        task.failed.connect(lambda m: (self.out.appendPlainText(f"✗ {m}"),
                                       self.btn_submit.setEnabled(True)))
        task.start()

    def _submit_quiz(self) -> str:
        quiz_list = self.data.get("list") or []
        for idx, edit, _kind in self.inputs:
            if edit.text().strip():
                quiz_list[idx]["personAnswer"] = {"myoption": edit.text().strip()}
        quiz_list = _autofill_quiz(quiz_list)
        payload = json.dumps(quiz_list, ensure_ascii=False)
        res = self.live.quiz_submit(
            str(self.course.get("clazzId", "")), str(self.course.get("courseId", "")),
            self.active.id, payload)
        return f"提交结果：{res}"

    def _submit_quiz_detail(self) -> str:
        qtype = self.active.type
        qs = (self.data.get("detail") or {}).get("questionlist") or []
        if qtype == 43:      # 投票
            if not qs:
                return "没有题目"
            qid = str(qs[0].get("id"))
            option = self.inputs[0][1].text().strip() if self.inputs else ""
            if not option:
                return "请填选项字母"
            res = self.live.vote_submit(
                str(self.course.get("courseId", "")), str(self.course.get("clazzId", "")),
                self.active.id, qid, option)
            return f"投票结果：{res}"
        if qtype == 14:      # 问卷
            answers = {}
            for qid, edit, _t in self.inputs:
                text = edit.text().strip()
                if text:
                    answers[qid] = [ch for ch in text] if len(text) > 1 and text.isalpha() else [text]
            res = self.live.questionnaire_submit(
                str(self.course.get("courseId", "")), str(self.course.get("clazzId", "")),
                self.active.id, answers)
            return f"问卷结果：{res}"
        return "该类型暂不支持自动提交"

    def _submit_evaluate(self) -> str:
        values = []
        for key, edit, _t in self.inputs:
            text = edit.text().strip()
            if not text:
                return "请把每一项评分都填上"
            try:
                values.append(int(text))
            except ValueError:
                return "评分必须是整数"
        total = sum(values) if values else 0
        content = self.comment.text() if hasattr(self, "comment") else ""
        res = self.live.evaluate_submit(
            self.active.id, str(self.course.get("clazzId", "")),
            str(self.course.get("courseId", "")), total,
            content=content, score_list=values)
        return "✓ 评分成功" if res else "✗ 评分失败"


def _quiz_type_name(qtype) -> str:
    return {0: "单选", 1: "多选", 2: "填空", 3: "判断", 4: "简答",
            5: "名词解释", 6: "论述", 7: "计算", 16: "判断"}.get(qtype, "题目")
