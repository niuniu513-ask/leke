# -*- coding: utf-8 -*-
"""登录对话框（授权闸门）自测：登录 / 注册跳转 / 找回账号跳转。

背景（v1.6.6）：账号密码相关的事情统一到网页用户中心 ——
  · 注册     → /register（v1.6.0 起就是跳浏览器）
  · 找回账号 → /forget（v1.6.6 新增：邮箱验证码重置密码）
软件内不再内置注册/找回表单，所以这两条链路必须验「按钮真的在、跳的地址真的对」。

不联网：把 kc_client 的 load_state / _server 换成桩，webbrowser.open 换成记录器，
对话框构造时不会真的去验证 token（否则会发网络请求）。

跑法：cd chaoxing-main; .venv/Scripts/python.exe tests/regression/_test_login_dialog.py
"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(line_buffering=True)   # type: ignore[attr-defined]
except Exception:
    pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import webbrowser  # noqa: E402

from api import kc_client  # noqa: E402
from gui_app import login_dialog as LD  # noqa: E402

FAILS: list = []
PASSES = 0
BASE = "https://kc-api.lifedays.site"


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASSES
    if ok:
        PASSES += 1
        print(f"  [PASS] {name}" + (f"  {extra}" if extra else ""))
    else:
        FAILS.append(name + (f"  {extra}" if extra else ""))
        print(f"  [FAIL] {name}" + (f"  {extra}" if extra else ""))


def main() -> int:
    print("=" * 56)
    print("登录对话框（v1.6.6）验证")
    print("=" * 56)

    # 桩：没有历史 token ⇒ 不启动「静默验证」线程（否则会真发网络请求）
    kc_client.load_state = lambda: {}
    kc_client._server = lambda: BASE
    kc_client.check_access = lambda version="": {"mode": "login", "reason": ""}
    kc_client.login = lambda u, p, version="": {"ok": True}

    opened: list = []
    real_open = webbrowser.open
    webbrowser.open = lambda url, *a, **k: (opened.append(url), True)[1]

    app = QApplication.instance() or QApplication(sys.argv)
    dlg = LD.AccessGateDialog(version="1.6.6")
    try:
        print("\n[1] 界面结构")
        check("标题是「登录乐课账号」", dlg.title.text() == "登录乐课账号", dlg.title.text())
        check("窗口标题带品牌", "乐课" in dlg.windowTitle(), dlg.windowTitle())
        check("登录按钮默认（回车即登录）", dlg.btn_submit.isDefault())
        check("有「注册新账号」入口", dlg.btn_goto_reg is not None)
        check("有「忘记密码 / 找回账号」入口（v1.6.6 新增）",
              dlg.btn_forgot is not None and dlg.btn_forgot.text() == "忘记密码 / 找回账号",
              getattr(dlg, "btn_forgot", None) and dlg.btn_forgot.text())

        print("\n[2] 注册 / 找回都跳浏览器门户")
        opened.clear()
        dlg.btn_goto_reg.click()
        check("「注册新账号」跳 /register",
              bool(opened) and opened[-1] == BASE + "/register", str(opened))
        opened.clear()
        dlg.btn_forgot.click()
        check("「忘记密码 / 找回账号」跳 /forget",
              bool(opened) and opened[-1] == BASE + "/forget", str(opened))
        check("跳转后给用户明确回执（不是静默）",
              "已在浏览器打开" in dlg.msg.text() and BASE in dlg.msg.text(),
              dlg.msg.text())

        print("\n[3] 打开浏览器失败时给手动地址兜底")
        def _boom(url, *a, **k):
            raise OSError("no browser")

        webbrowser.open = _boom
        opened.clear()
        dlg._open_forgot()
        check("失败时提示手动访问地址",
              "打开浏览器失败" in dlg.msg.text() and BASE + "/forget" in dlg.msg.text(),
              dlg.msg.text())

        print("\n[4] 登录输入校验（不发请求）")
        dlg.in_user.setText("")
        dlg.in_pass.setText("")
        dlg._do_login()
        check("空账号/密码时不进入登录流程", "请输入账号和密码" in dlg.msg.text(), dlg.msg.text())
        check("校验失败时不置忙（按钮仍可点）", dlg.btn_submit.isEnabled())
    finally:
        webbrowser.open = real_open
        dlg.close()

    print("\n" + "=" * 56)
    print(f"结果：{PASSES} 通过 / {len(FAILS)} 失败")
    for f in FAILS:
        print("  失败:", f)
    print("=" * 56)
    return 1 if FAILS else 0


if __name__ == "__main__":
    code = main()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)
