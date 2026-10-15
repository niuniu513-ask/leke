# -*- coding: utf-8 -*-
"""通用界面组件：矢量图标、卡片、徽标、指标块、空态、表格委托。

组件都刻意做得"克制"：卡片只靠描边分层，只有指标卡这类需要跳出来的
元素才上真实投影（QSS 画不了阴影，只能靠 QGraphicsDropShadowEffect）。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from gui_app import icons as I
from gui_app import theme as T


class IconLabel(QLabel):
    """矢量图标控件（名字 + 颜色 + 逻辑尺寸）。"""

    def __init__(self, name: str = "info", color: str = T.TEXT_SUB,
                 size: int = 18, parent=None):
        super().__init__(parent)
        self._name = name
        self._color = color
        self._size = size
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setPixmap(I.pixmap(name, color, size))

    def set_icon(self, name: str | None = None, color: str | None = None):
        if name:
            self._name = name
        if color:
            self._color = color
        self.setPixmap(I.pixmap(self._name, self._color, self._size))


def soft_shadow(widget: QWidget, blur: int = 18, dy: int = 3, alpha: int = 26):
    """给控件加一层很轻的投影。

    ⚠️ QGraphicsEffect 会强制控件走离屏渲染，放进 QScrollArea 里滚动时容易
    闪烁，所以只给不在滚动区里的"重点卡片"用。
    """
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(QColor(23, 43, 77, alpha))
    widget.setGraphicsEffect(eff)
    return eff


class Card(QFrame):
    """带标题的白色卡片容器。

    icon 传入图标名时会画一个圆角色块 + 图标，标题区更有辨识度；
    elevated=True 时加一层轻投影（只建议给不在滚动区里的卡片用）。
    """

    def __init__(self, title: str = "", hint: str = "", parent=None,
                 icon: str = "", elevated: bool = False,
                 margins: tuple[int, int, int, int] = (22, 20, 22, 22),
                 accent: str = "", accent_soft: str = ""):
        """accent/accent_soft：卡片图标的自定义强调色与底色（默认主色青绿）。

        U校园页传 theme.UNIPUS / UNIPUS_SOFT（v1.6.8 起与主色同值）。
        """
        super().__init__(parent)
        self.setObjectName("Card")
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(*margins)
        self._outer.setSpacing(14)

        if elevated:
            soft_shadow(self, blur=22, dy=4, alpha=22)

        # 头部行惰性创建：卡头右侧可能还要挂操作控件（见 head()）
        self._head = None
        self._head_host = None
        if title or icon:
            _accent = accent or T.PRIMARY
            _accent_soft = accent_soft or T.PRIMARY_SOFT
            head = self.head()
            if icon:
                mark = QLabel()
                mark.setObjectName("CardIcon")
                mark.setFixedSize(34, 34)
                mark.setAlignment(Qt.AlignCenter)
                mark.setPixmap(I.pixmap(icon, _accent, 17))
                mark.setStyleSheet(
                    f"background:{_accent_soft}; border-radius:9px;"
                )
                head.addWidget(mark, 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(3)
            if title:
                t = QLabel(title)
                t.setObjectName("CardTitle")
                t.setWordWrap(True)
                col.addWidget(t)
            if hint:
                h = QLabel(hint)
                h.setObjectName("CardHint")
                h.setWordWrap(True)
                col.addWidget(h)
            head.addLayout(col, 1)
            self._outer.addLayout(head)

        # 正文放进一个**独立容器**并给 stretch=1：
        # 之前正文是直接挂在 _outer 上的一个子布局，于是"卡片被拉高时"
        # 多出来的高度会在「标题区」和「正文区」之间平分 —— 标题下面凭空多出
        # 一大片空白（账号详情卡实测约 180px），看着就是"布局很乱"。
        # 挂成 stretch=1 的子控件后，多余高度全部落在正文区，标题区保持自然高度。
        self._body_host = QWidget()
        self.body = QVBoxLayout(self._body_host)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(12)
        self._outer.addWidget(self._body_host, 1)

    def head(self) -> QHBoxLayout:
        """卡片头部行（没有标题的卡片也会懒建一个空行）。

        头部在构造时只由 title/icon 决定，但很多页面需要在**同一行右侧**
        再挂一个操作控件（比如「账号列表」卡头放「＋ 添加」）。
        直接往 _outer 里 insert 一个独立行会在卡头下面多出一行、拉高卡片，
        所以统一走这里 —— 头部行是**同一行**，右侧自然对齐。
        """
        if self._head is None:
            self._head_host = QWidget()
            self._head = QHBoxLayout(self._head_host)
            self._head.setContentsMargins(0, 0, 0, 0)
            self._head.setSpacing(12)
            self._outer.insertWidget(0, self._head_host)
        return self._head

    def add_header(self, widget_or_layout):
        """把控件/布局挂到卡头右侧（需先有头部行，无标题的卡片会自动建）。"""
        head = self.head()
        if isinstance(widget_or_layout, QWidget):
            head.addWidget(widget_or_layout)
        else:
            head.addLayout(widget_or_layout)
        return widget_or_layout

    def add(self, widget_or_layout):
        if isinstance(widget_or_layout, QWidget):
            self.body.addWidget(widget_or_layout)
        else:
            self.body.addLayout(widget_or_layout)
        return widget_or_layout

    def row(self, *widgets, spacing: int = 12, stretch_last: bool = False) -> QHBoxLayout:
        lay = QHBoxLayout()
        lay.setSpacing(spacing)
        for i, w in enumerate(widgets):
            if w is None:
                lay.addStretch(1)
                continue
            lay.addWidget(w)
        if stretch_last:
            lay.addStretch(1)
        self.body.addLayout(lay)
        return lay


class Divider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Divider")
        self.setFixedHeight(1)


class Badge(QLabel):
    """状态小徽标。"""

    def __init__(self, text: str = "", kind: str = "idle", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        # 必须用 Minimum 而不是 Maximum：Maximum 的语义是「sizeHint 是上限」，
        # 布局仍可把徽标压到比 sizeHint 更窄 —— 账号页「共 4 个账号」就被压掉 4px
        # 导致文字被裁（QSS padding 3px 11px，内容区 59px < 文字 62px）。
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.set_kind(kind, text)

    def set_kind(self, kind: str, text: str | None = None):
        names = {
            "ok": "BadgeOk", "warn": "BadgeWarn", "error": "BadgeError",
            "idle": "BadgeIdle", "info": "BadgeInfo",
        }
        self.setObjectName(names.get(kind, "BadgeIdle"))
        if text is not None:
            self.setText(text)
        self.style().unpolish(self)
        self.style().polish(self)
        self.updateGeometry()
        # QSS 的 padding 要等 polish 之后才进入 sizeHint；布局若是按「没算 padding」
        # 的旧 sizeHint 分配宽度，徽标会被压掉几像素 —— 账号页「共 4 个账号」
        # 实测控件 81px、内容区 59px、文字 62px，直接被裁。
        # 每次都先清零再设：否则文字由长变短时旧的最小宽会留着，徽标右侧留空。
        self.setMinimumWidth(0)
        self.setMinimumWidth(max(self.sizeHint().width(), self.minimumSizeHint().width()))


class Metric(QFrame):
    """指标块：图标 + 大数字 + 说明。"""

    ICON_TINT = {
        "plan": (T.PRIMARY, T.PRIMARY_SOFT),
        "done": (T.OK, T.OK_SOFT),
        "total": (T.PRIMARY, T.PRIMARY_SOFT),
        "fail": (T.DANGER, T.DANGER_SOFT),
        "time": (T.TEXT_SUB, T.CARD_SUB_2),
    }

    def __init__(self, label: str, value: str = "0", parent=None,
                 icon: str = "", tint: str = ""):
        super().__init__(parent)
        self.setObjectName("MetricCard")
        # 只吃自然高度：运行页控件多，允许它被横向拉宽，但不许被纵向压扁
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(13, 10, 13, 10)
        lay.setSpacing(10)

        fg, bg = self.ICON_TINT.get(tint, (T.TEXT_SUB, T.CARD_SUB_2))
        self.icon_box = QLabel()
        self.icon_box.setObjectName("MetricIcon")
        self.icon_box.setFixedSize(30, 30)
        self.icon_box.setAlignment(Qt.AlignCenter)
        self.icon_box.setPixmap(I.pixmap(icon or "info", fg, 16))
        self.icon_box.setStyleSheet(f"background:{bg}; border-radius:8px;")
        lay.addWidget(self.icon_box, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(1)
        self.value = QLabel(value)
        self.value.setObjectName("BigNum")
        self.caption = QLabel(label)
        self.caption.setObjectName("MetricLabel")
        col.addWidget(self.value)
        col.addWidget(self.caption)
        lay.addLayout(col, 1)

    def set_value(self, value, color: str | None = None):
        self.value.setText(str(value))
        self.value.setStyleSheet(f"color: {color};" if color else "")


class EmptyState(QWidget):
    """居中空态：图标 + 标题 + 说明。表格/进度区的空数据占位都用它。"""

    def __init__(self, icon: str = "inbox", title: str = "", hint: str = "",
                 parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 28, 24, 28)
        lay.setSpacing(10)
        lay.addStretch(1)

        self.icon = IconLabel(icon, T.TEXT_FAINT, 40)
        lay.addWidget(self.icon, 0, Qt.AlignHCenter)

        self.title = QLabel(title)
        self.title.setObjectName("EmptyTitle")
        self.title.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.title)

        self.hint = QLabel(hint)
        self.hint.setObjectName("EmptyHint")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

        lay.addStretch(1)
        self.set_visible(False)

    def set_text(self, title: str, hint: str = ""):
        self.title.setText(title)
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))

    def set_visible(self, visible: bool):
        self.setVisible(bool(visible))


class CheckDelegate(QStyledItemDelegate):
    """表格里的居中复选框（自绘，避免依赖图片资源）。"""

    @staticmethod
    def _is_checked(index) -> bool:
        # PySide6 里 index.data(CheckStateRole) 回来的是 int，
        # 而 Qt.Checked 是枚举：int == enum 恒为 False，int() 转换又会抛异常。
        # 正确做法是用枚举构造器归一化后再比。
        state = index.data(Qt.CheckStateRole)
        if state is None:
            return False
        try:
            return Qt.CheckState(state) == Qt.CheckState.Checked
        except (TypeError, ValueError):
            return False

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        checked = self._is_checked(index)
        size = 17
        rect = option.rect
        box = QRectF(rect.center().x() - size / 2, rect.center().y() - size / 2, size, size)

        if checked:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(T.PRIMARY))
            painter.drawRoundedRect(box, 4.5, 4.5)
            pen = QPen(QColor("#FFFFFF"))
            pen.setWidthF(2.0)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            path = QPainterPath()
            path.moveTo(box.left() + 4.0, box.top() + 8.8)
            path.lineTo(box.left() + 7.0, box.top() + 11.8)
            path.lineTo(box.left() + 12.8, box.top() + 5.4)
            painter.drawPath(path)
        else:
            painter.setPen(QPen(QColor(T.BORDER_STRONG), 1.0))
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRoundedRect(box, 4.5, 4.5)
        if option.state & QStyle.State_HasFocus:
            painter.setPen(QPen(QColor(T.PRIMARY), 1.4))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(box.adjusted(-3, -3, 3, 3), 7, 7)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        mouse_toggle = (event.type() == QEvent.MouseButtonRelease
                        and event.button() == Qt.LeftButton)
        key_toggle = (event.type() == QEvent.KeyPress
                      and event.key() in (Qt.Key_Space, Qt.Key_Select))
        if mouse_toggle or key_toggle:
            new = (Qt.CheckState.Unchecked if self._is_checked(index)
                   else Qt.CheckState.Checked)
            model.setData(index, new, Qt.CheckStateRole)
            return True
        return False


class ProgressDelegate(QStyledItemDelegate):
    """进度列：细圆角条 + 百分比。数据为 (done, total)。"""

    def paint(self, painter: QPainter, option, index):
        data = index.data(Qt.UserRole)
        if not isinstance(data, (tuple, list)) or len(data) != 2:
            super().paint(painter, option, index)
            return
        done, total = int(data[0] or 0), int(data[1] or 0)
        ratio = (done / total) if total else 0.0
        ratio = max(0.0, min(1.0, ratio))

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect.adjusted(10, 0, -10, 0)
        bar_h = 7
        bar = QRectF(rect.left(), rect.center().y() - bar_h / 2,
                     max(40, rect.width() - 58), bar_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(T.TRACK))
        painter.drawRoundedRect(bar, 3.5, 3.5)

        if ratio > 0:
            fill = QRectF(bar.left(), bar.top(), bar.width() * ratio, bar.height())
            painter.setBrush(QColor(T.OK if ratio >= 1.0 else T.PRIMARY))
            painter.drawRoundedRect(fill, 3.5, 3.5)

        f = QFont(painter.font())
        f.setPointSizeF(9.0)
        painter.setFont(f)
        painter.setPen(QColor(T.OK if (total and done >= total) else T.TEXT_SUB))
        txt = f"{done}/{total}" if total else "—"
        painter.drawText(
            QRectF(bar.right() + 8, rect.top(), 50, rect.height()),
            Qt.AlignVCenter | Qt.AlignLeft, txt,
        )
        painter.restore()


class SegmentedBar(QWidget):
    """分段切换条（替代 QTabWidget，样式更可控）。"""

    sig_changed = Signal(int)

    def __init__(self, items: list[str], parent=None):
        super().__init__(parent)
        self.setObjectName("SegTabs")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget#SegTabs {{ background:{T.CARD_SUB};"
            f" border:1px solid {T.BORDER}; border-radius:10px; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        self._buttons: list[QPushButton] = []
        for i, text in enumerate(items):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(30)
            b.setStyleSheet(
                "QPushButton { border:1px solid transparent; border-radius:7px;"
                f" padding:5px 18px; color:{T.TEXT_SUB}; background:transparent; }}"
                f"QPushButton:hover {{ color:{T.TEXT}; background:{T.CARD_SUB_2}; }}"
                f"QPushButton:checked {{ background:{T.CARD}; color:{T.PRIMARY};"
                f" font-weight:bold; border:1px solid {T.BORDER_STRONG}; }}"
            )
            b.clicked.connect(lambda _=False, idx=i: self.set_index(idx))
            lay.addWidget(b)
            self._buttons.append(b)
        lay.addStretch(1)
        if self._buttons:
            self._buttons[0].setChecked(True)
        self._current = 0

    def set_index(self, index: int):
        if index < 0 or index >= len(self._buttons):
            return
        for i, b in enumerate(self._buttons):
            b.setChecked(i == index)
        if index != self._current:
            self._current = index
            self.sig_changed.emit(index)

    def index(self) -> int:
        return self._current


class SmoothBar(QWidget):
    """细圆角进度条（自绘）：浮点几何 + 抗锯齿 + 可选缓动。

    为什么不用 QProgressBar
    ----------------------
    QProgressBar 的 chunk 由样式引擎按**整数像素**绘制，只在 setValue() 时重绘。
    刷课场景里一个 32 分钟的视频每秒只前进 1/1940，落到 800px 宽的条上约
    0.4px/s —— 肉眼看到的就是「原地卡住 → 突然跳 1 像素」的台阶，也就是用户
    反馈的「进度条一跳一跳」。这里改成 QPainter 浮点几何绘制，配合运行页的
    插值定时器，chunk 边缘连续滑动，不再跳格。

    接口是 QProgressBar 的子集（setRange / setValue / value / maximum /
    minimum / setTextVisible / setFormat），可以直接替换。
    注意：value() 返回的是**目标值**（与 QProgressBar 语义一致），真正绘制用的
    是内部缓动值 _shown；视频插值走 set_display() 直通。
    """

    #: variant -> (轨道色, 渐变起, 渐变止, 圆角)
    VARIANTS = {
        "": (T.TRACK, "#345FE5", T.PRIMARY, 6.0),
        "ok": (T.TRACK, T.OK, T.OK, 6.0),
        "video": (T.TRACK, "#345FE5", T.PRIMARY, 2.5),
        "thin": (T.TRACK, T.OK, T.OK, 3.0),
    }

    def __init__(self, parent=None, height: int = 8, radius: float = 6.0,
                 variant: str = "", animate: bool = True):
        super().__init__(parent)
        self._min = 0.0
        self._max = 100.0
        self._target = 0.0
        self._shown = 0.0
        self._fallback = str(variant or "")
        self._animate = bool(animate)
        self._text_visible = False
        self._fmt = "%p%"
        self._radius = float(radius)
        self.setFixedHeight(int(height))
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance)

    # ------------------------------------------------------ QProgressBar 兼容
    def setRange(self, lo: float, hi: float):
        self._min, self._max = float(lo), float(hi)
        self._target = self._clamp(self._target)
        self._shown = self._clamp(self._shown)
        self.update()

    def minimum(self) -> float:
        return self._min

    def maximum(self) -> float:
        return self._max

    def setValue(self, value: float):
        self._target = self._clamp(value)
        gap = abs(self._target - self._shown)
        if not self._animate or gap < 0.5:
            self._shown = self._target
            self._timer.stop()
        elif not self._timer.isActive():
            self._timer.start()
        self.update()

    def value(self) -> float:
        return self._target

    def setTextVisible(self, visible: bool):
        self._text_visible = bool(visible)

    def isTextVisible(self) -> bool:
        return self._text_visible

    def setFormat(self, fmt: str):
        self._fmt = str(fmt)

    def format(self) -> str:
        return self._fmt

    # ------------------------------------------------------ 直通（视频插值）
    def set_display(self, value: float, force: bool = False) -> bool:
        """把绘制值直接推到 value（不缓动）。返回是否真的发生了变化。"""
        value = self._clamp(value)
        if not force and abs(value - self._shown) < 0.02:
            return False
        self._shown = value
        self._target = value
        self.update()
        return True

    def shown(self) -> float:
        return self._shown

    def ratio(self) -> float:
        span = (self._max - self._min) or 1.0
        return max(0.0, min(1.0, (self._shown - self._min) / span))

    # ------------------------------------------------------ 内部
    def _clamp(self, value: float) -> float:
        return max(self._min, min(self._max, float(value)))

    def _advance(self):
        """指数逼近目标：约 16ms 一帧，收敛后自动停表（只在跳变时短暂运行）。"""
        gap = self._target - self._shown
        if abs(gap) < 0.05:
            self._shown = self._target
            self._timer.stop()
        else:
            self._shown += gap * 0.26
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.0, 0.0, float(self.width()), float(self.height()))

        key = self.property("variant")
        key = str(key) if key is not None else self._fallback
        track, c_from, c_to, radius = self.VARIANTS.get(key, self.VARIANTS[""])
        radius = max(0.0, min(float(radius), self._radius,
                              rect.height() / 2.0, rect.width() / 2.0))

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(track))
        painter.drawRoundedRect(rect, radius, radius)

        ratio = self.ratio()
        if ratio > 0.0:
            width = max(rect.width() * ratio, 0.75)
            fill = QRectF(rect.left(), rect.top(), width, rect.height())
            grad = QLinearGradient(rect.topLeft(), rect.topRight())
            grad.setColorAt(0.0, QColor(c_from))
            grad.setColorAt(1.0, QColor(c_to))
            painter.setBrush(grad)
            corner = min(radius, width / 2.0)
            painter.drawRoundedRect(fill, corner, corner)
        painter.end()


class AccountBar(QFrame):
    """平台账号条：一排可点的账号 chip + 新增 / 删除。

    为什么不用「复选框 + 每账号一张卡」（学习通运行页那套）
    ------------------------------------------------------
    平台页同一时刻只需要**细看一个账号**（域名 / 登录态 / 课程表都只画当前账号），
    但**刷课依然可以多账号并行**。所以 chip 上带一个运行态圆点：
    一眼看出"哪几个正在跑"，而不用为每个账号各撑一整套卡片。
    """

    sig_switch = Signal(str)      # 切到某个账号
    sig_add = Signal()            # 新增账号
    sig_remove = Signal(str)      # 删除某个账号

    CHIP_MAX_WIDTH = 168

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AccountBar")
        self._current = ""
        self._buttons: dict[str, QPushButton] = {}
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._reveal_current)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(8)

        cap = QLabel("账号")
        cap.setObjectName("FieldLabel")
        lay.addWidget(cap, 0, Qt.AlignVCenter)

        self._row = QHBoxLayout()
        self._row.setSpacing(7)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.addStretch(1)
        chips = QWidget()
        chips.setLayout(self._row)
        chips.installEventFilter(self)
        self._chips_scroll = QScrollArea()
        self._chips_scroll.setWidgetResizable(True)
        self._chips_scroll.setFrameShape(QFrame.NoFrame)
        self._chips_scroll.setMinimumWidth(0)
        self._chips_scroll.setFixedHeight(48)
        self._chips_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._chips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._chips_scroll.setWidget(chips)
        lay.addWidget(self._chips_scroll, 1)

        self.btn_add = QPushButton("＋ 新增账号")
        self.btn_add.setProperty("variant", "ghost")
        self.btn_add.setMinimumHeight(30)
        self.btn_add.clicked.connect(self.sig_add.emit)
        lay.addWidget(self.btn_add, 0, Qt.AlignVCenter)

        self.btn_remove = QPushButton("删除当前")
        self.btn_remove.setProperty("variant", "ghost")
        self.btn_remove.setMinimumHeight(30)
        self.btn_remove.setToolTip("删除当前账号的档案（不影响其它账号）")
        self.btn_remove.clicked.connect(
            lambda: self.sig_remove.emit(self._current))
        lay.addWidget(self.btn_remove, 0, Qt.AlignVCenter)

    # ------------------------------------------------------------------ 接口
    def brand(self) -> str:
        """当前平台品牌色名（""/unipus/yuketang），QSS 用 chip 上的 brand 属性。"""
        return str(self.property("brand") or "")

    def set_brand(self, name: str):
        """切换平台品牌色。

        必须把属性同步到**每个 chip** 并逐个 repolish —— 祖先属性选择器不会重算
        子控件（项目硬规则 13）。`set_accounts()` 也会自动带上当前 brand，
        所以正常用法是「先 set_brand 再 set_accounts」，或者直接调这个方法。
        """
        name = str(name or "")
        self.setProperty("brand", name)
        for b in self._buttons.values():
            b.setProperty("brand", name)
            b.style().unpolish(b)
            b.style().polish(b)
        self.style().unpolish(self)
        self.style().polish(self)

    def current(self) -> str:
        return self._current

    def set_accounts(self, pairs: list, current: str = "",
                     running: set | None = None):
        """pairs = [(acc_id, 显示名), …]；running = 正在刷课的 acc_id 集合。"""
        for b in self._buttons.values():
            b.setParent(None)
            b.deleteLater()
        self._buttons = {}
        running = set(running or ())

        for acc_id, name in (pairs or []):
            acc_id = str(acc_id)
            b = QPushButton()
            b.setObjectName("AccChip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(30)
            b.setMaximumWidth(self.CHIP_MAX_WIDTH)
            b.setProperty("busy", "true" if acc_id in running else "false")
            b.setProperty("brand", self.brand())
            full = str(name or acc_id)
            # 原始显示名单独存一份：set_running 要靠它还原文案，
            # 用 toolTip 反推的话"名字里带（正在刷课）"就会被截错。
            b.setProperty("label", full)
            display = ("● " if acc_id in running else "") + full
            b.setText(b.fontMetrics().elidedText(display, Qt.ElideRight, self.CHIP_MAX_WIDTH - 32))
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            b.setToolTip(full + ("（正在刷课）" if acc_id in running else ""))
            b.clicked.connect(lambda _=False, a=acc_id: self.sig_switch.emit(a))
            self._row.insertWidget(self._row.count() - 1, b)
            self._buttons[acc_id] = b

        ids = [str(a) for a, _n in (pairs or [])]
        self._current = str(current) if str(current) in ids else (ids[0] if ids else "")
        for acc_id, b in self._buttons.items():
            b.setChecked(acc_id == self._current)
        if self._current in self._buttons:
            self._reveal_timer.start(0)
        self.btn_remove.setEnabled(bool(self._current) and len(ids) > 1)
        if not ids:
            self.btn_remove.setEnabled(False)
        return dict(self._buttons)

    def _reveal_current(self):
        current = self._buttons.get(self._current)
        if current is not None:
            self._row.activate()
            self._chips_scroll.ensureWidgetVisible(current)

    def eventFilter(self, watched, event):
        from shiboken6 import isValid
        if hasattr(self, "_chips_scroll") and not isValid(self._chips_scroll):
            return False
        if (hasattr(self, '_chips_scroll') and watched is self._chips_scroll.widget()
                and event.type() == QEvent.Resize):
            self._reveal_timer.start(0)
        return super().eventFilter(watched, event)

    def chip(self, acc_id: str):
        """按账号 id 取 chip 按钮（找不到返回 None）。"""
        return self._buttons.get(str(acc_id))

    def running_ids(self) -> set:
        """当前显示为"正在刷课"的账号 id 集合（测试/状态同步用）。"""
        return {acc_id for acc_id, b in self._buttons.items()
                if b.property("busy") == "true"}

    def set_running(self, running: set | None):
        """只更新运行态圆点，不重建按钮（刷课过程中高频调用）。"""
        running = set(running or ())
        for acc_id, b in self._buttons.items():
            busy = acc_id in running
            if (b.property("busy") == "true") == busy:
                continue
            b.setProperty("busy", "true" if busy else "false")
            text = str(b.property("label") or b.text().lstrip("● "))
            display = ("● " if busy else "") + text
            b.setText(b.fontMetrics().elidedText(display, Qt.ElideRight, self.CHIP_MAX_WIDTH - 32))
            b.setToolTip(text + ("（正在刷课）" if busy else ""))
            b.style().unpolish(b)
            b.style().polish(b)


def label(text: str, sub: bool = False, weak: bool = False) -> QLabel:
    lab = QLabel(text)
    if weak:
        lab.setObjectName("CardHint")
    elif sub:
        lab.setObjectName("FieldLabel")
    return lab


def title_label(text: str, size: int = 14, bold: bool = True) -> QLabel:
    lab = QLabel(text)
    f = lab.font()
    f.setPointSizeF(size)
    f.setBold(bold)
    lab.setFont(f)
    return lab
