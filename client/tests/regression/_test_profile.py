# -*- coding: utf-8 -*-
"""用户中心页（v1.6.3）自测：用户名 / 头像 / 设备 / 密码入口 / 登出。

不联网：把 kc_client 的资料相关函数换成桩，只验界面与调用契约。
验的是「网页与客户端共用同一份账号数据」这条硬规则有没有在客户端真正落地：
页面必须打 /me 拿数据、改用户名必须发 update_profile(nickname=…)、
重名必须把服务端原文显示出来。

v1.6.6 起「修改密码」表单迁到网页用户中心，客户端只留跳转入口
（/profile 改密、/forget 找回账号），这里改为验跳转地址是否正确。

跑法：cd chaoxing-main; .venv/Scripts/python.exe tests/regression/_test_profile.py
"""
from __future__ import annotations

import base64
import os
import sys
import time
import webbrowser

# 行缓冲：本机跑回归时偶发「进程被环境杀掉」（exit 127/139，stdout 块缓冲会把
# 已跑完的结果整块丢掉，表现为「脚本毫无输出」）。改成行缓冲后，哪怕中途挂掉
# 也能看到已经跑了哪些项，不会把失败伪装成静默。
try:
    sys.stdout.reconfigure(line_buffering=True)   # type: ignore[attr-defined]
except Exception:
    pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray  # noqa: E402
from PySide6.QtGui import QImage, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from api import kc_client                                    # noqa: E402
from gui_app import icons as I                               # noqa: E402
from gui_app import theme as T                               # noqa: E402

FAILS: list = []
PASSES = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASSES
    if ok:
        PASSES += 1
        print(f"  [PASS] {name}" + (f"  {extra}" if extra else ""))
    else:
        FAILS.append(name + (f"  {extra}" if extra else ""))
        print(f"  [FAIL] {name}" + (f"  {extra}" if extra else ""))


def wait_for(pred, timeout: float = 3.0) -> bool:
    """等后台线程跑完并把信号派发到主线程（服务端调用都在子线程）。"""
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return bool(pred())


def calls_of(kind: str) -> list:
    return [c for c in CALLS if c[0] == kind]


# ---------- kc_client 桩 ----------

ME_DATA = {
    "id": 1,
    "username": "niumain",
    "email": "niu@example.com",
    "nickname": "牛哥",
    "display_name": "牛哥",
    "avatar_url": "",
    "has_avatar": False,
    "expires_at": "2026-11-08T12:25:57Z",
    "points": 120,
    "devices": [
        {"id": 11, "machine_id": "mach-aaaa-0001", "machine_name": "台式机",
         "bound_at": "2026-10-01T00:00:00Z", "last_seen": "2026-10-09T12:00:00Z"},
        {"id": 12, "machine_id": "mach-bbbb-0002", "machine_name": "笔记本",
         "bound_at": "2026-10-02T00:00:00Z", "last_seen": "2026-10-08T12:00:00Z"},
    ],
    "max_devices": 2,
}

CALLS: list = []


class _Stub:
    """把 kc_client 的资料函数换成记录型桩。"""

    error_on_nickname = ""

    @staticmethod
    def me():
        CALLS.append(("me", None))
        return dict(ME_DATA)

    @classmethod
    def update_profile(cls, nickname=None, avatar=None):
        CALLS.append(("update_profile", {"nickname": nickname, "avatar": avatar}))
        if nickname and cls.error_on_nickname and nickname == cls.error_on_nickname:
            raise kc_client.KcError("该用户名已被使用，换一个试试")
        nick = nickname if nickname is not None else ME_DATA["nickname"]
        has_av = bool(avatar)
        if avatar is None:
            has_av = bool(ME_DATA["avatar_url"])
        return {"ok": True, "nickname": nick, "display_name": nick,
                "avatar_url": "/api/v1/avatars/1?v=abc" if has_av else "",
                "has_avatar": has_av}

    @staticmethod
    def fetch_avatar(path):
        CALLS.append(("fetch_avatar", path))
        img = QImage(8, 8, QImage.Format_RGB32)
        img.fill(0xFF3366CC)
        from PySide6.QtCore import QBuffer, QIODevice

        # ⚠ 不要写成 QImage.save(QBuffer(QByteArray()), "PNG")：临时 QBuffer 会让
        #   C++ 侧所有权交接出竞态，直接 access violation（本机 PySide6 6.11.2 实测）。
        store = QByteArray()
        buf = QBuffer(store)
        buf.open(QIODevice.WriteOnly)
        try:
            img.save(buf, "PNG")
            return bytes(store)
        finally:
            buf.close()

    @staticmethod
    def unbind_device(device_id):
        CALLS.append(("unbind_device", device_id))
        return {"ok": True}

    @staticmethod
    def change_password(old, new):
        CALLS.append(("change_password", (old, new)))
        return {"ok": True}

    @staticmethod
    def logout():
        CALLS.append(("logout", None))


def main() -> int:
    print("=" * 56)
    print("用户中心页（v1.6.3）验证")
    print("=" * 56)

    kc_client.me = _Stub.me
    kc_client.update_profile = _Stub.update_profile
    kc_client.fetch_avatar = _Stub.fetch_avatar
    kc_client.unbind_device = _Stub.unbind_device
    kc_client.change_password = _Stub.change_password
    kc_client.logout = _Stub.logout

    from gui_app import main_window as MW
    from gui_app.page_profile import ProfilePage, _initial_pixmap, _round_pixmap

    app = QApplication.instance() or QApplication(sys.argv)

    print("\n[1] 导航 / 主题 / 图标接线（数据驱动）")
    groups = [g for g, _ in MW.NAV]
    check("NAV 末尾新增「账户」组", groups[-1] == "账户", str(groups))
    check("账户组只放用户中心",
          [it[0] for g, items in MW.NAV if g == "账户" for it in items] == ["profile"])
    check("profile 进了 PAGE_KEYS", "profile" in MW.PAGE_KEYS)
    check("PAGE_GROUP / GROUP_BRAND 都认它",
          MW.PAGE_GROUP["profile"] == "账户" and MW.GROUP_BRAND["账户"] == "account")
    check("页面栈下标一致（stack == PAGE_INDEX）",
          MW.PAGE_INDEX["profile"] == len(MW.PAGE_KEYS) - 1
          and len(MW.PAGE_KEYS) == len(set(MW.PAGE_KEYS)))
    check("导航图标已注册", "profile" in I.ICONS)
    check("导航选中色走主色（NAV_BRAND）", T.NAV_BRAND.get("profile") == T.PRIMARY)
    qss = T.build_qss(os.path.join(ROOT, "assets"))
    check("平台 pill / 色条有 account 变体",
          'PlatformChip[brand="account"]' in qss and 'TopAccent[brand="account"]' in qss)
    check("账号概况块 / 设备行样式已入全局 QSS",
          "MiniStat" in qss and "DeviceRow" in qss)

    print("\n[2] 头像绘制（圆形裁剪 + 首字母兜底）")
    src = QPixmap(64, 64)
    src.fill(0xFF4F46E5)
    rp = _round_pixmap(src, 88)
    check("圆图尺寸正确", rp.width() == 88 and rp.height() == 88)
    check("圆图四角透明（真的裁圆了）",
          rp.toImage().pixelColor(1, 1).alpha() == 0
          and rp.toImage().pixelColor(44, 44).alpha() > 0)
    ip = _initial_pixmap("牛", 88)
    check("首字母兜底非空且有内容",
          not ip.isNull() and ip.toImage().pixelColor(44, 44).alpha() > 0)

    print("\n[3] 拉取资料并落界面")
    page = ProfilePage()
    wait_for(lambda: page._me.get("id") == 1)
    check("请求了 /me", ("me", None) in CALLS)
    check("用户名填进输入框", page.ed_nick.text() == "牛哥", page.ed_nick.text())
    check("账号显示登录名", page.lb_account._value_label.text() == "niumain")
    check("剩余天数算出来了（不是 —）",
          page.lb_left._value_label.text().endswith("天"),
          page.lb_left._value_label.text())
    check("积分显示", page.lb_points._value_label.text() == "120")
    check("无头像时用首字母兜底、禁掉「移除头像」",
          not page.lb_avatar.pixmap().isNull() and not page.btn_avatar_clear.isEnabled())
    check("设备行两行", page.device_box.count() == 3, str(page.device_box.count()))  # 标题 + 2

    print("\n[4] 改用户名：走后端、重名给原文")
    page.ed_nick.setText("牛哥本尊")
    page._save_nickname()
    ok = wait_for(lambda: bool(calls_of("update_profile")))
    call = calls_of("update_profile")[-1] if ok else ("", {})
    check("发送了 update_profile(nickname=…)",
          call[1] == {"nickname": "牛哥本尊", "avatar": None}, str(call[1]))
    check("保存后输入框保留新名", page.ed_nick.text() == "牛哥本尊", page.ed_nick.text())

    _Stub.error_on_nickname = "占用名"
    page.ed_nick.setText("占用名")
    page._save_nickname()
    wait_for(lambda: "已被使用" in page.lb_nick_tip.text())
    check("重名时把服务端原文显示出来（不是笼统报错）",
          "已被使用" in page.lb_nick_tip.text(), page.lb_nick_tip.text())
    _Stub.error_on_nickname = ""
    page.ed_nick.setText("牛哥本尊")

    print("\n[5] 换头像：编码 → 上传 → 回读")
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="_test_profile_")
    src_path = os.path.join(tmpdir, "big.png")
    big = QImage(900, 600, QImage.Format_RGB32)
    big.fill(0xFF4F46E5)
    big.save(src_path, "PNG")
    data_url = page._encode_avatar(src_path)
    check("头像编码成 PNG data URL",
          bool(data_url) and data_url.startswith("data:image/png;base64,"),
          (data_url or "")[:32])
    raw = base64.b64decode(data_url.split(",", 1)[1])
    check("编码后不超 1 MB 上限", len(raw) < 1024 * 1024, f"{len(raw)} B")
    # 回归防护：QImage.save(QBuffer, "PNG") 必须走「QByteArray 先建、QBuffer 持引用」
    # 的写法，不能写成 save(QBuffer(QByteArray()), ...)（临时对象 → access violation）。
    # 源码里一旦出现该反模式，这里直接判失败，不让它再靠"偶发"混过去。
    import inspect
    import re as _re

    src = inspect.getsource(page.__class__._encode_avatar)
    # 去掉注释行再判，否则注释里提到反模式也会被算中
    code_only = "\n".join(ln for ln in src.splitlines()
                          if not ln.lstrip().startswith("#"))
    check("头像编码不踩 QBuffer 临时对象反模式",
          "QBuffer(QByteArray())" not in code_only,
          "见 page_profile._encode_avatar")
    check("PNG 编码走 PNG 编解码器（magic 头正确）",
          raw[:8] == b"\x89PNG\r\n\x1a\n", raw[:8].hex())
    decoded = QImage.fromData(raw)
    check("编码结果是 256×256（居中裁方 + 缩放）",
          decoded.width() == 256 and decoded.height() == 256,
          f"{decoded.width()}×{decoded.height()}")

    page._run(lambda: {"kind": "saved",
                       "data": kc_client.update_profile(avatar=data_url)}, ctx="avatar")
    ok = wait_for(lambda: bool(calls_of("fetch_avatar")), timeout=4.0)
    check("上传成功后按地址回读头像", ok)
    wait_for(lambda: page.btn_avatar_clear.isEnabled())
    check("有头像后「移除头像」可用", page.btn_avatar_clear.isEnabled())
    check("头像载入为圆形 pixmap（88px）",
          not page.lb_avatar.pixmap().isNull() and page.lb_avatar.pixmap().width() == 88,
          str(page.lb_avatar.pixmap().width()))
    check("超长图也裁成正方形（不留拉伸）",
          decoded.width() == decoded.height())
    import shutil

    shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n[6] 设备解绑 / 密码入口 / 登出契约")
    page._run(lambda: (kc_client.unbind_device(12), {"kind": "bound"})[1], ctx="device")
    wait_for(lambda: bool(calls_of("unbind_device")))
    check("解绑打了 unbind_device(id)", ("unbind_device", 12) in CALLS)
    page._on_done({"kind": "bound"})
    wait_for(lambda: len(calls_of("me")) >= 2)
    check("解绑后重新拉了 /me（列表会刷新）", len(calls_of("me")) >= 2,
          f"{len(calls_of('me'))} 次")

    # v1.6.6：改密表单已迁到网页，客户端只留跳转入口 —— 验它跳的地址对不对。
    opened: list = []
    _real_open = webbrowser.open
    webbrowser.open = lambda url, *a, **k: (opened.append(url), True)[1]
    try:
        opened.clear()
        page.btn_pwd_web.click()
        check("「打开网页修改密码」跳 /profile",
              bool(opened) and opened[-1].endswith("/profile"), str(opened))
        opened.clear()
        page.btn_forgot_web.click()
        check("「忘记密码 / 找回账号」跳 /forget",
              bool(opened) and opened[-1].endswith("/forget"), str(opened))
        opened.clear()
        page._open_site()
        check("「打开网页用户中心」仍跳 /profile",
              bool(opened) and opened[-1].endswith("/profile"), str(opened))
    finally:
        webbrowser.open = _real_open
    check("客户端不再内置改密表单（已迁网页）",
          not hasattr(page, "ed_old") and not hasattr(page, "lb_pwd"))

    page.apply_logout()
    check("登出清本地登录态", ("logout", None) in CALLS)

    print("\n[7] 主窗口接线（信号不悬空）")
    win = MW.MainWindow()
    win.resize(1300, 860)
    check("主窗口装了用户中心页", isinstance(win.profile_page, ProfilePage))
    check("导航行数 = 页面数 + 分组数",
          win.nav.count() == len(MW.PAGE_KEYS) + len(MW.NAV), str(win.nav.count()))
    win.goto("profile")
    app.processEvents()
    check("goto('profile') 切到该页", win.stack.currentIndex() == MW.PAGE_INDEX["profile"])
    check("顶栏平台标签切到「账户」（+ 色条换 brand）",
          win._group_tabs["账户"].isChecked()
          and win.top_accent.property("brand") == "account",
          str(win.top_accent.property("brand")))
    win.profile_page._on_done({"kind": "me", "data": dict(ME_DATA)})
    check("页面数据能进主窗口（不抛异常）", True)
    win.close()

    print("\n" + "=" * 56)
    print(f"结果：{PASSES} 通过 / {len(FAILS)} 失败")
    for f in FAILS:
        print("  失败:", f)
    print("=" * 56)
    return 1 if FAILS else 0


if __name__ == "__main__":
    code = main()
    # offscreen 下完整 MainWindow 析构阶段偶发 SIGSEGV（后台线程/QTimer 残留），
    # 直接 os._exit 跳过析构。否则 stdout 是块缓冲（重定向到文件时），
    # 崩溃会把缓冲里的结果一起丢掉 —— 表现为「脚本没有任何输出」，
    # 批量回归会静默漏掉这个脚本。
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)
