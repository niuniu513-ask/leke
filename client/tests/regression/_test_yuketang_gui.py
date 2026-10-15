# -*- coding: utf-8 -*-
"""雨课堂页 + 多账号 + 登录链路自测（离屏，不联网，不碰真实账号文件）。

覆盖：
- 侧栏分门别类 / 数据驱动跳转 / 快捷键（沿用旧用例）；
- AccountStore：新增 / 切换 / 删除 / 多账号落盘 / 旧单账号文件自动迁移；
- 每账号一个 runner 的注册表（get_runner / all_runners / drop_runner / shutdown_all）；
- AccountBar 接线：switch / add / remove 信号、set_running 圆点；
- 登录分步骤文案到界面、失败 sig_done(error) + QMessageBox.warning 原文；
- cookie 兜底登录成功 / 失败两条路径（mock fetch_identity）；
- 多账号并行 start()，各自有进度回调；
- 页面日志行首带 [账号名]、_log 双写 loguru。

跑法：cd chaoxing-main; .venv/Scripts/python.exe tests/regression/_test_yuketang_gui.py

注意：
* 账号文件全部落在临时目录（替换 platform_accounts / yuketang_engine 里的
  store 替身），绝不读写项目根的 yuketang.json；
* 用假 api.logger 顶替，测试不落 leke.log，也不调用 stream.install()。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import types

os.environ.pop("QT_QPA_PLATFORM", None)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

# ---------------------------------------------------------------- 假 api.logger
LOG_CALLS: list = []
_logger_mod = types.ModuleType("api.logger")


class _FakeLogger:
    def __getattr__(self, name):
        def _call(*args, **kwargs):
            LOG_CALLS.append((name,) + tuple(args))
        return _call


_logger_mod.logger = _FakeLogger()
sys.modules["api.logger"] = _logger_mod

import api  # noqa: E402

api.logger = _logger_mod

# ---------------------------------------------------------------- 账号落到临时目录
TMP = tempfile.mkdtemp(prefix="yt_gui_")


class _TmpStore:
    """只提供 app_dir 的 store 替身（AccountStore / 引擎只用这一个方法）。"""

    def __init__(self, path):
        self._path = path

    def app_dir(self):
        return self._path


import gui_app.platform_accounts as pa  # noqa: E402
import gui_app.yuketang_engine as ye  # noqa: E402

pa.store = _TmpStore(TMP)
ye.store = _TmpStore(TMP)
ye.reset_store()

from PySide6.QtCore import Qt as _Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from gui_app import theme as T  # noqa: E402
from gui_app.main_window import (  # noqa: E402
    GROUP_BRAND, NAV, PAGES, PAGE_GROUP, PAGE_INDEX, PAGE_KEYS, MainWindow,
)
import gui_app.page_yuketang as ypage  # noqa: E402
from gui_app.platform_accounts import AccountStore  # noqa: E402
from gui_app.widgets import AccountBar  # noqa: E402

DEFAULTS = dict(ye.ACCOUNT_DEFAULTS)

FAILS: list = []
PASSES = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASSES
    if ok:
        PASSES += 1
        print("  [PASS] " + name + ("  " + extra if extra else ""))
    else:
        FAILS.append(name + ("  " + extra if extra else ""))
        print("  [FAIL] " + name + ("  " + extra if extra else ""))


class _FakeClient:
    """假的雨课堂客户端：只回放一次进度，不发网络请求。"""

    def __init__(self, tag: str):
        self.tag = tag

    def watch_course_videos(self, *args, **kwargs):
        on_start = kwargs.get("on_video_start")
        on_prog = kwargs.get("on_progress")
        if on_start:
            on_start("v-" + self.tag, self.tag + "视频1")
        if on_prog:
            on_prog({"video_name": self.tag + "视频1", "rate": 0.6,
                     "watch_length": 12})
        return {"total": 1, "done": 1, "skipped": 0, "failed": []}

    def close(self):
        pass


def wait_idle(app, runners, timeout: float = 10.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if not any(r.busy() for r in runners):
            break
        time.sleep(0.01)
    for _ in range(5):
        app.processEvents()
        time.sleep(0.005)


def main() -> int:
    print("=" * 56)
    print("雨课堂页（多账号 + 登录链路）验证")
    print("=" * 56)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(T.build_qss(os.path.join(ROOT, "assets")))

    dialog_state = {"warning": [], "question": []}

    class _FakeBox:
        Yes = 1
        No = 2

        @staticmethod
        def warning(parent, title, text):
            dialog_state["warning"].append((title, text))
            return 0

        @staticmethod
        def question(parent, title, text, *args, **kwargs):
            dialog_state["question"].append(title)
            return _FakeBox.Yes

    real_box = ypage.QMessageBox
    ypage.QMessageBox = _FakeBox

    # 真实用户文件一个都不许被改：先备份，跑完还原（MainWindow 关闭会写
    # gui_settings.json 的窗口几何；yuketang.json 必须原样不动）。
    import gui_app.store as appstore

    ui_path = appstore.ui_path()
    legacy_path = os.path.join(ROOT, ye.YUKETANG_FILE)
    root_accounts = os.path.join(ROOT, ye.ACCOUNTS_FILE)
    ui_backup = None
    if os.path.exists(ui_path):
        with open(ui_path, "rb") as f:
            ui_backup = f.read()
    legacy_backup = None
    if os.path.exists(legacy_path):
        with open(legacy_path, "rb") as f:
            legacy_backup = f.read()
    root_accounts_existed = os.path.exists(root_accounts)

    try:
        # v1.5.6：导航分组 = 平台。此前是"运行 / 学习通 / 其他平台 / 应用设置"，
        # 把学习通自己的页面拆到两个组，又把两个平台塞进同一个"其他平台"，
        # 结果看不出有几个平台、现在在哪个。
        print("\n[1] 侧栏按平台分门别类（6 组 / 10 页）")
        groups = [g for g, _items in NAV]
        check("分组顺序 = 学习通 / U校园 / 雨课堂 / 智慧树 / 应用设置 / 账户",
              groups == ["学习通", "U校园", "雨课堂", "智慧树", "应用设置", "账户"], str(groups))
        check("每组至少一个页面", all(items for _g, items in NAV))
        check("页面 key 唯一", len(set(PAGE_KEYS)) == len(PAGE_KEYS), str(PAGE_KEYS))
        check("学习通组含账号/课程/刷课/课堂互动四个页面",
              [it[0] for g, items in NAV if g == "学习通" for it in items]
              == ["account", "courses", "run", "live"])
        check("U校园 / 雨课堂 各自独立成组、互不混装",
              [it[0] for g, items in NAV if g == "U校园" for it in items] == ["unipus"]
              and [it[0] for g, items in NAV if g == "雨课堂" for it in items] == ["yuketang"])
        check("账户组 = 用户中心（v1.6.3）",
              [it[0] for g, items in NAV if g == "账户" for it in items] == ["profile"])
        check("页面 key -> 分组 映射完整",
              set(PAGE_GROUP.keys()) == set(PAGE_KEYS)
              and PAGE_GROUP["unipus"] == "U校园" and PAGE_GROUP["run"] == "学习通")
        check("平台 -> 品牌 key 映射齐备",
              GROUP_BRAND["学习通"] == "chaoxing" and GROUP_BRAND["U校园"] == "unipus"
              and GROUP_BRAND["雨课堂"] == "yuketang" and GROUP_BRAND["账户"] == "account")
        check("页面栈下标与 PAGE_KEYS 严格对应（构造方式保证）",
              PAGE_INDEX["account"] == 0 and PAGE_INDEX["run"] == 2)

        # Navigation validation must not start real classroom network requests.
        from gui_app.page_live import LivePage
        real_load_courses = LivePage._load_courses
        LivePage._load_courses = lambda self: None
        try:
            win = MainWindow()
        finally:
            LivePage._load_courses = real_load_courses
        win.live_page._load_courses = lambda: None
        win.resize(1300, 860)
        win.show()
        app.processEvents()

        check("页面栈页数 = 页面数", win.stack.count() == len(PAGE_KEYS),
              str(win.stack.count()) + " vs " + str(len(PAGE_KEYS)))
        check("导航行数 = 页面数 + 分组标题数",
              win.nav.count() == len(PAGE_KEYS) + len(NAV), str(win.nav.count()))

        print("\n[2] 分组标题不可点 / 不可选")
        head_rows = [i for i in range(win.nav.count()) if not win._row_key.get(i)]
        check("标题行数 = 分组数", len(head_rows) == len(NAV), str(head_rows))
        check("标题行 flags 为 NoItemFlags",
              all(win.nav.item(i).flags() == _Qt.NoItemFlags for i in head_rows))
        check("页面行都可选中",
              all(bool(win.nav.item(win._key_row[k]).flags() & _Qt.ItemIsSelectable)
                  for k in PAGE_KEYS))

        print("\n[3] 数据驱动跳转：goto(key) / goto(int)")
        ok = True
        for key in PAGE_KEYS:
            win.goto(key)
            app.processEvents()
            if win.stack.currentIndex() != PAGE_INDEX[key]:
                ok = False
                break
            if win.lb_title.text() != win._nav_items[key].text():
                ok = False
                break
        check("每个 key 都落到正确的页面栈下标且标题同步", ok)
        # 硬规则：导航数据驱动，别写 goto(4) —— 页面顺序会随 NAV 调整而变
        idx = PAGE_INDEX["yuketang"]
        win.goto(idx)
        app.processEvents()
        check("goto(int) 与 goto(PAGE_KEYS[int]) 等价",
              win.stack.currentIndex() == PAGE_INDEX[PAGE_KEYS[idx]])
        check("goto(索引) 落在雨课堂",
              PAGE_KEYS[idx] == "yuketang" and win.lb_title.text() == PAGES[idx][0],
              win.lb_title.text())

        print("\n[4] Ctrl+1..9 / Ctrl+0 快捷键")
        check("快捷键数量 = 页面数",
              len(getattr(win, "_shortcuts", [])) == len(PAGE_KEYS),
              str(len(getattr(win, "_shortcuts", []))))
        check("第 1 个快捷键文本是 Ctrl+1",
              win._shortcuts[0].key().toString() == "Ctrl+1",
              win._shortcuts[0].key().toString())
        # 第 10 页起序号取模成单个数字（Qt 不认 "Ctrl+10"，会解析成空串）
        _last_want = "Ctrl+" + str(len(PAGE_KEYS) % 10)
        check("最后一个快捷键文本是 Ctrl+N（第 10 页 = Ctrl+0）",
              win._shortcuts[-1].key().toString() == _last_want,
              win._shortcuts[-1].key().toString())
        check("全部快捷键都解析成功（无空串）",
              all(sc.key().toString() for sc in win._shortcuts),
              str([sc.key().toString() for sc in win._shortcuts]))

        # ============================================================ [5] 档案
        print("\n[5] AccountStore：新增 / 切换 / 删除 / 落盘 / 旧文件迁移")
        sub = os.path.join(TMP, "storecase")
        os.makedirs(sub, exist_ok=True)
        saved_store = pa.store
        pa.store = _TmpStore(sub)
        try:
            book = AccountStore("accs.json", "legacy.json", DEFAULTS).load()
            check("空档案加载不炸", book.accounts == [])
            a1 = book.add("账号A")
            a2 = book.add("账号B")
            check("新增可指定名字且自动编号兜底",
                  a1["name"] == "账号A" and a2["name"] == "账号B")
            check("新增后自动设为当前", book.active == a2["id"])
            book.set_active(a1["id"])
            check("切换当前账号并落盘", book.active == a1["id"])
            book.update(a1["id"], cookie="csrftoken=1; sessionid=1", user_id="7")
            check("逐字段合并",
                  book.get(a1["id"])["user_id"] == "7"
                  and book.get(a1["id"])["cookie"].startswith("csrftoken"),
                  str(book.get(a1["id"])))
            check("显示名优先 name", book.display_name(a1["id"]) == "账号A")
            check("pairs 顺序与档案一致",
                  [p[0] for p in book.pairs()] == [a1["id"], a2["id"]])
            reloaded = AccountStore("accs.json", "legacy.json", DEFAULTS).load()
            check("多账号落盘后回读",
                  len(reloaded.accounts) == 2 and reloaded.active == a1["id"]
                  and reloaded.get(a1["id"])["user_id"] == "7")
            book.remove(a1["id"])
            check("删除后当前账号自动落到剩余那条",
                  len(book.accounts) == 1 and book.active == a2["id"])

            legacy = os.path.join(sub, "legacy.json")
            with open(legacy, "w", encoding="utf8") as f:
                f.write('{"domain": "ustc.yuketang.cn", "cookie": "csrftoken=old",'
                        ' "user_id": "99"}')
            migrated = AccountStore("accs2.json", "legacy.json", DEFAULTS).load()
            check("旧单账号文件自动迁移成一条",
                  len(migrated.accounts) == 1
                  and migrated.accounts[0]["user_id"] == "99"
                  and migrated.accounts[0]["domain"] == "ustc.yuketang.cn",
                  str(migrated.accounts))
            check("迁移后旧文件改名 .migrated",
                  (not os.path.exists(legacy))
                  and os.path.exists(legacy + ".migrated"))
            check("迁移记录带 migrated_from",
                  migrated.accounts[0].get("migrated_from") == "legacy.json")
        finally:
            pa.store = saved_store

        # ============================================================ [6] runner 注册表
        print("\n[6] 每账号一个 runner 注册表")
        ra = ye.get_runner("acc_unit_a")
        rb = ye.get_runner("acc_unit_b")
        check("不同账号拿到不同 runner",
              ra is not rb and ra.acc_id == "acc_unit_a" and rb.acc_id == "acc_unit_b")
        check("同账号复用同一实例", ye.get_runner("acc_unit_a") is ra)
        check("all_runners 含这两个账号",
              {"acc_unit_a", "acc_unit_b"} <= set(ye.all_runners()))
        ye.drop_runner("acc_unit_b")
        check("drop_runner 摘掉实例", "acc_unit_b" not in ye.all_runners())
        crashed = False
        try:
            ye.shutdown_all()
        except Exception:
            crashed = True
        check("shutdown_all 不炸", not crashed)

        # ============================================================ [7] 页面
        print("\n[7] 雨课堂页控件与状态机")
        win.goto("yuketang")
        app.processEvents()
        pg = win.yuketang_page
        check("页面账号档案落在临时目录（不碰项目根）",
              pg._store.path().startswith(TMP), pg._store.path())
        check("引擎旧文件名路径也指向临时目录",
              ye.yuketang_path().startswith(TMP), ye.yuketang_path())
        for attr in ("btn_login", "btn_cancel", "btn_start_all", "btn_stop_all",
                     "ed_domain", "btn_save_domain", "btn_logout", "btn_load",
                     "btn_start", "btn_stop", "cb_workers", "courses_box", "bar",
                     "account_bar",
                     "badge", "qr_box", "lb_qr", "lb_account", "lb_step",
                     "cookie_box", "ed_cookie", "btn_cookie", "btn_cookie_ok",
                     "summary_box", "lb_count", "empty", "log", "lb_now",
                     "lb_stats"):
            check("控件 " + attr + " 存在", hasattr(pg, attr))
        check("进度条是浮点自绘的 SmoothBar",
              pg.bar.__class__.__name__ == "SmoothBar")
        check("账号条是 AccountBar 且 brand=yuketang",
              isinstance(pg.account_bar, AccountBar)
              and pg.account_bar.property("brand") == "yuketang")
        check("进度条与账号条是两个不同控件", pg.bar is not pg.account_bar)
        check("cookie 输入是密码回显",
              pg.ed_cookie.echoMode() == pg.ed_cookie.EchoMode.Password)
        check("cookie 入口默认折叠", not pg.cookie_box.isVisibleTo(pg))

        pg.set_state("idle")
        check("idle：开始禁用、停止禁用",
              not pg.btn_start.isEnabled() and not pg.btn_stop.isEnabled())
        check("未登录时「读取课程」置灰并说明原因",
              not pg.btn_load.isEnabled() and "登录" in pg.btn_load.toolTip(),
              pg.btn_load.toolTip())
        check("未登录时「开始刷课」给出登录提示",
              "登录" in pg.btn_start.toolTip(), pg.btn_start.toolTip())

        pg.runner._account["cookie"] = "csrftoken=test; sessionid=test"
        pg.set_state("ready")
        check("已登录：读取课程可用、取消按钮收起",
              pg.btn_load.isEnabled() and not pg.btn_cancel.isVisibleTo(pg))
        check("已登录但没读课程：开始仍置灰并说明原因",
              not pg.btn_start.isEnabled() and "读取课程" in pg.btn_start.toolTip(),
              pg.btn_start.toolTip())

        pg.set_state("running")
        check("running：配置类控件禁用（课程勾选/并发/域名）",
              not pg.cb_workers.isEnabled() and not pg.ed_domain.isEnabled())
        check("running：停止可用", pg.btn_stop.isEnabled())
        pg.set_state("logging_in")
        check("logging_in：取消按钮可见、登录按钮禁用",
              pg.btn_cancel.isVisibleTo(pg) and not pg.btn_login.isEnabled())
        pg.set_state("ready")

        print("\n[8] 课程勾选与进度回调")
        acc0 = pg._current
        pg.fill_courses([
            {"course_name": "线性代数", "classroom_id": "1", "course_sign": "s1",
             "course_id": "c1", "sku_id": "k1"},
            {"course_name": "大学物理", "classroom_id": "2", "course_sign": "s2",
             "course_id": "c2", "sku_id": "k2"},
        ], acc0)
        check("课程行已建立", len(pg._boxes) == 2, str(len(pg._boxes)))
        check("默认全选", len(pg._selected(acc0)) == 2)
        pg._select_all(False)
        check("全不选生效", len(pg._selected(acc0)) == 0)
        check("计数文案同步", "已选 0 / 2 门" in pg.lb_count.text(),
              pg.lb_count.text())
        pg._select_all(True)
        check("全选生效", len(pg._selected(acc0)) == 2)

        pg.on_progress({"course": "线性代数", "video": "1.1 行列式.mp4", "rate": 0.42,
                        "course_index": 1, "course_total": 2,
                        "videos_done": 1, "videos_total": 5})
        check("进度条比例 = rate", abs(pg.bar.value() - 0.42) < 1e-6,
              str(pg.bar.value()))
        check("当前课程/视频显示", "线性代数" in pg.lb_now.text()
              and "1.1 行列式.mp4" in pg.lb_now.text(), pg.lb_now.text())
        check("统计文案带百分比与视频计数",
              "42.0%" in pg.lb_stats.text() and "1 / 5" in pg.lb_stats.text(),
              pg.lb_stats.text())

        print("\n[9] AccountBar 接线：新增 / 切换 / 运行圆点")
        bar = pg.account_bar
        n0 = len(pg._store.accounts)
        bar.sig_add.emit()
        app.processEvents()
        check("sig_add：新增账号并切过去",
              len(pg._store.accounts) == n0 + 1
              and pg._current == pg._store.active)
        check("chip 数量跟账号数一致",
              len(getattr(bar, "_buttons", {})) == n0 + 1,
              str(len(getattr(bar, "_buttons", {}))))
        new_id = pg._current
        box_new = getattr(bar, "_buttons", {}).get(new_id)
        bar.set_running({new_id})
        check("set_running：busy 属性 + ● 前缀",
              box_new is not None and box_new.property("busy") == "true"
              and box_new.text().startswith("●"),
              box_new.text() if box_new else "no chip")
        bar.set_running(set())
        check("set_running 清空后恢复",
              box_new.property("busy") == "false"
              and not box_new.text().startswith("●"))

        other = [a["id"] for a in pg._store.accounts if a["id"] != new_id][0]
        bar.sig_switch.emit(other)
        app.processEvents()
        check("sig_switch：页面当前账号跟随",
              pg._current == other and pg._store.active == other)
        check("切换后 chip 选中态跟随", bar.current() == other, bar.current())
        pg._on_add()
        app.processEvents()
        check("新增后当前账号是新账号",
              pg._current == pg._store.active and len(pg._store.accounts) == n0 + 2)

        print("\n[10] 登录分步骤 + 失败显性")
        acc = pg._current
        runner = pg.runner
        steps: list = []
        done: list = []
        runner.sig_login_step.connect(lambda a, t: steps.append(t))
        runner.sig_done.connect(lambda s, m: done.append((s, m)))

        runner.sig_login_step.emit(acc, "2/4 已连接，等待扫码")
        check("步骤文案能到界面", "2/4 已连接，等待扫码" in pg.lb_step.text(),
              pg.lb_step.text())

        import api.yuketang.client as ytc
        login_orig = ytc.YuketangClient.login_by_qrcode
        ident_orig = ytc.YuketangClient.fetch_identity

        png = ytc.render_qrcode_png("https://xuetangx.com/login/ticket-test")

        def fake_login_ok(domain, on_qrcode=None, should_stop=None, timeout=300):
            if on_qrcode:
                on_qrcode(png)
            return {"cookie": "csrftoken=x; sessionid=y", "user_id": "42",
                    "university_id": "3325", "realname": "测试同学"}

        steps.clear()
        done.clear()
        ytc.YuketangClient.login_by_qrcode = staticmethod(fake_login_ok)
        try:
            runner.login("buu.yuketang.cn")
            wait_idle(app, [runner])
        finally:
            ytc.YuketangClient.login_by_qrcode = login_orig
        check("扫码登录四步都上报",
              any(t.startswith("1/4") for t in steps)
              and any(t.startswith("2/4") for t in steps)
              and any(t.startswith("3/4") for t in steps)
              and any(t.startswith("4/4") for t in steps), " | ".join(steps))
        check("登录成功：phase=ready 且写档案",
              runner.phase == "ready"
              and "sessionid=y" in runner._account.get("cookie", ""),
              str(runner._account.get("cookie")))
        check("步骤 4/4 进界面", "4/4" in pg.lb_step.text(), pg.lb_step.text())
        check("二维码真的画进了标签（合法 PNG）",
              pg.lb_qr.pixmap() is not None and not pg.lb_qr.pixmap().isNull()
              and pg.qr_box.isVisibleTo(pg))

        def fake_login_fail(domain, on_qrcode=None, should_stop=None, timeout=300):
            raise ytc.YuketangError("登录校验失败：HTTP 403 <html>login</html>")

        done.clear()
        dialog_state["warning"].clear()
        ytc.YuketangClient.login_by_qrcode = staticmethod(fake_login_fail)
        try:
            runner.login("buu.yuketang.cn")
            wait_idle(app, [runner])
        finally:
            ytc.YuketangClient.login_by_qrcode = login_orig
        check("登录失败：sig_done(error, 完整原因)",
              any(s == "error" and "403" in m for s, m in done), str(done))
        check("登录失败：QMessageBox.warning 弹出原文",
              bool(dialog_state["warning"])
              and "403" in dialog_state["warning"][-1][1],
              str(dialog_state["warning"][-1:]))
        check("登录失败：phase 回 idle", runner.phase == "idle", runner.phase)

        print("\n[11] cookie 兜底登录（成功 / 失败）")
        steps.clear()
        ytc.YuketangClient.fetch_identity = lambda self: {
            "user_id": "88", "realname": "Cookie同学", "university_id": "3325"}
        try:
            runner.login_with_cookie(acc, "csrftoken=c1; sessionid=c2",
                                     "ustc.yuketang.cn")
            wait_idle(app, [runner])
        finally:
            ytc.YuketangClient.fetch_identity = ident_orig
        saved = pg._store.get(acc)
        check("cookie 登录成功写档案",
              saved.get("user_id") == "88"
              and "sessionid=c2" in saved.get("cookie", "")
              and saved.get("realname") == "Cookie同学"
              and saved.get("domain") == "ustc.yuketang.cn", str(saved))
        check("cookie 登录成功：phase=ready", runner.phase == "ready", runner.phase)
        check("cookie 登录成功有步骤文案",
              any("1/3" in t for t in steps) and any("3/3" in t for t in steps),
              " | ".join(steps))

        def boom(self):
            raise ytc.YuketangError("cookie 无效：HTTP 403 <html>login</html>")

        done.clear()
        dialog_state["warning"].clear()
        ytc.YuketangClient.fetch_identity = boom
        try:
            runner.login_with_cookie(acc, "badcookie=1", "ustc.yuketang.cn")
            wait_idle(app, [runner])
        finally:
            ytc.YuketangClient.fetch_identity = ident_orig
        check("cookie 登录失败：sig_done(error, 原文)",
              any(s == "error" and "403" in m for s, m in done), str(done))
        check("cookie 登录失败：弹窗 + phase 回 idle",
              bool(dialog_state["warning"])
              and "403" in dialog_state["warning"][-1][1]
              and runner.phase == "idle")

        print("\n[12] 日志双写（sig_log + loguru）")
        tagged = [c for c in LOG_CALLS
                  if len(c) >= 4 and c[1] == ye.LOG_TAG and c[2]]
        check("_log 双写 loguru 且带 [雨课堂/<acc>] 前缀",
              bool(tagged), str(tagged[:2]))
        check("ERROR 级别走 logger.error",
              any(c[0] == "error" for c in LOG_CALLS),
              str([c[0] for c in LOG_CALLS][:5]))

        # ============================================================ [13] 并行
        print("\n[13] 多账号并行刷课")
        pg._on_add()
        app.processEvents()
        ids = [a["id"] for a in pg._store.accounts]
        a_id, b_id = ids[0], ids[1]
        for acc_id, tag in ((a_id, "A"), (b_id, "B")):
            pg._store.update(acc_id, cookie="csrftoken=t; sessionid=" + tag)
            pg._courses[acc_id] = [{"course_name": tag + "课程", "classroom_id": 1}]
            pg._selected_cache[acc_id] = list(pg._courses[acc_id])
            r = pg._runner_for(acc_id)
            r.reload_account()
            r._ensure_client = (lambda tag=tag: _FakeClient(tag))
        for acc_id in ids[2:]:
            pg._courses.pop(acc_id, None)
            pg._selected_cache.pop(acc_id, None)
        pg._progress.pop(a_id, None)
        pg._progress.pop(b_id, None)
        pg._refresh_start_all()
        check("全部开始：可启动集合非空", pg._any_startable())
        check("全部开始按钮可用并给了 tooltip",
              pg.btn_start_all.isEnabled() and bool(pg.btn_start_all.toolTip()),
              pg.btn_start_all.toolTip())

        pg._on_start_all()
        wait_idle(app, [pg._runner_for(a_id), pg._runner_for(b_id)])
        pa_prog = pg._progress.get(a_id) or {}
        pb_prog = pg._progress.get(b_id) or {}
        check("账号 A 有进度回调", pa_prog.get("course") == "A课程", str(pa_prog))
        check("账号 B 有进度回调", pb_prog.get("course") == "B课程", str(pb_prog))
        check("两个账号都跑完（phase=ready）",
              pg._runner_for(a_id).phase == "ready"
              and pg._runner_for(b_id).phase == "ready")
        pg.inspector.setCurrentIndex(0)
        app.processEvents()
        check("进度摘要两行且多账号时可见",
              len(pg._row_labels) >= 2 and pg.summary_box.isVisibleTo(pg))
        log_text = pg.log.toPlainText()
        name_a = pg._store.display_name(a_id)
        name_b = pg._store.display_name(b_id)
        check("日志行首带 [账号名] 前缀",
              ("[" + name_a + "]") in log_text and ("[" + name_b + "]") in log_text,
              name_a + " / " + name_b)

        print("\n[14] 全部停止")
        ra_run = pg._runner_for(a_id)
        rb_run = pg._runner_for(b_id)
        stops: list = []
        ra_run.stop = lambda: stops.append("a")
        rb_run.stop = lambda: stops.append("b")
        pg._phases[a_id] = "running"
        pg._phases[b_id] = "running"
        pg._refresh_running()
        check("AccountBar 知道两个账号在跑",
              pg._running_ids() == {a_id, b_id}, str(pg._running_ids()))
        busy_chips = sum(1 for b in getattr(bar, "_buttons", {}).values()
                         if b.property("busy") == "true")
        check("两个 chip 都点亮运行圆点", busy_chips == 2, str(busy_chips))
        check("全部停止按钮可用", pg.btn_stop_all.isEnabled())
        pg._on_stop_all()
        check("全部停止向两个 runner 发出停止",
              set(stops) == {"a", "b"}, str(stops))
        del ra_run.stop
        del rb_run.stop
        pg._phases[a_id] = "ready"
        pg._phases[b_id] = "ready"
        pg._refresh_running()

        print("\n[15] 删除账号（二次确认 + drop_runner）")
        before = len(pg._store.accounts)
        victim = pg._store.accounts[-1]["id"]
        dialog_state["question"].clear()
        pg._on_remove(victim)
        app.processEvents()
        check("删除前弹二次确认",
              bool(dialog_state["question"]), str(dialog_state["question"]))
        check("确认后账号被删除",
              len(pg._store.accounts) == before - 1 and pg._store.get(victim) == {})
        check("drop_runner 清掉实例", victim not in ye.all_runners())
        check("chip 数量同步", len(getattr(bar, "_buttons", {})) == before - 1,
              str(len(getattr(bar, "_buttons", {}))))

        print("\n[16] 主窗口收尾不炸 + 测试不留痕")
        win.close()
        app.processEvents()
        check("closeEvent 调用了雨课堂 shutdown（无异常）", True)

        if legacy_backup is None:
            untouched = not os.path.exists(legacy_path)
        else:
            with open(legacy_path, "rb") as f:
                untouched = f.read() == legacy_backup
        check("项目根的 yuketang.json 原样未动", untouched, legacy_path)
        check("测试没有在项目根生成 yuketang_accounts.json",
              root_accounts_existed or not os.path.exists(root_accounts))
    finally:
        ypage.QMessageBox = real_box
        try:
            win.close()
        except Exception:
            pass
        try:
            if ui_backup is None:
                if os.path.exists(ui_path):
                    os.remove(ui_path)
            else:
                with open(ui_path, "wb") as f:
                    f.write(ui_backup)
        except Exception:
            pass
        # Destroy Qt objects while QApplication and test doubles are still alive.
        # Native child-window timers otherwise outlive the test during interpreter exit.
        from PySide6.QtCore import QCoreApplication, QEvent
        win.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
        shutil.rmtree(TMP, ignore_errors=True)

    print("\n" + "=" * 56)
    print("结果：" + str(PASSES) + " 通过 / " + str(len(FAILS)) + " 失败")
    for name in FAILS:
        print("  失败:", name)
    print("=" * 56)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
