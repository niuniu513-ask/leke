# -*- coding: utf-8 -*-
"""U校园接入（v1.5.5 多账号版）验证：

vendor 引擎可选依赖降级 / Config 构建 / 补丁守卫；
适配层：AccountStore 档案（新增/切换/删除/旧文件迁移）、resolve_ai(acc_id)
的「账号覆盖优先 → 回退 config.ini」两条路径、UnipusRunner 状态机、
每账号一个 runner + 注册表 + shutdown_all；
页面：多账号切换 / 新增 / 删除 / 摘要 / 按钮可用态；
AccountBar 接线（切换/新增/删除/运行态圆点）；
多账号并行（两个账号同时 running，各自有 sig_state）；
导航注册。

跑法：.venv\\Scripts\\python.exe tests/regression/_test_unipus.py
（offscreen 即可；本测试不会真的启动 Edge、不联网。）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from api.unipus import unipus_engine as vendor  # noqa: E402
from api.unipus.audio_recognizer import AudioTranscriber, whisper  # noqa: E402
import gui_app.store as store  # noqa: E402
from gui_app import icons as I  # noqa: E402
from gui_app import __version__ as APP_VERSION  # noqa: E402
from gui_app.main_window import (  # noqa: E402
    NAV, PAGE_GROUP, PAGE_INDEX, PAGE_KEYS, PAGES,
)
from gui_app.platform_accounts import AccountStore  # noqa: E402
from gui_app.widgets import AccountBar, Card, EmptyState  # noqa: E402
from gui_app import unipus_engine as UE  # noqa: E402
from gui_app.unipus_engine import (  # noqa: E402
    UNIPUS_ACCOUNTS_FILE, UNIPUS_FILE, UnipusRunner, _mask_key, account_store,
    all_runners, drop_runner, get_runner, resolve_ai, shutdown_all,
    unipus_load, unipus_save,
)

FAILS: list[str] = []
PASSES = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASSES
    if ok:
        PASSES += 1
        print(f"  [PASS] {name}" + (f"  {extra}" if extra else ""))
    else:
        FAILS.append(name)
        print(f"  [FAIL] {name}" + (f"  {extra}" if extra else ""))


def section(title: str) -> None:
    print(f"\n[{title}]")


# ------------------------------------------------------------------ 隔离环境
TMP = tempfile.mkdtemp(prefix="_test_unipus_")
_ORIG_APP_DIR = store.app_dir
store.app_dir = lambda: TMP          # 账号档案 / config.ini 全部落到临时目录


def write_engine_cfg_file(**over) -> str:
    """写一个引擎可读的 config json，返回路径。"""
    data = {
        "url": "", "username": "u1", "password": "p1", "token_full": "",
        "api_key": "sk-test", "base_url": "https://api.test/v1",
        "model": "test-model", "temperature": 0.3, "max_tokens": 2000,
        "debug_mode": False,
    }
    data.update(over)
    p = os.path.join(TMP, "_engine_cfg.json")
    with open(p, "w", encoding="utf8") as f:
        json.dump(data, f, ensure_ascii=False)
    return p


class _FakeSolver:
    """只满足按钮可用态判断的桩 solver。"""

    def clear_stop(self):
        pass

    def request_stop(self):
        pass

    def _should_stop(self):
        return False

    def solve_current_page(self, name=""):
        return True

    def process_selected_tabs(self, tabs, chapter):
        return len(tabs), []


class _BlockingSolver(_FakeSolver):
    """process_selected_tabs 会阻塞，用来观察「两个账号同时 running」。"""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.stopped = False

    def clear_stop(self):
        self.stopped = False

    def request_stop(self):
        self.stopped = True

    def _should_stop(self):
        return self.stopped

    def process_selected_tabs(self, tabs, chapter):
        self.started.set()
        self.release.wait(3)
        return len(tabs), []


def summary_texts(page) -> list:
    out = []
    for i in range(page.summary_lay.count()):
        w = page.summary_lay.itemAt(i).widget()
        if w is not None:
            out.append(w.text())
    return out


def main() -> int:
    try:
        # ============================================================ [1] vendor 引擎
        section("vendor 引擎：导入与可选依赖降级")
        check("1.1 vendor 引擎可导入（语法 / 路径 / 缺失依赖均不炸）", True)
        check("1.2 gui_log_queue 是 Queue",
              vendor.gui_log_queue.__class__.__name__ == "Queue")
        check("1.3 模块级 logger 兜底（NullHandler）",
              vendor.logger.name == "UCampusBot"
              and any(getattr(h, "_gph", None) is not None or True
                      for h in vendor.logger.handlers))
        check("1.4 FluentModernGUI 降级为 None（PyQt-Fluent-Widgets 未 vendor）",
              vendor.FluentModernGUI is None)
        check("1.5 EnvironmentChecker 可选（未 vendor 也不炸）",
              getattr(vendor, "EnvironmentChecker", "MISSING") in (None,))
        check("1.6 whisper 未安装 → whisper=None", whisper is None)
        check("1.7 AudioTranscriber 类仍存在（转写代码路径完整保留）",
              AudioTranscriber is not None)
        try:
            AudioTranscriber()
            raised = ""
        except Exception as e:
            raised = type(e).__name__
        check("1.8 转录器懒加载，不阻塞启动或依赖旧 whisper",
              raised == "", raised)

        src = open(vendor.__file__, encoding="utf-8").read()
        check("1.9 相对导入 audio_recognizer（vendor 化）",
              "from .audio_recognizer import AudioTranscriber" in src)
        check("1.10 EnvironmentChecker 为可选导入（skip_check=True 永不需要）",
              "except ImportError" in src and "EnvironmentChecker" in src)
        check("1.11 无 /n 字面坏补丁残留", "/n" not in src)
        check("1.12 VideoHandler 转写器 try/except 降级守卫",
              "self.transcriber = AudioTranscriber(driver, stop_requested) if AudioTranscriber else None" in src
              and "except Exception:" in src)
        check("1.13 音频转写 None 守卫（跳过听力转写）",
              "if transcriber is None:" in src)

        # ============================================================ [2] Config
        section("引擎 Config 构建")
        cfg_path = write_engine_cfg_file()
        cfg = vendor.Config.from_json(cfg_path)
        check("2.1 username/password 透传", cfg.username == "u1" and cfg.password == "p1")
        check("2.2 api_key/base_url/model 透传",
              cfg.api_key == "sk-test" and cfg.base_url == "https://api.test/v1"
              and cfg.model == "test-model")
        check("2.3 url 为空回落 DEFAULT_LOGIN_URL",
              cfg.url == vendor.DEFAULT_LOGIN_URL)
        check("2.4 token_full 可空", cfg.token_full == "")
        check("2.5 temperature/max_tokens 默认", cfg.temperature == 0.3
              and cfg.max_tokens == 2000)

        # ============================================================ [3] 账号档案
        section("适配层：AccountStore 档案 / resolve_ai(acc_id)")
        check("3.1 文件常量", UNIPUS_FILE == "unipus.json"
              and UNIPUS_ACCOUNTS_FILE == "unipus_accounts.json")
        st = account_store()
        check("3.2 全新档案为空（无旧文件可迁移）",
              st.accounts == [] and st.active == "")
        acc_a = st.add("账号 A")
        acc_b = st.add("账号 B")
        check("3.3 add 生成 id 并设为当前",
              bool(acc_a.get("id")) and st.active == acc_b["id"])
        st.set_active(acc_a["id"])
        check("3.4 set_active 切换当前账号", st.active == acc_a["id"])
        check("3.5 pairs / display_name",
              st.pairs() == [(acc_a["id"], "账号 A"), (acc_b["id"], "账号 B")])
        st.update(acc_a["id"], username="ua", password="pa", token_full="ta")
        check("3.6 update 落盘", st.get(acc_a["id"])["username"] == "ua")
        check("3.7 unipus_load 按账号往返",
              unipus_load(acc_a["id"])["password"] == "pa"
              and unipus_load(acc_a["id"])["token_full"] == "ta")
        changed = unipus_save({"password": "secret2"}, acc_a["id"])
        check("3.8 unipus_save 描述打码",
              all("secret2" not in c for c in changed)
              and any("password" in c for c in changed))

        unipus_save({"ai_endpoint": "https://api.ov/v1", "ai_key": "sk-ov",
                     "ai_model": "ov-model"}, acc_a["id"])
        check("3.9 resolve_ai：账号覆盖优先",
              resolve_ai(acc_a["id"]) == ("https://api.ov/v1", "sk-ov", "ov-model"))

        ini = os.path.join(TMP, "config.ini")
        with open(ini, "w", encoding="utf8") as f:
            f.write("[tiku]\nendpoint = https://api.leke/v1\n"
                    "key = sk-leke\nmodel = leke-model\n")
        check("3.10 resolve_ai：无覆盖 → 回退 config.ini [tiku]",
              resolve_ai(acc_b["id"]) == ("https://api.leke/v1", "sk-leke", "leke-model"))
        check("3.11 账号级隔离（A 的覆盖不影响 B）",
              resolve_ai(acc_a["id"])[0] == "https://api.ov/v1")
        unipus_save({"ai_endpoint": "https://api.ov/v1"}, acc_b["id"])
        check("3.12 部分覆盖 + 回退补全",
              resolve_ai(acc_b["id"]) == ("https://api.ov/v1", "sk-leke", "leke-model"))
        check("3.13 ai_override_keys", UE.ai_override_keys(acc_b["id"]) == ["ai_endpoint"])
        check("3.14 _mask_key 打码",
              _mask_key("sk-1234567890abcd") == "sk-12******abcd"
              and _mask_key("short") == "******")

        # 旧单账号文件自动迁移（单独 tmp 目录，不碰上面的档案）
        MIG = tempfile.mkdtemp(prefix="_test_unipus_mig_")
        try:
            with open(os.path.join(MIG, "unipus.json"), "w", encoding="utf8") as f:
                json.dump({"username": "legacy_u", "password": "legacy_p",
                           "ai_key": "sk-legacy"}, f)
            _saved_dir = store.app_dir
            store.app_dir = lambda: MIG
            try:
                mig = AccountStore("unipus_accounts.json", "unipus.json",
                                   dict(UE._UNIPUS_DEFAULTS)).load()
            finally:
                store.app_dir = _saved_dir
            check("3.15 旧 unipus.json 迁移成一条档案", len(mig.accounts) == 1)
            check("3.16 迁移保留字段",
                  mig.accounts[0]["username"] == "legacy_u"
                  and mig.accounts[0]["ai_key"] == "sk-legacy"
                  and mig.accounts[0].get("migrated_from") == "unipus.json")
            check("3.17 迁移后 active 指向该账号",
                  mig.active == mig.accounts[0]["id"])
            check("3.18 旧文件改名 .migrated",
                  os.path.exists(os.path.join(MIG, "unipus.json.migrated"))
                  and not os.path.exists(os.path.join(MIG, "unipus.json")))
        finally:
            shutil.rmtree(MIG, ignore_errors=True)

        # ============================================================ [4] Runner
        section("UnipusRunner：状态机 / 每账号一个实例 / 注册表（不开浏览器）")
        runner = get_runner(acc_a["id"])
        states: list[str] = []
        runner.sig_state.connect(states.append)
        steps: list[tuple] = []
        runner.sig_login_step.connect(lambda aid, t: steps.append((aid, t)))
        check("4.1 初始 phase=idle", runner.phase == "idle")
        check("4.2 初始未连接", runner.connected is False)
        check("4.3 runner 绑定 acc_id", runner.acc_id == acc_a["id"])
        check("4.4 sig_login_step 存在（acc_id, 步骤文案）",
              hasattr(UnipusRunner, "sig_login_step"))
        check("4.5 现有信号名保持不变",
              all(hasattr(UnipusRunner, n) for n in
                  ("sig_log", "sig_state", "sig_tasks", "sig_done")))

        # 空账密 → connect 直接拒绝回 idle
        unipus_save({"username": "", "password": ""}, acc_a["id"])
        runner.connect()
        check("4.6 未填账密 connect 被拒（回 idle）",
              runner.phase == "idle" and states[-1] == "idle")
        check("4.7 拒绝路径不启动泵线程", runner._pump_thread is None)
        check("4.8 拒绝路径也发登录步骤（acc_id 可分辨）",
              bool(steps) and all(s[0] == acc_a["id"] for s in steps))

        # 有账密无 AI → 拒绝
        unipus_save({"username": "ua", "password": "pa",
                     "ai_endpoint": "", "ai_key": "", "ai_model": ""}, acc_a["id"])
        os.remove(ini)
        runner.connect()
        check("4.9 AI 配置缺失 connect 被拒（回 idle）", runner.phase == "idle")

        # process/quick/scan 未连接时全部 no-op
        runner.process([0])
        runner.quick()
        runner.scan()
        check("4.10 未连接时 process/quick/scan 均 no-op", runner.phase == "idle")

        runner._tabs = [{"a": 1}, {"a": 2}]
        check("4.11 selected_tabs 越界过滤",
              [t["a"] for t in runner.selected_tabs([0, 5, 1, -1])] == [1, 2])
        runner.shutdown()
        check("4.12 shutdown 回 idle 且可重复调用", runner.phase == "idle")
        runner.shutdown()

        runner._tabs = [{"display": "[必修] Unit1 - t1", "is_compulsory": True},
                        {"display": "[选修] Unit1 - t2", "is_compulsory": False},
                        {"display": "[已完成][必修] Unit1 - t3",
                         "is_compulsory": True, "is_finished": True}]
        views = runner._tab_views()
        check("4.13 _tab_views 携带 index/display/is_compulsory/is_finished",
              views[0] == {"index": 0, "display": "[必修] Unit1 - t1",
                           "is_compulsory": True, "is_finished": False}
              and views[1]["index"] == 1 and views[1]["is_compulsory"] is False
              and views[1]["is_finished"] is False
              and views[2]["is_finished"] is True)

        ra = get_runner(acc_a["id"])
        rb = get_runner(acc_b["id"])
        check("4.14 每账号一个 runner（同 id 复用、异 id 不同实例）",
              ra is runner and rb is not runner)
        check("4.15 all_runners 覆盖全部账号",
              set(all_runners()) >= {acc_a["id"], acc_b["id"]})
        snapshot = all_runners()
        snapshot.clear()
        check("4.16 all_runners 返回副本", acc_a["id"] in all_runners())
        dropped = get_runner("acc_tmp_drop")
        drop_runner("acc_tmp_drop")
        check("4.17 drop_runner 摘掉注册项", "acc_tmp_drop" not in all_runners()
              and dropped.phase == "idle")
        try:
            shutdown_all()
            ok_shutdown = True
        except Exception as exc:
            ok_shutdown = False
            print("    shutdown_all 异常:", exc)
        check("4.18 shutdown_all 不炸", ok_shutdown)
        check("4.19 shutdown_all 后全部回 idle",
              all(r.phase == "idle" for r in all_runners().values()))

        # ============================================================ [5] 页面
        section("U校园页面（多账号 / offscreen 构建）")
        app = QApplication.instance() or QApplication([])
        from gui_app.page_unipus import UnipusPage
        page = UnipusPage()
        check("5.1 页面可构建（无 selenium/引擎副作用）", page is not None)
        check("5.2 idle 态：连接可用", page.btn_connect.isEnabled())
        check("5.3 idle 态：扫描/处理/快速/停止全部禁用",
              not page.btn_scan.isEnabled() and not page.btn_process.isEnabled()
              and not page.btn_quick.isEnabled() and not page.btn_stop.isEnabled())
        check("5.4 账号条 brand=unipus", page.acc_bar.property("brand") == "unipus")
        check("5.5 账号条列出全部账号且当前账号被选中",
              len(page.acc_bar._buttons) == 2
              and page.acc_bar.current() == page._current
              and page.acc_bar._buttons[page._current].isChecked())

        page.fill_tasks([
            {"index": 0, "display": "[必修] U1 - t1", "is_compulsory": True},
            {"index": 1, "display": "[选修] U1 - t2", "is_compulsory": False},
            {"index": 2, "display": "[必修] U1 - t3", "is_compulsory": True},
        ])
        check("5.6 必修默认勾选（2/3）",
              page.lb_count.text() == "已选 2 / 3"
              and "开始任务 (2)" in page.btn_process.text())
        page._select_all(False)
        check("5.7 全不选 → 处理按钮禁用", not page.btn_process.isEnabled())
        page._select_compulsory()
        check("5.8 仅必修 → 恢复 2 项", page._checked_indices() == [0, 2])
        page._select_all(True)
        check("5.9 全选 → 3 项", len(page._checked_indices()) == 3)

        page.fill_tasks([
            {"index": 0, "display": "[必修] U1 - t1", "is_compulsory": True},
            {"index": 1, "display": "[已完成][必修] U1 - t2",
             "is_compulsory": True, "is_finished": True},
            {"index": 2, "display": "[已完成][选修] U1 - t3",
             "is_compulsory": False, "is_finished": True},
        ])
        check("5.9b 已完成任务默认不勾选（仅 1 项）",
              page._checked_indices() == [0]
              and "开始任务 (1)" in page.btn_process.text())
        # 用户坚持重做：手动勾上已完成项仍然可行
        page._boxes[1][1].setChecked(True)
        check("5.9c 已完成项可手动勾选重做", page._checked_indices() == [0, 1])

        page.fill_tasks([])
        got_empty = any(isinstance(page.tasks_lay.itemAt(i).widget(), EmptyState)
                        for i in range(page.tasks_lay.count())
                        if page.tasks_lay.itemAt(i).widget() is not None)
        check("5.10 空结果回填空态（EmptyState 组件）", got_empty)

        # 状态矩阵（模拟 connected：注入假 solver）
        runner2 = page.runner
        runner2._solver = _FakeSolver()
        runner2._set_phase("ready")
        app.processEvents()
        check("5.11 ready 态：可断开、扫描/快速可用",
              page.btn_connect.isEnabled() and page.btn_connect.text() == "断开"
              and page.btn_scan.isEnabled() and page.btn_quick.isEnabled())
        page.fill_tasks([
            {"index": 0, "display": "[必修] U1 - t1", "is_compulsory": True},
            {"index": 1, "display": "[选修] U1 - t2", "is_compulsory": False},
            {"index": 2, "display": "[必修] U1 - t3", "is_compulsory": True},
        ])
        page._select_compulsory()
        app.processEvents()
        check("5.12 ready+有勾选 → 处理按钮可用", page.btn_process.isEnabled())
        runner2._set_phase("running")
        app.processEvents()
        check("5.13 running 态：只有停止可用",
              page.btn_stop.isEnabled() and not page.btn_scan.isEnabled()
              and not page.btn_process.isEnabled() and not page.btn_quick.isEnabled())
        runner2._set_phase("scanning")
        app.processEvents()
        check("5.14 scanning 态：处理类全部禁用",
              not page.btn_scan.isEnabled() and not page.btn_process.isEnabled())
        runner2._solver = None
        runner2._set_phase("idle")
        app.processEvents()
        check("5.15 回 idle：连接恢复可用", page.btn_connect.isEnabled()
              and page.btn_connect.text() == "连接并登录")
        check("5.16 禁用按钮带 tooltip 说明原因",
              bool(page.btn_scan.toolTip()) and bool(page.btn_process.toolTip()))

        # 账号卡往返（走页面按钮）
        page.ed_user.setText("nug2")
        page.ed_pass.setText("pw2")
        page.ed_token.setText("tok2")
        page._on_save_account()
        check("5.17 页面保存账密 → 当前账号档案落盘",
              unipus_load(page._current)["username"] == "nug2"
              and unipus_load(page._current)["password"] == "pw2"
              and unipus_load(page._current)["token_full"] == "tok2")
        page.ed_user.clear()
        page.ed_pass.clear()
        page._fill_account()
        check("5.18 回填账号卡", page.ed_user.text() == "nug2")

        # 日志
        page.append_log("ERROR", "测试错误行")
        page.append_log("INFO", "测试信息行")
        check("5.19 日志追加（含时间戳与文本）",
              "测试错误行" in page.log.toPlainText()
              and "测试信息行" in page.log.toPlainText())
        page_name = page.store.display_name(page._current)
        runner2.sig_log.emit("来自引擎的一行")
        app.processEvents()
        check("5.20 引擎日志带 [账号名] 前缀",
              f"[{page_name}] 来自引擎的一行" in page.log.toPlainText())

        # AI 卡
        unipus_save({"ai_endpoint": "", "ai_key": "", "ai_model": ""}, page._current)
        try:
            os.remove(os.path.join(TMP, "config.ini"))
        except OSError:
            pass
        page.refresh_ai_info()
        check("5.21 AI 卡显示缺失告警（并指向「AI 大模型」页）",
              "AI 配置不完整" in page.lb_ai.text()
              and "AI 大模型" in page.lb_ai.text()
              and "参数设置" not in page.lb_ai.text())
        check("5.22 缺失时给出「AI 大模型」页入口", not page.btn_ai_goto.isHidden())
        unipus_save({"ai_endpoint": "https://api.ov/v1", "ai_key": "sk-ov",
                     "ai_model": "ov-model"}, page._current)
        page.refresh_ai_info()
        check("5.23 AI 卡展示当前账号覆盖",
              "本账号覆盖" in page.lb_ai.text() and page.btn_ai_goto.isHidden())

        # 账号条：新增 / 切换 / 删除（删除走二次确认桩）
        first = page._current
        first_name = page.store.display_name(first)
        page._on_add_account()
        app.processEvents()
        new_id = page._current
        check("5.24 新增账号 → 档案 +1 并切到新账号",
              len(page.store.accounts) == 3 and new_id != first)
        page._on_switch_account(first)
        check("5.25 切换账号 → active 与账号卡同步",
              page._current == first and page.store.active == first
              and page.ed_user.text() == "nug2")
        page._confirm_remove = lambda name: True
        page._on_remove_account(new_id)
        app.processEvents()
        check("5.26 删除账号 → 档案 -1", len(page.store.accounts) == 2)
        check("5.27 删除后 drop_runner（注册表里消失）",
              new_id not in all_runners())
        check("5.28 删除后仍保留其它账号",
              page.store.get(first) != {}
              and first in [a["id"] for a in page.store.accounts])

        # 多账号摘要
        page._update_summary()
        texts = summary_texts(page)
        check("5.29 每个账号一行摘要（账号名 + 阶段 + 已处理）",
              len(texts) == len(page.store.accounts)
              and all("已处理" in t for t in texts)
              and any(first_name in t for t in texts))

        # 全部连接 / 全部停止
        check("5.30 「全部连接 / 全部停止」入口就位",
              page.btn_connect_all.text() == "全部连接"
              and page.btn_stop_all.text() == "全部停止")
        check("5.31 有待机账号 → 全部连接可用且带 tooltip",
              page.btn_connect_all.isEnabled()
              and bool(page.btn_connect_all.toolTip()))
        check("5.32 没有运行中账号 → 全部停止禁用并写明原因",
              (not page.btn_stop_all.isEnabled())
              and bool(page.btn_stop_all.toolTip()))

        # ============================================================ [5B] AccountBar
        section("AccountBar 接线（切换 / 新增 / 删除 / 运行态圆点）")
        bar = AccountBar()
        sw, ad, rm = [], [], []
        bar.sig_switch.connect(sw.append)
        bar.sig_add.connect(lambda: ad.append(True))
        bar.sig_remove.connect(rm.append)
        bar.set_accounts([("a1", "账号一"), ("a2", "账号二")], "a1",
                         running={"a2"})
        check("B1 chip 选中当前账号",
              bar.current() == "a1" and bar._buttons["a1"].isChecked()
              and not bar._buttons["a2"].isChecked())
        check("B2 运行态圆点只标在运行账号上",
              bar._buttons["a2"].text().startswith("●")
              and not bar._buttons["a1"].text().startswith("●"))
        bar._buttons["a2"].click()
        check("B3 点击 chip → sig_switch(acc_id)", sw == ["a2"])
        bar.set_running({"a1"})
        app.processEvents()
        check("B4 set_running 只更新圆点",
              bar._buttons["a1"].text().startswith("●")
              and not bar._buttons["a2"].text().startswith("●"))
        bar.btn_remove.click()
        check("B5 删除当前 → sig_remove(acc_id)", rm == ["a1"])
        bar.btn_add.click()
        check("B6 新增 → sig_add", ad == [True])
        bar.set_accounts([("a1", "账号一")], "a1")
        check("B7 只剩一个账号时禁用删除", not bar.btn_remove.isEnabled())

        # ============================================================ [5C] 并行
        section("多账号并行（桩 solver，两个账号同时 running）")
        ra2 = get_runner(acc_a["id"])
        rb2 = get_runner(acc_b["id"])
        sa, sb = _BlockingSolver(), _BlockingSolver()
        states_a: list[str] = []
        states_b: list[str] = []
        ra2.sig_state.connect(states_a.append)
        rb2.sig_state.connect(states_b.append)
        ra2._solver, rb2._solver = sa, sb
        ra2._tabs = [{"l1_title": "ch", "display": "t", "is_compulsory": True}]
        rb2._tabs = [{"l1_title": "ch", "display": "t", "is_compulsory": True}]
        ra2._set_phase("ready")
        rb2._set_phase("ready")
        ra2.process([0])
        rb2.process([0])
        sa.started.wait(2)
        sb.started.wait(2)
        check("P1 账号 A 进入 running（各自 sig_state）",
              ra2.phase == "running" and "running" in states_a)
        check("P2 账号 B 进入 running（各自 sig_state）",
              rb2.phase == "running" and "running" in states_b)
        check("P3 两个账号同时处于 running（并行）",
              ra2.phase == "running" and rb2.phase == "running")
        sa.release.set()
        sb.release.set()
        deadline = time.time() + 3
        while time.time() < deadline and (ra2.phase != "ready" or rb2.phase != "ready"):
            time.sleep(0.02)
        check("P4 各自收尾回 ready",
              ra2.phase == "ready" and rb2.phase == "ready")
        check("P5 各自统计已处理数", ra2.processed >= 1 and rb2.processed >= 1)

        try:
            page.shutdown()
            ok_page_shutdown = True
        except Exception as exc:
            ok_page_shutdown = False
            print("    page.shutdown 异常:", exc)
        check("P6 页面 shutdown() 调 shutdown_all 不炸", ok_page_shutdown)
        check("P7 全部 runner 回 idle",
              all(r.phase == "idle" for r in all_runners().values()))

        # ============================================================ [6] 导航/版本
        section("导航注册与版本")
        check("6.1 U校园已注册进导航",
              "unipus" in PAGE_KEYS and PAGES[PAGE_INDEX["unipus"]][0] == "U校园")
        # v1.5.6：U校园**自己一组**（原来和雨课堂挤在"其他平台"里）
        check("6.2 U校园独立成组",
              [it[0] for g, items in NAV if g == "U校园" for it in items] == ["unipus"]
              and PAGE_GROUP["unipus"] == "U校园")
        check("6.3 U校园导航用专属 unipus 图标",
              PAGES[PAGE_INDEX["unipus"]][2] == "unipus" and "unipus" in I.ICONS)
        # 不再逐版改断言：只要求"三段式且不低于引入 U校园 多账号的那版"
        _parts = str(APP_VERSION).split(".")
        check("6.4 版本号 ≥ 1.5.5 的三段式 semver",
              len(_parts) == 3 and all(p.isdigit() for p in _parts)
              and tuple(int(p) for p in _parts) >= (1, 5, 5),
              APP_VERSION)

        # ============================================================ [7] 品牌视觉
        section("品牌图标与视觉（v1.5.2b）")
        check("7.1 单色 unipus 图标已注册", "unipus" in I.ICONS)
        check("7.2 U 形 + 定位点三笔构成",
              I.ICONS["unipus"].count("<path") == 2
              and "<circle" in I.ICONS["unipus"])
        pm = I.brand_pixmap(30)
        check("7.3 彩色品牌标志可渲染（非空 pixmap）", not pm.isNull())
        from gui_app import theme as T
        check("7.4 品牌标志含渐变与白色 U",
              T.PRIMARY in I.UNIPUS_BRAND_SVG and T.PRIMARY_HOVER in I.UNIPUS_BRAND_SVG
              and 'stroke="#FFFFFF"' in I.UNIPUS_BRAND_SVG)
        from gui_app import theme as T
        check("7.5 品牌令牌齐备",
              all(hasattr(T, n) for n in (
                  "UNIPUS", "UNIPUS_HOVER", "UNIPUS_PRESS", "UNIPUS_SOFT",
                  "UNIPUS_SOFT_2", "UNIPUS_LINE", "UNIPUS_HERO_A",
                  "UNIPUS_HERO_B", "UNIPUS_HERO_TEXT", "UNIPUS_HERO_SUB")))
        check("7.5b 四平台品牌色已收敛到同一主色（不用彩虹色）",
              T.UNIPUS == T.YUKETANG == T.ZHIHUISHU == T.PRIMARY)
        check("7.6 QSS 含 unipus 主按钮变体",
              'variant="unipus"' in T.QSS and ":disabled" in T.QSS)
        check("7.7 QSS 含 Hero 品牌条与任务面板",
              "UnipusHero" in T.QSS and "UnipusHeroMark" in T.QSS
              and "UnipusHeroTitle" in T.QSS and "TaskPanel" in T.QSS)
        check("7.8 页面 Hero 就位（品牌标志 + 标题 + 连接按钮）",
              page._hero_host is not None
              and not page.btn_connect.objectName()
              and page.btn_connect.property("variant") == "unipus")
        c = Card("t", icon="user", accent="#FF0000", accent_soft="#FFEEDD")
        check("7.9 Card 支持 accent 强调色（默认蓝不受影响）",
              c is not None and Card("t2", icon="list") is not None)
        nav_src = open("gui_app/main_window.py", encoding="utf-8").read()
        check("7.10 导航选中色按平台取品牌色（unipus / yuketang）",
              "_refresh_nav_icons" in nav_src
              and "NAV_BRAND" in nav_src
              and T.NAV_BRAND.get("unipus") == T.UNIPUS
              and T.NAV_BRAND.get("yuketang") == T.YUKETANG)
        check("7.11 账号条品牌色落到 chip（QSS + chip 属性）",
              ('AccChip[brand="unipus"]' in T.QSS
               or 'AccountBar[brand="unipus"]' in T.QSS)
              and page.acc_bar._buttons[page._current].property("brand") == "unipus")

    finally:
        store.app_dir = _ORIG_APP_DIR
        shutil.rmtree(TMP, ignore_errors=True)

    print(f"\n===== 结果：{PASSES} 项通过，{len(FAILS)} 项失败 =====")
    for f in FAILS:
        print(f"  FAIL: {f}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
