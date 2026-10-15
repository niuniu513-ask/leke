# -*- coding: utf-8 -*-
"""课程页：课程列表 + 章节进度 + 勾选要刷的课 + 逐课程选择章节。"""
from __future__ import annotations
import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QScrollArea, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui_app import theme as T
from gui_app.widgets import (
    Badge, Card, CheckDelegate, EmptyState, ProgressDelegate,
)

COL_CHECK, COL_NAME, COL_CHAP, COL_PROG, COL_REST, COL_CID, COL_CLZ = range(7)


class CoursesPage(QWidget):
    sig_request_courses = Signal(bool)     # with_progress
    sig_selection_changed = Signal(list)   # 选中的课程 dict 列表
    sig_account_changed = Signal(str)      # 切换了要查看哪个账号的课程
    sig_selection_committed = Signal(str, list)  # acc_id, 勾选的课程 id（写回档案）
    sig_chapters_request = Signal(str, dict)     # acc_id, course —— 请求拉取章节
    sig_chapters_saved = Signal(str, str, object)  # acc_id, courseId, point_ids|None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.courses: list[dict] = []
        self.progress: dict[int, dict] = {}
        self._loading = False
        self._saved: set[str] = set()
        # 章节缓存：(acc_id, courseId) -> points 列表；切换账号/重新读取课程时清空
        self._chapter_cache: dict[tuple[str, str], list] = {}
        # 章节选择摘要：courseId -> 已选数（None = 全部），用于按钮文案
        self._chap_sel: dict[str, list | None] = {}
        self._chap_btns: dict[int, QPushButton] = {}
        self._pending_dialog: tuple[str, dict] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        root.addWidget(self._build_table_card(), 1)
        root.addLayout(self._build_foot())

    # ------------------------------------------------------------------ 构建
    def _build_table_card(self) -> Card:
        card = Card(icon="book", title="课程与进度")

        # 账号选择 + 「读取课程」并进卡头右侧：它们只作用于这张表，
        # 之前单独占一张工具卡（还拖着三行长说明），页面白顶一整行（v1.6.2）。
        lab = QLabel("查看账号")
        lab.setObjectName("FieldLabel")
        self.cb_account = QComboBox()
        self.cb_account.setMinimumHeight(32)
        self.cb_account.setMinimumWidth(180)
        self.cb_account.currentIndexChanged.connect(self._on_account_changed)

        self.btn_refresh = QPushButton("读取该账号的课程")
        self.btn_refresh.setProperty("variant", "primary")
        self.btn_refresh.setMinimumHeight(32)
        self.btn_refresh.clicked.connect(lambda: self.sig_request_courses.emit(True))

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(lab)
        head.addWidget(self.cb_account)
        head.addWidget(self.btn_refresh)
        card.add_header(head)

        # 表格作用域的操作（全选/全不选/只选未完成）紧贴表格上方
        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_all = QPushButton("全选")
        self.btn_all.setMinimumHeight(32)
        self.btn_all.clicked.connect(lambda: self._set_all(True))
        self.btn_none = QPushButton("全不选")
        self.btn_none.setMinimumHeight(32)
        self.btn_none.clicked.connect(lambda: self._set_all(False))
        self.btn_unfinished = QPushButton("只选未完成的")
        self.btn_unfinished.setMinimumHeight(32)
        self.btn_unfinished.clicked.connect(self._select_unfinished)

        self.badge_sel = Badge("已选 0 门", "idle")

        row.addWidget(self.btn_all)
        row.addWidget(self.btn_none)
        row.addWidget(self.btn_unfinished)
        row.addStretch(1)
        row.addWidget(self.badge_sel)
        card.add(row)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["", "课程名称", "章节", "进度", "待完成", "课程 ID", "班级 ID"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setItemDelegateForColumn(COL_CHECK, CheckDelegate(self.table))
        self.table.setItemDelegateForColumn(COL_PROG, ProgressDelegate(self.table))
        self.table.verticalHeader().setDefaultSectionSize(46)

        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_CHECK, QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        hh.setSectionResizeMode(COL_CHAP, QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_PROG, QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_REST, QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_CID, QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_CLZ, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_CHECK, 46)
        self.table.setColumnWidth(COL_CHAP, 118)
        self.table.setColumnWidth(COL_PROG, 190)
        self.table.setColumnWidth(COL_REST, 84)
        self.table.setColumnWidth(COL_CID, 110)
        self.table.setColumnWidth(COL_CLZ, 110)
        hh.setHighlightSections(False)
        self.table.itemChanged.connect(self._on_item_changed)

        # 空态与表格共用一块区域：没数据时不留一张空表格
        self.empty = EmptyState(
            "folder", "还没有课程数据",
            "点上方「读取该账号的课程」拉取本学期课程。",
        )
        self.slot = QStackedWidget()
        self.slot.addWidget(self.table)
        self.slot.addWidget(self.empty)
        self.slot.setCurrentIndex(1)
        card.add(self.slot)
        return card

    def _build_foot(self) -> QHBoxLayout:
        foot = QHBoxLayout()
        foot.setSpacing(16)
        self.lb_total = QLabel("尚未读取课程")
        self.lb_total.setObjectName("CardHint")
        self.lb_sel = QLabel("")
        self.lb_sel.setObjectName("CardHint")
        foot.addWidget(self.lb_total)
        foot.addStretch(1)
        foot.addWidget(self.lb_sel)
        return foot

    # ------------------------------------------------------------------ 账号
    def set_accounts(self, accounts: list[tuple[str, str]], current: str = ""):
        """刷新账号下拉框。accounts = [(acc_id, 显示名), …]"""
        self.cb_account.blockSignals(True)
        self.cb_account.clear()
        for acc_id, name in accounts:
            self.cb_account.addItem(name, acc_id)
        idx = self.cb_account.findData(current)
        self.cb_account.setCurrentIndex(max(0, idx))
        self.cb_account.blockSignals(False)

    def current_account(self) -> str:
        return str(self.cb_account.currentData() or "")

    def _on_account_changed(self, *_):
        acc_id = self.current_account()
        if not acc_id:
            return
        # 切账号：清掉上一个账号的课程与进度，等主窗口把缓存灌进来
        self.courses = []
        self.progress = {}
        self._chap_sel = {}
        self.invalidate_chapter_cache()
        self.table.setRowCount(0)
        self.lb_total.setText("尚未读取该账号的课程")
        self.lb_sel.setText("")
        self.empty.set_text("还没有课程数据",
                            "点上方「读取该账号的课程」拉取本学期课程。")
        self.slot.setCurrentIndex(1)
        self.sig_account_changed.emit(acc_id)

    # ------------------------------------------------------------------ 数据
    def set_loading(self, loading: bool):
        self._loading = loading
        self.btn_refresh.setEnabled(not loading)
        self.btn_refresh.setText("读取中…" if loading else "读取该账号的课程")
        self.cb_account.setEnabled(not loading)
        if loading and not self.courses:
            self.empty.set_text("正在读取课程…", "取决于课程数量，通常需要十几秒。")
            self.slot.setCurrentIndex(1)

    def set_saved(self, course_ids: list[str]):
        """该账号上次勾选的课程 id（来自账号档案 / config.ini）。"""
        self._saved = {str(x).strip() for x in (course_ids or []) if str(x).strip()}

    def fill(self, courses: list[dict]):
        self.invalidate_chapter_cache()
        self.courses = list(courses)
        self.progress = {}
        self._chap_btns = {}
        self.table.blockSignals(True)
        self.table.setRowCount(len(courses))
        for r, c in enumerate(courses):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            chk.setCheckState(Qt.Checked if str(c.get("courseId", "")) in self._saved
                              else Qt.Unchecked)
            self.table.setItem(r, COL_CHECK, chk)

            name = QTableWidgetItem(str(c.get("title", "")))
            name.setToolTip(str(c.get("title", "")))
            name.setData(Qt.UserRole + 10, c)
            self.table.setItem(r, COL_NAME, name)

            # 章节：打开按课程选择章节的对话框
            cid = str(c.get("courseId", ""))
            btn = QPushButton(self._chap_btn_text(cid))
            btn.setObjectName("ChapterBtn")
            btn.setMinimumHeight(28)
            btn.setToolTip("选择这门课要刷哪些章节（不选 = 刷全部）")
            btn.clicked.connect(lambda _=False, course=c: self._open_chapters(course))
            self.table.setCellWidget(r, COL_CHAP, btn)
            self._chap_btns[r] = btn

            prog = QTableWidgetItem()
            prog.setData(Qt.UserRole, (0, 0))
            self.table.setItem(r, COL_PROG, prog)

            rest = QTableWidgetItem("读取中…")
            rest.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, COL_REST, rest)

            cid = QTableWidgetItem(str(c.get("courseId", "")))
            cid.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, COL_CID, cid)

            clz = QTableWidgetItem(str(c.get("clazzId", "")))
            clz.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, COL_CLZ, clz)
        self.table.blockSignals(False)

        self.lb_total.setText(f"共 {len(courses)} 门课程")
        if courses:
            self.slot.setCurrentIndex(0)
        else:
            self.empty.set_text("没有读到课程",
                                "账号下本学期没有课程，或课程数据还没同步。")
            self.slot.setCurrentIndex(1)
        self._refresh_selection()

        for row, course in enumerate(self.courses):
            if isinstance(course.get('_progress'), dict):
                self.update_progress(row, course['_progress'])

    def update_progress(self, row: int, info: dict):
        if row < 0 or row >= self.table.rowCount():
            return
        self.progress[row] = info
        done, total = int(info.get("done", 0)), int(info.get("total", 0))
        err = info.get("error")

        item = self.table.item(row, COL_PROG)
        if item:
            item.setData(Qt.UserRole, (done, total))

        rest = self.table.item(row, COL_REST)
        if rest:
            if err:
                rest.setText("读取失败")
                rest.setForeground(QColor(T.DANGER))
                rest.setToolTip(str(err))
            else:
                miss = max(0, total - done)
                rest.setText(str(miss) if not (total == 0) else "—")
                rest.setForeground(QColor(T.OK if miss == 0 else T.TEXT))
                rest.setToolTip(f"已完成 {done} / 共 {total} 个章节")
        self._refresh_selection()

    # ------------------------------------------------------------------ 选择
    def _set_all(self, checked: bool):
        self.table.blockSignals(True)
        state = Qt.Checked if checked else Qt.Unchecked
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_CHECK)
            if it:
                it.setCheckState(state)
        self.table.blockSignals(False)
        self._refresh_selection()

    def _select_unfinished(self):
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_CHECK)
            if not it:
                continue
            info = self.progress.get(r)
            miss = None
            if info and not info.get("error"):
                miss = max(0, int(info.get("total", 0)) - int(info.get("done", 0)))
            it.setCheckState(Qt.Checked if (miss is None or miss > 0) else Qt.Unchecked)
        self.table.blockSignals(False)
        self._refresh_selection()

    def _on_item_changed(self, item: QTableWidgetItem):
        if item.column() == COL_CHECK:
            self._refresh_selection(commit=True)

    def _refresh_selection(self, commit: bool = False):
        if self._loading:
            return
        rows = []
        total_rest = 0
        for r in range(self.table.rowCount()):
            chk = self.table.item(r, COL_CHECK)
            if chk and chk.checkState() == Qt.Checked:
                rows.append(r)
            info = self.progress.get(r)
            if info and not info.get("error"):
                total_rest += max(0, int(info.get("total", 0)) - int(info.get("done", 0)))
        self.badge_sel.set_kind("info" if rows else "idle", f"已选 {len(rows)} 门")
        unknown = sum(1 for r in range(self.table.rowCount())
                      if not self.progress.get(r) or self.progress[r].get('error'))
        text = f"已读取课程待完成章节：{total_rest} 个"
        if unknown:
            text += f"；{unknown} 门课程进度未确认"
        self.lb_sel.setText(text if self.courses else '')
        selected = self.selected_courses()
        if commit:
            self._saved = {str(c.get("courseId", "")) for c in selected}
            self.sig_selection_committed.emit(
                self.current_account(), [str(c.get("courseId", "")) for c in selected])
        self.sig_selection_changed.emit(selected)

    def selected_courses(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            chk = self.table.item(r, COL_CHECK)
            if chk and chk.checkState() == Qt.Checked and r < len(self.courses):
                out.append(self.courses[r])
        return out

    # ------------------------------------------------------------------ 章节
    def set_chapter_selection(self, course_id: str, ids: list | None):
        """从档案恢复某课程的章节选择（None = 全部）。"""
        cid = str(course_id)
        if ids:
            self._chap_sel[cid] = [str(x) for x in ids]
        else:
            self._chap_sel.pop(cid, None)
        self._refresh_chap_btn(cid)

    def chapter_selection(self, course_id: str) -> list | None:
        return self._chap_sel.get(str(course_id))

    def _chap_btn_text(self, course_id: str) -> str:
        sel = self._chap_sel.get(str(course_id))
        if not sel:
            return "章节 · 全部"
        return f"章节 · 已选 {len(sel)}"

    def _refresh_chap_btn(self, course_id: str):
        cid = str(course_id)
        for r, c in enumerate(self.courses):
            if str(c.get("courseId", "")) == cid:
                btn = self._chap_btns.get(r)
                if btn:
                    btn.setText(self._chap_btn_text(cid))
                break

    def _open_chapters(self, course: dict):
        acc_id = self.current_account()
        if not acc_id:
            return
        cid = str(course.get("courseId", ""))
        cached = self._chapter_cache.get((acc_id, cid))
        if cached is None:
            # 未缓存：请求拉取，结果回来后自动弹窗
            request = dict(course, _chapter_request=uuid.uuid4().hex)
            self._pending_dialog = (acc_id, request)
            for r, c in enumerate(self.courses):
                if str(c.get("courseId", "")) == cid:
                    b = self._chap_btns.get(r)
                    if b:
                        b.setText("读取中…")
                        b.setEnabled(False)
                    break
            self.sig_chapters_request.emit(acc_id, request)
            return
        self._show_chapter_dialog(course, cached)

    def _show_chapter_dialog(self, course: dict, points: list):
        acc_id = self.current_account()
        cid = str(course.get("courseId", ""))
        dlg = ChapterDialog(course, points,
                            self._chap_sel.get(cid), self)
        if dlg.exec() == QDialog.Accepted:
            ids = dlg.selected_ids()
            total = len(points)
            chosen = ids if (ids and len(ids) < total) else None
            if chosen:
                self._chap_sel[cid] = chosen
            else:
                self._chap_sel.pop(cid, None)
            self._refresh_chap_btn(cid)
            self.sig_chapters_saved.emit(acc_id, cid, chosen)

    def on_chapters_versioned(self, acc_id, course_id, points, error, request_id):
        pending = self._pending_dialog
        if not pending or pending[1].get('_chapter_request') != request_id:
            return
        self.on_chapters(acc_id, course_id, points, error)

    def on_chapters(self, acc_id: str, course_id: str, points: list, error: str):
        """worker 拉取章节的结果（无论哪个账号发起，都按账号缓存）。"""
        cid = str(course_id)
        pending = self._pending_dialog
        if not pending or pending[0] != acc_id or str(pending[1].get('courseId', '')) != cid:
            return
        if error:
            self._chapter_cache.pop((acc_id, cid), None)
        else:
            self._chapter_cache[(acc_id, cid)] = list(points or [])
        for r, c in enumerate(self.courses):
            if acc_id == self.current_account() and str(c.get("courseId", "")) == cid:
                b = self._chap_btns.get(r)
                if b:
                    b.setEnabled(True)
                    b.setText(self._chap_btn_text(cid))
                break
        if acc_id == self.current_account() and self._pending_dialog:
            pend_acc, pend_course = self._pending_dialog
            if pend_acc == acc_id and str(pend_course.get("courseId", "")) == cid:
                self._pending_dialog = None
                if error:
                    QMessageBox.warning(
                        self, "读取章节失败",
                        f"课程「{pend_course.get('title', '')}」的章节读取失败：\n\n{error}")
                elif points:
                    self._show_chapter_dialog(pend_course, points)

    def invalidate_chapter_cache(self):
        """切账号 / 重读课程时清空缓存与待弹窗。"""
        self._chapter_cache.clear()
        self._pending_dialog = None


class ChapterDialog(QDialog):
    """按课程勾选要刷的章节。全选 ≡ 全部（落盘时归一为「无选择」）。"""

    def __init__(self, course: dict, points: list, selected: list | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"选择章节 · {course.get('title', '')}")
        self.setMinimumSize(560, 480)
        self._points = list(points or [])

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        head = QVBoxLayout()
        head.setSpacing(4)
        title = QLabel(f"{course.get('title', '')} · 共 {len(self._points)} 个章节")
        title.setObjectName("DialogTitle")
        hint = QLabel("勾选要刷的章节；全部勾选或一个不勾 = 刷全部章节。"
                      "灰色「已完成」的章节勾了也会重刷一遍。")
        hint.setObjectName("CardHint")
        hint.setWordWrap(True)
        head.addWidget(title)
        head.addWidget(hint)
        root.addLayout(head)

        # 章节列表（可滚动）
        self.boxes: list[QCheckBox] = []
        host = QWidget()
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        for i, p in enumerate(self._points):
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 4, 8, 4)
            rl.setSpacing(8)
            cb = QCheckBox(f"{i + 1}. {p.get('title', '')}")
            cb.setChecked(not selected or str(p.get("id")) in {str(x) for x in selected})
            self.boxes.append(cb)
            rl.addWidget(cb, 1)
            mark = QLabel("已完成" if p.get("has_finished") else
                          (f"{p.get('jobCount')} 个任务点" if str(p.get("jobCount", "")).strip() not in ("", "0", "1") else ""))
            mark.setObjectName("ChapterState")
            if p.get("has_finished"):
                mark.setProperty("state", "done")
            rl.addWidget(mark)
            col.addWidget(row)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(host)
        sc.setFrameShape(QScrollArea.NoFrame)
        root.addWidget(sc, 1)

        # 工具行
        tools = QHBoxLayout()
        tools.setSpacing(8)
        b_all = QPushButton("全选")
        b_all.setMinimumHeight(32)
        b_all.clicked.connect(lambda: self._set_all(True))
        b_none = QPushButton("全不选")
        b_none.setMinimumHeight(32)
        b_none.clicked.connect(lambda: self._set_all(False))
        b_undo = QPushButton("只选未完成")
        b_undo.setMinimumHeight(32)
        b_undo.clicked.connect(self._select_unfinished)
        self.lb_count = QLabel("")
        self.lb_count.setObjectName("CardHint")
        tools.addWidget(b_all)
        tools.addWidget(b_none)
        tools.addWidget(b_undo)
        tools.addStretch(1)
        tools.addWidget(self.lb_count)
        root.addLayout(tools)

        # 确认行
        acts = QHBoxLayout()
        acts.setSpacing(8)
        acts.addStretch(1)
        b_cancel = QPushButton("取消")
        b_cancel.setMinimumHeight(36)
        b_cancel.clicked.connect(self.reject)
        b_ok = QPushButton("确定")
        b_ok.setProperty("variant", "primary")
        b_ok.setMinimumHeight(36)
        b_ok.clicked.connect(self.accept)
        acts.addWidget(b_cancel)
        acts.addWidget(b_ok)
        root.addLayout(acts)

        for cb in self.boxes:
            cb.toggled.connect(self._refresh_count)
        self._refresh_count()

    def _set_all(self, checked: bool):
        for cb in self.boxes:
            cb.setChecked(checked)

    def _select_unfinished(self):
        for cb, p in zip(self.boxes, self._points):
            cb.setChecked(not p.get("has_finished"))

    def _refresh_count(self, *_):
        n = sum(1 for cb in self.boxes if cb.isChecked())
        total = len(self.boxes)
        self.lb_count.setText(
            f"已选 {n} / {total}" if n < total else f"已选全部 {total} 个（= 刷全部章节）")

    def selected_ids(self) -> list[str]:
        return [str(p.get("id")) for cb, p in zip(self.boxes, self._points)
                if cb.isChecked()]
