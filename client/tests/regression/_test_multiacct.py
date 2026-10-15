# -*- coding: utf-8 -*-
"""多账号专项验证：会话隔离 / cookie 路径隔离 / 档案 CRUD / 并行刷课互不干扰。

全程用临时目录，不碰用户的真实 accounts.json / config.ini / cookies。

跑法：.venv\\Scripts\\python.exe tests/regression/_test_multiacct.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

os.environ.pop("QT_QPA_PLATFORM", None)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

from gui_app import store as S          # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []

TMP = tempfile.mkdtemp(prefix="leke_acc_")
# 把程序目录指到临时目录，避免污染真实的 accounts.json / cookies。
# profiles / session 都经 store.app_dir() 取路径，替换这一个函数就够了。
_orig_app_dir = S.app_dir
S.app_dir = lambda: TMP                                   # type: ignore[assignment]


def check(name: str, ok: bool, extra: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK' if ok else 'XX'}] {name}" + (f"  {extra}" if extra else ""))


try:
    # ==================================================================
    print("[1] 账号档案 CRUD（临时目录）")

    from gui_app import profiles as P       # noqa: E402

    book = P.ProfileBook({"accounts": [], "active": ""})
    book.save()

    a1 = book.add(name="主号", username="18032901968", password="pw1")
    a2 = book.add(name="小号", username="13900001111", password="pw2")
    check("新增 2 个账号", len(book.accounts) == 2, f"得到 {len(book.accounts)}")
    check("两个 id 不重复", a1["id"] != a2["id"], f"{a1['id']} vs {a2['id']}")
    check("备注名可读", book.display_name(a1) == "主号")
    check("get 能取到", (book.get(a2["id"]) or {}).get("username") == "13900001111")

    book.update(a1["id"], name="改名后")
    check("update 改备注名", book.display_name(a1) == "改名后")

    book.set_courses(a1["id"], ["100", "200"])
    book.set_courses(a2["id"], ["300"])
    check("课程勾选按账号隔离（A）", book.courses(a1["id"]) == ["100", "200"])
    check("课程勾选按账号隔离（B）", book.courses(a2["id"]) == ["300"])
    check("A 的勾选没串到 B", "100" not in book.courses(a2["id"]))

    book.save()
    reloaded = P.ProfileBook.load()
    # load() 会自动补一个 default 账号（把 config.ini 老配置迁进来），
    # 所以这里按 id 判断，而不是数个数。
    by_id = {a["id"]: a for a in reloaded.accounts}
    check("落盘后原来的两个账号都在", a1["id"] in by_id and a2["id"] in by_id,
          f"得到 {sorted(by_id)}")
    check("load 会自动补上 default 账号", P.DEFAULT_ID in by_id,
          f"得到 {sorted(by_id)}")
    check("落盘后课程勾选保留",
          reloaded.courses(a1["id"]) == ["100", "200"],
          f"得到 {reloaded.courses(a1['id'])}")
    check("落盘后备注名保留", by_id[a1["id"]].get("name") == "改名后")

    # 再删掉自动补的 default，让后续断言数量可控
    reloaded.remove(P.DEFAULT_ID)
    book = reloaded
    book.remove(a2["id"])
    check("删除账号后只剩 1 个", len(book.accounts) == 1,
          f"得到 {[a['id'] for a in book.accounts]}")
    check("被删的账号取不到", book.get(a2["id"]) is None)

    # ==================================================================
    print("\n[2] cookie 文件路径隔离")

    def _rel(p: str) -> str:
        return os.path.basename(p)

    c_def = P.cookie_path("default")
    c_a = P.cookie_path(a1["id"])
    c_cn = P.cookie_path("账号-中文#1")
    check("default 用 cookies.txt", _rel(c_def) == "cookies.txt", _rel(c_def))
    check("其他账号用 cookies_<id>.txt", _rel(c_a) == f"cookies_{a1['id']}.txt", _rel(c_a))
    check("两个账号 cookie 路径不同", c_def != c_a)
    check("非法字符被清洗", "#" not in _rel(c_cn), _rel(c_cn))

    check("脱敏：11 位手机号", P.mask_user("18032901968") == "180****1968")
    check("脱敏：空值", P.mask_user("") == "（未填写）")
    check("脱敏：非手机号原样", P.mask_user("stud_001") == "stud_001")

    # ==================================================================
    print("\n[3] 会话隔离：两个账号互不干扰")

    from api.base import SessionManager  # noqa: E402

    SessionManager._pool.clear()
    sa = SessionManager.create_session("acc_a")
    sb = SessionManager.create_session("acc_b")
    check("两次 create_session 返回不同对象", sa is not sb)
    check("同一 key 复用同一对象", SessionManager.create_session("acc_a") is sa)
    sa.headers["X-Test"] = "A"
    check("给 A 加的头不会串到 B", "X-Test" not in sb.headers)
    check("空 key 拿到的是全局单例会话",
          SessionManager.create_session() is SessionManager.get_session())
    check("全局单例与 A 不是同一个", SessionManager.create_session() is not sa)
    check("反复取全局会话都是同一个对象（不会被重建）",
          SessionManager.get_session() is SessionManager.get_session())
    check("pool 里只有 A / B 两个 key",
          set(SessionManager._pool) == {"acc_a", "acc_b"}, f"得到 {sorted(SessionManager._pool)}")
    SessionManager.drop_session("acc_a")
    check("drop 后重建得到新对象", SessionManager.create_session("acc_a") is not sa)
    SessionManager._pool.clear()

    # ==================================================================
    print("\n[4] Chaoxing 实例按 key 拿自己的 session")

    import api.base as B  # noqa: E402

    SessionManager._pool.clear()
    ca = B.Chaoxing(account=None, tiku=None, session_key="k1",
                    cookie_path=os.path.join(TMP, "cookies_k1.txt"))
    cb = B.Chaoxing(account=None, tiku=None, session_key="k2",
                    cookie_path=os.path.join(TMP, "cookies_k2.txt"))
    check("两个实例 session 不同", ca._session is not cb._session)
    check("instance A 指向自己的 session",
          ca._session is SessionManager.create_session("k1"))
    check("cookie 路径已按账号落地", ca.cookie_path.endswith("cookies_k1.txt"),
          os.path.basename(ca.cookie_path))
    check("两个实例 cookie 路径不同", ca.cookie_path != cb.cookie_path)
    check("session_key 已记录", ca.session_key == "k1" and cb.session_key == "k2")

    # 单账号（不传 key）沿用全局会话 —— 命令行行为完全不变
    c0 = B.Chaoxing(account=None, tiku=None)
    check("不传 key 时 cookie 走默认路径",
          os.path.basename(c0.cookie_path) == "cookies.txt",
          os.path.basename(c0.cookie_path))
    check("不传 key 的实例不占用 pool",
          c0.session_key == "" and "k1" in SessionManager._pool,
          f"session_key={c0.session_key!r} pool={sorted(SessionManager._pool)}")
    SessionManager._pool.clear()

    # ==================================================================
    print("\n[5] Runner：多账号并行状态互不干扰")

    from PySide6.QtWidgets import QApplication  # noqa: E402

    app = QApplication.instance() or QApplication([])

    import gui_app.worker as W  # noqa: E402

    r = W.Runner()
    st_a = r.session("a", "账号A", cookie_path=os.path.join(TMP, "cookies_a.txt"))
    st_b = r.session("b", "账号B", cookie_path=os.path.join(TMP, "cookies_b.txt"))
    check("两个 session 对象不同", st_a is not st_b)
    check("各带各的 cookie 路径", st_a.cookie_path != st_b.cookie_path,
          f"{os.path.basename(st_a.cookie_path)} vs {os.path.basename(st_b.cookie_path)}")
    check("各带各的 stop_event", st_a.stop_event is not st_b.stop_event)

    st_a.studying = True
    check("A 在跑不影响 B", r.is_studying("a") and not r.is_studying("b"))
    st_a.chaoxing = object()
    check("A 登录不影响 B", r.is_logged_in("a") and not r.is_logged_in("b"))
    check("未注册的账号默认未登录", not r.is_logged_in("nobody"))
    check("sessions 列表含两个", len(r.sessions()) == 2, f"得到 {len(r.sessions())}")

    st_a.studying = False
    st_a.chaoxing = None
    check("复位后状态正确", not r.is_studying("a") and not r.is_logged_in("a"))

    # ==================================================================
    print("\n[6] 信号都带 account_id（防止串号）")

    def _arg_types(sig) -> list[str]:
        """从 PySide6 的 'name(QString,PyObject)' 里解析出参数类型列表。

        注意 sig.signatures[0][0] 只给出 's'（Qt 内部类型码），不能用。
        """
        s = sig.signatures[0]
        inner = s[s.index("(") + 1: s.rindex(")")]
        return [x.strip() for x in inner.split(",") if x.strip()]

    sig_names = ["sig_login", "sig_courses", "sig_course_progress", "sig_study_total",
                 "sig_study_pass", "sig_study_update", "sig_study_current",
                 "sig_study_totals", "sig_state", "sig_status", "sig_finished"]
    bad = []
    for n in sig_names:
        sig = getattr(W.Runner, n, None)
        if sig is None:
            bad.append(f"{n}(缺失)")
            continue
        # PySide6 把 str 报成 QString、任意对象报成 PyObject
        types = _arg_types(sig)
        if not types or types[0] not in ("str", "QString"):
            bad.append(f"{n}({types})")
    check("所有刷课相关信号首参都是账号 id 字符串", not bad, f"异常: {bad}")

    kinds = {n: _arg_types(getattr(W.Runner, n)) for n in sig_names}
    check("sig_study_current = (账号, 详情对象)",
          kinds["sig_study_current"][0] in ("str", "QString")
          and kinds["sig_study_current"][1] in ("object", "PyObject"),
          f"得到 {kinds['sig_study_current']}")
    check("sig_study_pass = (账号, 遍数)",
          kinds["sig_study_pass"][1] in ("int", "PyObject", "object"),
          f"得到 {kinds['sig_study_pass']}")
    check("sig_finished = (账号, 类型, 状态, 文案)",
          len(kinds["sig_finished"]) == 4, f"得到 {kinds['sig_finished']}")

finally:
    print("\n[7] 收尾：清理")
    try:
        from api.base import SessionManager as _SM

        _SM._pool.clear()
    except Exception:
        pass
    shutil.rmtree(TMP, ignore_errors=True)
    check("临时目录已清理", not os.path.exists(TMP), TMP)
    S.app_dir = _orig_app_dir                            # type: ignore[assignment]

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
for f in FAIL:
    print("  失败:", f)
sys.exit(1 if FAIL else 0)
