# -*- coding: utf-8 -*-
"""用户中心页：用户名 / 头像 / 绑定设备 / 密码入口 / 支持作者 / 退出登录。

软件完全免费，账号没有「到期 / 套餐」概念；仍需注册登录（设备绑定与云端
知识库都挂在账号上）。

「账号」只有一个来源 —— kc_server 的 /api/v1/me*。
网页（kc-api.lifedays.site）改的用户名与头像，这里下次刷新/心跳就会同步过来；
反过来在这里改的，网页刷新立刻可见。软件内不保存任何课程平台凭据。

密码不在这里改：改密表单与「找回账号」都统一在网页用户中心（/profile、/forget），
客户端只提供跳转入口，避免两处表单各自校验、行为不一致。

所有网络请求都在后台线程，结果经信号回主线程（Qt 控件不能在子线程碰）。
"""
from __future__ import annotations

import base64
import os
import threading

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from api import kc_client
from gui_app import theme as T
from gui_app.widgets import Badge, Card

AVATAR_SIDE = 256      # 上传前统一缩到 256px（与网页、服务端一致）


def _round_pixmap(src: QPixmap, size: int) -> QPixmap:
    """把方图裁成圆形（抗锯齿）。"""
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addEllipse(0.0, 0.0, float(size), float(size))
    p.setClipPath(path)
    scaled = src.scaled(size, size, Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation)
    p.drawPixmap(-(scaled.width() - size) // 2, -(scaled.height() - size) // 2, scaled)
    p.end()
    return out


def _initial_pixmap(text: str, size: int) -> QPixmap:
    """没有头像时的兜底：品牌色圆底 + 首字母。"""
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(T.PRIMARY_SOFT))
    p.drawEllipse(0, 0, size, size)
    p.setPen(QColor(T.PRIMARY))
    f = p.font()
    f.setPointSizeF(max(9.0, size * 0.4))
    f.setBold(True)
    p.setFont(f)
    ch = (text or "乐").strip()[:1].upper() or "乐"
    p.drawText(out.rect(), Qt.AlignCenter, ch)
    p.end()
    return out


def _fmt_time(iso: str) -> str:
    if not iso:
        return "—"
    return str(iso).replace("T", " ")[:16]


class _Sig(QObject):
    done = Signal(object)


class ProfilePage(QWidget):
    """用户中心。"""

    sig_status = Signal(str)             # 状态栏提示
    sig_profile_changed = Signal(dict)   # 用户名/头像变了（主窗口可刷新标题）
    sig_logout = Signal()                # 请求退出登录（主窗口确认后执行）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sig = _Sig()
        self._sig.done.connect(self._on_done)
        self._busy = False
        self._bind_busy = False
        self._me: dict = {}
        self._avatar_pm: QPixmap | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QScrollArea.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(16)

        lay.addWidget(self._build_profile_card())
        lay.addWidget(self._build_account_card())
        lay.addWidget(self._build_device_card())
        lay.addWidget(self._build_password_card())
        lay.addWidget(self._build_footer_card())
        lay.addStretch(1)

        sa.setWidget(inner)
        root.addWidget(sa)
        self.refresh()

    # ================================================================ 卡片
    def _build_profile_card(self) -> Card:
        card = Card("用户名与头像",
                    "保存在乐课账号里，网页（用户中心）与软件共用同一份 —— "
                    "任何一边改完，另一边刷新即可看到。用户名在平台内唯一。",
                    icon="profile")
        row = QHBoxLayout()
        row.setSpacing(20)

        self.lb_avatar = QLabel()
        self.lb_avatar.setFixedSize(88, 88)
        self.lb_avatar.setPixmap(_initial_pixmap("乐", 88))
        row.addWidget(self.lb_avatar, 0, Qt.AlignTop)

        right = QVBoxLayout()
        right.setSpacing(8)

        self.btn_avatar = QPushButton("更换头像…")
        self.btn_avatar.setMinimumHeight(30)
        self.btn_avatar.clicked.connect(self._pick_avatar)
        self.btn_avatar_clear = QPushButton("移除头像")
        self.btn_avatar_clear.setProperty("variant", "ghost")
        self.btn_avatar_clear.setMinimumHeight(30)
        self.btn_avatar_clear.clicked.connect(self._clear_avatar)
        av_row = QHBoxLayout()
        av_row.setSpacing(8)
        av_row.addWidget(self.btn_avatar)
        av_row.addWidget(self.btn_avatar_clear)
        av_row.addStretch(1)
        right.addLayout(av_row)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        lb = QLabel("用户名")
        lb.setObjectName("FieldLabel")
        self.ed_nick = QLineEdit()
        self.ed_nick.setPlaceholderText("2-20 位：中文 / 字母 / 数字 / _-")
        self.ed_nick.setMaxLength(20)
        self.ed_nick.setMinimumHeight(32)
        self.ed_nick.setMaximumWidth(280)
        self.btn_save_nick = QPushButton("保存用户名")
        self.btn_save_nick.setProperty("variant", "primary")
        self.btn_save_nick.setMinimumHeight(32)
        self.btn_save_nick.clicked.connect(self._save_nickname)
        self.ed_nick.returnPressed.connect(self._save_nickname)
        name_row.addWidget(lb)
        name_row.addWidget(self.ed_nick)
        name_row.addWidget(self.btn_save_nick)
        name_row.addStretch(1)
        right.addLayout(name_row)

        self.lb_nick_tip = QLabel("")
        self.lb_nick_tip.setObjectName("CardHint")
        right.addWidget(self.lb_nick_tip)
        right.addStretch(1)

        row.addLayout(right, 1)
        card.body.addLayout(row)
        return card

    def _build_account_card(self) -> Card:
        card = Card("账号概况", "乐课完全免费，账号长期有效；注册登录只为了云端知识库与设备绑定。",
                    icon="user")
        grid = QHBoxLayout()
        grid.setSpacing(14)
        self.lb_account = self._stat_block("登录账号", "—", "user")
        self.lb_free = self._stat_block("版本", "永久免费", "check")
        grid.addWidget(self.lb_account, 1)
        grid.addWidget(self.lb_free, 1)
        grid.addStretch(2)
        card.body.addLayout(grid)
        return card

    def _stat_block(self, caption: str, value: str, icon: str) -> QFrame:
        box = QFrame()
        box.setObjectName("MiniStat")
        box.setProperty("icon", icon)
        v = QVBoxLayout(box)
        v.setContentsMargins(13, 10, 13, 10)
        v.setSpacing(3)
        c = QLabel(caption)
        c.setObjectName("MetricLabel")
        val = QLabel(value)
        val.setObjectName("MiniStatValue")
        val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(c)
        v.addWidget(val)
        box._value_label = val          # type: ignore[attr-defined]
        return box

    def _build_device_card(self) -> Card:
        card = Card("已绑定设备", "同一账号最多两台电脑；换电脑时先解绑旧设备再登录。",
                    icon="shield")
        self.device_box = QVBoxLayout()
        self.device_box.setSpacing(8)
        card.body.addLayout(self.device_box)
        self.lb_device_empty = QLabel("尚未在软件中登录过。")
        self.lb_device_empty.setObjectName("CardHint")
        self.device_box.addWidget(self.lb_device_empty)

        btn_row = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setMinimumHeight(30)
        self.btn_refresh.clicked.connect(self.refresh)
        self.badge_state = Badge("未同步", "idle")
        btn_row.addWidget(self.btn_refresh)
        btn_row.addWidget(self.badge_state)
        btn_row.addStretch(1)
        card.body.addLayout(btn_row)
        return card

    def _build_password_card(self) -> Card:
        """密码卡只做入口：改密与找回都在网页用户中心，客户端不内置表单。

        早先这里是一张「原密码 / 新密码 / 确认」的表单，与网页各写一套校验；
        用户要求统一到网页后改为跳转，服务端接口 /auth/change_password 保持不变
        （客户端 api/kc_client.py 的 change_password 仍保留给脚本与自检用）。
        """
        card = Card("密码与安全",
                    "密码在网页用户中心「个人资料 → 修改密码」里改，改完立即生效，"
                    "其它设备上的登录状态不受影响。忘记原密码就用注册邮箱收验证码重置。",
                    icon="key")
        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_pwd_web = QPushButton("打开网页修改密码")
        self.btn_pwd_web.setProperty("variant", "primary")
        self.btn_pwd_web.setMinimumHeight(32)
        self.btn_pwd_web.clicked.connect(self._open_pwd_page)
        self.btn_forgot_web = QPushButton("忘记密码 / 找回账号")
        self.btn_forgot_web.setMinimumHeight(32)
        self.btn_forgot_web.clicked.connect(self._open_forgot_page)
        row.addWidget(self.btn_pwd_web)
        row.addWidget(self.btn_forgot_web)
        row.addStretch(1)
        card.body.addLayout(row)
        hint = QLabel("打开后会进入浏览器；网页与软件用的是同一个乐课账号。")
        hint.setObjectName("CardHint")
        hint.setWordWrap(True)
        card.body.addWidget(hint)
        return card

    def _build_footer_card(self) -> Card:
        card = Card("", margins=(20, 16, 20, 16))
        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_site = QPushButton("打开网页用户中心")
        self.btn_site.setProperty("variant", "ghost")
        self.btn_site.setMinimumHeight(32)
        self.btn_site.clicked.connect(self._open_site)
        self.btn_support = QPushButton("支持作者 ❤")
        self.btn_support.setMinimumHeight(32)
        self.btn_support.clicked.connect(self._open_support)
        self.btn_logout = QPushButton("退出登录")
        self.btn_logout.setProperty("variant", "danger")
        self.btn_logout.setMinimumHeight(32)
        self.btn_logout.clicked.connect(self._ask_logout)
        row.addWidget(self.btn_site)
        row.addWidget(self.btn_support)
        row.addWidget(self.btn_logout)
        row.addStretch(1)
        card.body.addLayout(row)
        hint = QLabel("退出登录后需要重新输入乐课账号密码才能进入；"
                      "课程平台账号仍保留在本机，不会因此丢失。")
        hint.setObjectName("CardHint")
        hint.setWordWrap(True)
        card.body.addWidget(hint)
        return card

    def _open_support(self):
        """支持作者：弹微信收款码。软件免费，扫码转账全凭自愿。"""
        from gui_app.page_settings import SupportPage

        dlg = QDialog(self)
        dlg.setWindowTitle("支持作者 · 乐课永久免费")
        dlg.resize(560, 760)
        root = QVBoxLayout(dlg)
        root.addWidget(SupportPage(dlg))
        close = QPushButton("继续免费使用")
        close.clicked.connect(dlg.accept)
        root.addWidget(close)
        dlg.exec()

    # ================================================================ 网络
    def refresh(self):
        """重新拉取 /me（进页面、点刷新、心跳后都调它）。"""
        self.badge_state.set_kind("idle", "同步中…")
        self._run(lambda: {"kind": "me", "data": kc_client.me()}, ctx="me")

    def _run(self, fn, ctx: str = ""):
        """把一次网络调用丢到后台线程；ctx 决定失败时提示落在哪个标签上。"""

        def worker():
            try:
                payload = fn()
            except kc_client.KcAuthError as e:
                payload = {"kind": "error", "msg": str(e), "auth": True, "ctx": ctx}
            except Exception as e:  # noqa: BLE001 —— 界面不该被网络异常打断
                payload = {"kind": "error", "msg": str(e), "ctx": ctx}
            self._sig.done.emit(payload)

        threading.Thread(target=worker, daemon=True).start()

    def _on_done(self, payload: dict):
        kind = payload.get("kind")
        if kind == "error":
            self.badge_state.set_kind("error", "失败")
            msg = payload.get("msg", "操作失败")
            self.sig_status.emit(f"用户中心：{msg}")
            ctx = payload.get("ctx") or ""
            if ctx in ("profile", "avatar"):
                self.lb_nick_tip.setText(msg)     # 重名等表单错误就近显示原文
            return

        if kind == "me":
            self._me = payload.get("data") or {}
            self._apply_me()
            url = self._me.get("avatar_url") or ""
            if url:
                self._run(lambda u=url: {"kind": "avatar", "bytes": kc_client.fetch_avatar(u)})
            else:
                self._avatar_pm = None
                self._paint_avatar()
            return

        if kind == "avatar":
            pm = QPixmap()
            if pm.loadFromData(QByteArray(payload.get("bytes") or b"")):
                self._avatar_pm = pm
            else:
                self._avatar_pm = None
            self._paint_avatar()
            return

        if kind == "saved":
            data = payload.get("data") or {}
            self._me.update(data)
            self._apply_me(keep_typing=True)
            self.badge_state.set_kind("ok", "已同步")
            self.sig_status.emit(f"已保存，用户名：{data.get('display_name') or ''}")
            self.sig_profile_changed.emit(dict(self._me))
            if data.get("has_avatar"):
                url = data.get("avatar_url") or ""
                self._run(lambda u=url: {"kind": "avatar", "bytes": kc_client.fetch_avatar(u)})
            elif "avatar" in data:
                self._avatar_pm = None
                self._paint_avatar()
            return

        if kind == "bound":
            self._bind_busy = False
            self.badge_state.set_kind("ok", "已同步")
            self.sig_status.emit("已解绑该设备")
            self.refresh()

    # ================================================================ 应用数据
    def _apply_me(self, keep_typing: bool = False):
        me = self._me
        name = me.get("display_name") or me.get("username") or ""
        if not keep_typing or not self.ed_nick.text().strip():
            self.ed_nick.setText(me.get("nickname") or name)
        self.lb_nick_tip.setText(
            f"用户名：{name}　·　登录账号：{me.get('username') or '—'}"
            + (f"　·　邮箱：{me.get('email')}" if me.get("email") else ""))
        self.lb_account._value_label.setText(me.get("username") or "—")      # type: ignore[attr-defined]
        self.badge_state.set_kind("ok", "已同步")
        self._paint_avatar()
        self._fill_devices(me.get("devices") or [], me.get("max_devices", 2))

    def _paint_avatar(self):
        if self._avatar_pm is not None and not self._avatar_pm.isNull():
            self.lb_avatar.setPixmap(_round_pixmap(self._avatar_pm, 88))
            self.btn_avatar_clear.setEnabled(True)
        else:
            name = self._me.get("display_name") or self._me.get("username") or "乐"
            self.lb_avatar.setPixmap(_initial_pixmap(str(name), 88))
            self.btn_avatar_clear.setEnabled(False)

    def _fill_devices(self, devices: list, max_devices: int):
        while self.device_box.count():
            item = self.device_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        head = QLabel(f"共 {len(devices)}/{max_devices} 台")
        head.setObjectName("CardHint")
        self.device_box.addWidget(head)
        if not devices:
            lab = QLabel("尚未在软件中登录过。")
            lab.setObjectName("CardHint")
            self.device_box.addWidget(lab)
            return
        for d in devices:
            self.device_box.addWidget(self._device_row(d))

    def _device_row(self, d: dict) -> QWidget:
        host = QFrame()
        host.setObjectName("DeviceRow")
        lay = QHBoxLayout(host)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(12)
        name = QLabel(d.get("machine_name") or "未命名设备")
        name.setMinimumWidth(140)
        mid = QLabel(f"最近活跃 {_fmt_time(d.get('last_seen'))}")
        mid.setObjectName("CardHint")
        btn = QPushButton("解绑")
        btn.setProperty("variant", "danger")
        btn.setMinimumHeight(28)
        btn.clicked.connect(lambda _=False, dev=dict(d): self._unbind(dev))
        lay.addWidget(name)
        lay.addWidget(mid, 1)
        lay.addWidget(btn)
        return host

    # ================================================================ 动作
    def _save_nickname(self):
        nick = self.ed_nick.text().strip()
        if not nick:
            self.lb_nick_tip.setText("用户名不能为空")
            return
        if nick == (self._me.get("nickname") or ""):
            self.lb_nick_tip.setText("用户名没有变化")
            return
        self.badge_state.set_kind("warn", "提交中…")
        self._run(lambda: {"kind": "saved",
                           "data": kc_client.update_profile(nickname=nick)}, ctx="profile")

    def _pick_avatar(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择头像图片", "", "图片 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not path:
            return
        data_url = self._encode_avatar(path)
        if not data_url:
            return
        self.badge_state.set_kind("warn", "上传头像…")
        self._run(lambda: {"kind": "saved",
                           "data": kc_client.update_profile(avatar=data_url)}, ctx="avatar")

    def _encode_avatar(self, path: str) -> str | None:
        """把图片文件转成上传用的 data URL：居中裁方 → 缩到 256px → PNG。

        与服务端、网页三处同规格（256px），所以正常情况只有几十 KB，
        不会撞到 1 MB 上限。失败返回 None 并给出提示。
        """
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "无法读取", "这个文件不是有效图片，换一张试试。")
            return None
        if img.width() < 1 or img.height() < 1:
            return None
        side = min(img.width(), img.height())
        square = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
        square = square.scaled(AVATAR_SIDE, AVATAR_SIDE, Qt.IgnoreAspectRatio,
                               Qt.SmoothTransformation)
        # ⚠ QImage.save(QBuffer, "PNG") 在本机 PySide6 6.11.2 下会 access violation：
        #   必须避免写成 save(QBuffer(QByteArray()), ...) —— QBuffer/QByteArray 作为
        #   临时对象时，C++ 侧所有权交接有竞态，直接崩（exit=127/139）。
        #   QImage/QPixmap 存「文件」都正常，只有「存到临时 QBuffer」这条会崩。
        #   写法：QByteArray + QBuffer 都先建好、保持局部引用，存完再取 bytes。
        store = QByteArray()
        buf = QBuffer(store)
        buf.open(QIODevice.WriteOnly)
        raw = b""
        try:
            if square.save(buf, "PNG"):
                raw = bytes(store)
        finally:
            buf.close()
        if not raw:
            QMessageBox.warning(self, "处理失败", "头像转码失败，换一张图片试试。")
            return None
        if len(raw) > 1024 * 1024:
            QMessageBox.warning(self, "图片太大", "头像超过 1 MB，请换一张更小的图片。")
            return None
        return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")

    def _clear_avatar(self):
        if not (self._me.get("avatar_url") or ""):
            return
        if QMessageBox.question(self, "移除头像", "确定移除当前头像吗？") != QMessageBox.Yes:
            return
        self.badge_state.set_kind("warn", "提交中…")
        self._run(lambda: {"kind": "saved", "data": kc_client.update_profile(avatar="")},
                  ctx="avatar")

    def _unbind(self, dev: dict):
        label = dev.get("machine_name") or str(dev.get("machine_id", ""))[:12]
        if getattr(self, "_bind_busy", False):
            return
        if QMessageBox.question(
                self, "解绑设备",
                f"解绑「{label}」？\n解绑后这台电脑需要重新登录才能继续使用。") != QMessageBox.Yes:
            return
        self._bind_busy = True
        did = int(dev.get("id") or 0)
        self._run(lambda: (kc_client.unbind_device(did), {"kind": "bound"})[1], ctx="device")

    def _open_pwd_page(self):
        self._open_url("/profile", "修改密码")

    def _open_forgot_page(self):
        self._open_url("/forget", "找回账号")

    def _open_site(self):
        self._open_url("/profile", "网页用户中心")

    def _open_url(self, path: str, what: str):
        """在浏览器打开门户网页（改密 / 找回都走网页，软件内不再内置表单）。"""
        import webbrowser

        url = kc_client._server() + path
        try:
            webbrowser.open(url)
            self.sig_status.emit(f"已在浏览器打开{what}页")
        except Exception:
            QMessageBox.information(self, "打开失败", f"请手动访问：{url}")

    def _ask_logout(self):
        if QMessageBox.question(
                self, "退出登录",
                "退出当前乐课账号？\n下次启动需要重新登录。") != QMessageBox.Yes:
            return
        self.sig_logout.emit()

    def apply_logout(self):
        """由主窗口确认后调用：清本地登录态。"""
        try:
            kc_client.logout()
        except Exception:
            pass
