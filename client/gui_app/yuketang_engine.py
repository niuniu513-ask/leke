# -*- coding: utf-8 -*-
"""雨课堂（yuketang / 学堂在线）适配层。

职责边界
--------
- 上游引擎在 api/yuketang/（vendor，见同目录 NOTICE.md）；
- 本模块是引擎和界面之间的**唯一桥梁**：账号读写、后台线程、信号回抛；
- page_yuketang.py 只画界面，不直接碰引擎。

多账号（每账号一个 runner）
--------------------------
v1.5.5 起由「模块级单例 runner + 单份 yuketang.json」升级为
「每账号一个 runner + AccountStore 档案」：

* 账号档案 yuketang_accounts.json（gui_app.platform_accounts.AccountStore）；
* 旧 yuketang.json 在档案为空时自动迁移成第一条记录（并改名 .migrated）；
* get_runner(acc_id) 按账号取 runner，每个 runner 自带
  acc_id / 自己的 YuketangClient / 自己的 stop、cancel_login 事件 / 自己的 phase，
  因此多个账号可以真正并行刷课。

三条设计约束（沿用 U校园适配层踩出来的经验）
--------------------------------------------
1. **引擎延迟导入**：api.yuketang 依赖 websocket / Pillow。本模块在
   import 期绝不触碰它，缺依赖时只是"扫码登录用不了"，程序照常启动。
2. **不跨线程传对象**：信号里只走 dict / str / bytes，不传 session、不传控件。
3. **停止靠 threading.Event**：引擎的 should_stop 回调直接读它，
   GUI 线程只置位，不做任何阻塞操作。

日志（v1.5.5 双写）
-------------------
_log(level, text) 除了发 sig_log 给页面日志框，还写 loguru，
内容进 exe 旁的 leke.log —— 以前只进日志框，真机出问题没有事后证据。
api.logger **延迟导入**（函数内），因为启动时 stream.install()
必须先于它，顺序不能动。统一前缀 [雨课堂/<acc_id>] 方便 grep。
"""
from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import QObject, Signal

from gui_app import store
from gui_app.platform_accounts import AccountStore, mask, safe_suffix

#: 旧单账号文件名（迁移来源；保留常量名，外部引用不失效）
YUKETANG_FILE = "yuketang.json"

#: 多账号档案文件名
ACCOUNTS_FILE = "yuketang_accounts.json"

#: 学校入口由扫码识别或用户填写，不提供某所学校的默认值。
DEFAULT_DOMAIN = ""

#: 心跳里每拍推进的播放秒数（上游默认 12）
DEFAULT_LEARNING_RATE = 12

#: 雨课堂题型 -> 通用题库链的题型名（api/base.py 的 q["type"] 契约）
_YUKETANG_TYPE_MAP = {
    "single": "single",
    "multiple": "multiple",
    "fill": "completion",
    "judge": "judgement",
    "unknown": "single",
}

#: 每条账号记录的字段与默认值（AccountStore 的 defaults）
ACCOUNT_DEFAULTS = {
    "login_mode": "professional",
    "domain": DEFAULT_DOMAIN,
    "cookie": "",
    "user_id": "",
    "university_id": "",
    "realname": "",
    "learning_rate": DEFAULT_LEARNING_RATE,
}

#: loguru 统一前缀模板（第一条参数是 acc_id，第二条是正文）
#: loguru 用的是 `{}` 花括号占位符，**不是** %s ——
#: 写成 %s 的话整条模板会被当字面量记进 leke.log，acc_id 和正文全丢（实测踩过）。
LOG_TAG = "[雨课堂/{}] {}"

#: 进程内账号档案（懒加载）
_STORE = None
_STORE_LOCK = threading.Lock()


# ------------------------------------------------------------------ 账号档案
def account_store() -> AccountStore:
    """进程内唯一的雨课堂账号档案（首次访问时加载 / 迁移，空档案自动建一条）。"""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            loaded = AccountStore(ACCOUNTS_FILE, YUKETANG_FILE, ACCOUNT_DEFAULTS).load()
            # Older releases seeded BUU even before a user chose or logged into a school.
            for account in loaded.accounts:
                if (account.get('domain') == 'buu.yuketang.cn'
                        and not any(account.get(key) for key in
                                    ('cookie', 'user_id', 'university_id', 'realname'))):
                    loaded.update(account['id'], domain='')
            if not loaded.accounts:
                loaded.add("账号 1")
            _STORE = loaded
        return _STORE


def reset_store() -> None:
    """丢弃进程内缓存的档案，下次访问重新从磁盘加载（测试 / 切目录用）。"""
    global _STORE
    with _STORE_LOCK:
        _STORE = None


def accounts_path() -> str:
    return account_store().path()


def yuketang_path() -> str:
    """旧单账号文件路径（迁移成功后会被改名成 .migrated）。"""
    return os.path.join(store.app_dir(), YUKETANG_FILE)


def _account_of(acc_id: str) -> dict:
    """把档案里的某条记录补全成"带全部默认值"的 dict（找不到就给默认值）。"""
    data = dict(ACCOUNT_DEFAULTS)
    acc = account_store().get(str(acc_id)) if acc_id else {}
    if acc:
        data.update(acc)
    return data


# ------------------------------------------------------------------ 旧接口兼容
def yuketang_load() -> dict:
    """当前账号的字段（兼容旧单账号调用）。"""
    return _account_of(account_store().active)


def yuketang_save(patch: dict) -> list:
    """把 patch 合并进当前账号并落盘，返回变更描述（cookie 打码）。"""
    book = account_store()
    acc_id = book.active
    if not acc_id:
        acc_id = book.add("账号 1")["id"]
    book.update(acc_id, **(patch or {}))
    out = []
    for key, value in (patch or {}).items():
        shown = "******" if key in ("cookie", "password") else value
        out.append(str(key) + " = " + str(shown))
    return out


def yuketang_clear_account() -> None:
    """清空当前账号的登录态（cookie / user_id / realname / university_id）。"""
    book = account_store()
    if book.active:
        book.update(book.active, cookie="", user_id="", university_id="", realname="")


def masked_cookie(cookie: str) -> str:
    """cookie 打码（复用共享件的 mask）。"""
    return mask(cookie)


def qrcode_available() -> bool:
    """二维码渲染是否可用（命中 vendor 副本也算可用）。"""
    try:
        from api.yuketang import client as _c
        return bool(_c.qrcode_available())
    except Exception:
        return False


def _builtin_answer(problem: dict):
    """从题目里抠出接口自带的答案（雨课堂允许查看答案时用户对象里就有）。

    优先 user.answer（选择 / 判断），再退 user.answers（填空字典）。
    题目原文（含 user）在 parse_problem 之前是完整的，所以这里直接用 raw。
    """
    user = problem.get("user") if isinstance(problem, dict) else None
    if not isinstance(user, dict):
        return None
    if user.get("is_show_answer") is False:
        # 明确标记"不展示答案"时别用它的占位值
        pass
    for key in ("answer", "answers"):
        value = user.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _problem_to_query(problem: dict) -> dict:
    """把解析后的题目转成统一题库链认识的 q_info（对齐 api/base.py 契约）。

    q_info = {"id", "title", "options": "A. xx\\nB. yy", "type": single/...}。
    """
    body = str(problem.get("body") or problem.get("body_html") or "").strip()
    if not body:
        return {}
    options = problem.get("options") or []
    lines = []
    for opt in options:
        if not isinstance(opt, dict):
            continue
        key = str(opt.get("key") or "").strip()
        value = str(opt.get("value") or "").strip()
        if key and value:
            lines.append(key + ". " + value)
        elif value:
            lines.append(value)
    q_type = _YUKETANG_TYPE_MAP.get(str(problem.get("type") or ""), "single")
    return {
        "id": problem.get("problem_id"),
        "title": body,
        "options": "\n".join(lines),
        "type": q_type,
    }


# ------------------------------------------------------------------ runner
class YuketangRunner(QObject):
    """雨课堂引擎 runner：**一个账号一个实例**，阻塞调用全部搬进后台线程。

    phase 状态机::

        idle -> logging_in -> ready -> loading -> loaded
                              ↘ running -> ready

    任何失败都回 idle（并带错误文案）。

    信号名与单账号时代保持一致，另外新增 sig_login_step（acc_id, 步骤文案）。
    """

    sig_log = Signal(str, str)          # level, text
    sig_state = Signal(str)             # phase 变化
    sig_qrcode = Signal(bytes)          # 扫码登录二维码（PNG 字节）
    sig_account = Signal(dict)          # 账号信息（不含 cookie 原文）
    sig_courses = Signal(list)          # 课程列表
    sig_progress = Signal(dict)         # 刷课进度
    sig_done = Signal(str, str)         # status(ok/stopped/error), message
    sig_login_step = Signal(str, str)   # acc_id, 登录步骤文案
    sig_browser = Signal(str, int)
    sig_browser_closed = Signal(str)

    def __init__(self, acc_id: str = "", parent=None):
        super().__init__(parent)
        self.acc_id = str(acc_id or "")
        self._browser_driver = None
        self._phase = "idle"
        self._lock = threading.Lock()
        self._client = None
        self._stop = threading.Event()
        self._cancel_login = threading.Event()
        self._next_course = threading.Event()
        self._thread = None
        self._account = _account_of(self.acc_id)
        self._phase = 'ready' if self.logged_in() else 'idle'

    # -------------------------------------------------------------- 状态
    @property
    def phase(self) -> str:
        return self._phase

    @property
    def account(self) -> dict:
        return dict(self._account)

    def logged_in(self) -> bool:
        return bool(str(self._account.get("cookie") or "").strip())

    def busy(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def reload_account(self) -> dict:
        """重新从档案读取本账号字段（切换账号 / 保存域名 / cookie 登录后用）。"""
        self._account = _account_of(self.acc_id)
        self._drop_client()
        return dict(self._account)

    def configure_school(self, domain: str) -> str:
        """切换学校时丢弃上一学校的会话和身份，账号档案互相独立。"""
        from api.yuketang.client import _normalize_domain, YuketangError
        normalized = _normalize_domain(domain)
        if self.busy():
            raise RuntimeError("请先停止当前任务，再修改学校")
        previous = str(self._account.get('domain') or '').strip()
        if previous:
            try:
                previous = _normalize_domain(previous)
            except YuketangError:
                pass
        fields = {'domain': normalized, 'login_mode': 'professional'}
        if previous != normalized:
            self._close_browser()
            fields.update(cookie='', user_id='', university_id='', realname='')
        account_store().update(self.acc_id, **fields)
        self._account = _account_of(self.acc_id)
        self._drop_client()
        if previous != normalized:
            self._set_phase('idle')
        return normalized

    def _set_phase(self, phase: str):
        self._phase = str(phase)
        self.sig_state.emit(self._phase)

    def _log(self, level: str, text: str):
        """双写：页面日志框 + loguru（leke.log）。api.logger 延迟导入。"""
        level = str(level or "INFO").upper()
        text = str(text)
        self.sig_log.emit(level, text)
        try:
            from api.logger import logger          # 延迟导入，见模块 docstring
            if level in ("ERROR", "CRITICAL"):
                logger.error(LOG_TAG, self.acc_id, text)
            elif level in ("WARNING", "WARN"):
                logger.warning(LOG_TAG, self.acc_id, text)
            elif level == "SUCCESS":
                logger.success(LOG_TAG, self.acc_id, text)
            elif level == "DEBUG":
                logger.debug(LOG_TAG, self.acc_id, text)
            else:
                logger.info(LOG_TAG, self.acc_id, text)
        except Exception:
            pass

    def _step(self, text: str):
        """登录分步骤上报：信号 + 日志一起走。"""
        text = str(text)
        self.sig_login_step.emit(self.acc_id, text)
        self._log("INFO", "登录步骤：" + text)

    # -------------------------------------------------------------- 客户端
    def _make_client(self):
        """按本账号已存字段造一个客户端。缺依赖时抛异常，由调用方兜住。"""
        from api.yuketang import YuketangClient        # 延迟导入（见模块 docstring）

        account = self._account
        if account.get('login_mode') == 'standard':
            raise RuntimeError('普通版官网已登录，请在内置浏览器操作课程；自动任务需要学校专业版登录。')
        domain = str(account.get("domain") or "").strip() or DEFAULT_DOMAIN
        return YuketangClient(
            domain,
            cookie=str(account.get("cookie") or ""),
            user_id=str(account.get("user_id") or ""),
            university_id=str(account.get("university_id") or ""),
            learning_rate=int(account.get("learning_rate") or DEFAULT_LEARNING_RATE),
        )

    def _ensure_client(self):
        if self._client is None:
            self._client = self._make_client()
        return self._client

    def _drop_client(self):
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def _spawn(self, target, kind: str):
        if self.busy():
            self._log("WARNING", "已有雨课堂任务在跑，忽略本次操作")
            return False
        self._stop.clear()
        name = "yuketang-" + str(kind) + "-" + safe_suffix(self.acc_id)
        self._thread = threading.Thread(target=target, name=name, daemon=True)
        self._thread.start()
        return True

    def _store_update(self, fields: dict) -> None:
        """把登录结果写进本账号档案，并刷新内存快照。"""
        if not self.acc_id:
            return
        try:
            account_store().update(self.acc_id, **(fields or {}))
            account_store().mark_logged_in(self.acc_id)
        except Exception as exc:
            self._log("WARNING", "账号写入失败：" + str(exc))
        self._account = _account_of(self.acc_id)

    # -------------------------------------------------------------- 题库链
    def _make_answer_fn(self):
        """造一个 answer_fn(problem_dict) -> 归一化答案，接入统一题库链。

        复用全局的题库配置（[tiku] 段：TikuAdapter → TikuGo → TikuLocal → AI），
        与本项目超星 / U校园用的是同一套 provider，因此雨课堂的题也会自动
        走「官方聚合 → 免费源 → 本地知识库 → AI 兜底」四级回退。

        返回的答案已经过 client 侧题型归一（选择→选项 key，填空→文本）。
        若题库不可用，返回 None，调用方按"无答案"处理。
        """
        tiku = None
        try:
            from gui_app.store import config_path
            from api.answer import Tiku

            tiku = Tiku.get_tiku_from_config(None, config_path=config_path())
            tiku.init_tiku()          # 生产必须显式 init（见 worker.py）
        except Exception as exc:
            self._log("WARNING", "雨课堂答题：题库初始化失败（将只用接口自带答案）：" + str(exc))
            tiku = None

        def answer_fn(problem: dict):
            parsed = dict(problem or {})
            # 1) 接口自带答案（雨课堂允许查看答案时 content.user.answer）
            builtin = _builtin_answer(parsed)
            if builtin not in (None, "", [], {}):
                return builtin            # 2) 题库链（含 AI 兜底）
            if tiku is None:
                return None
            try:
                query = _problem_to_query(parsed)
                if not query:
                    return None
                answers = tiku.query_all([query])
                if answers and answers[0]:
                    return answers[0]
            except Exception as exc:
                self._log("WARNING", "雨课堂答题：题库查询失败：" + str(exc))
            return None

        return answer_fn

    # -------------------------------------------------------------- 答题
    def answer(self, selected: list, options: dict = None):
        """对选中课程做作业 / 章节测验。selected = [课程 dict, ...]。"""
        courses = [c for c in (selected or []) if isinstance(c, dict)]
        if not courses:
            self._log("WARNING", "没有勾选任何雨课堂课程（答题）")
            return
        if self._account.get("login_mode") == "standard":
            self.sig_done.emit("error", "普通版自动答题尚未接通，请在浏览器完成作业。")
            return False
        options = dict(options or {})

        def job():
            self._set_phase("running")
            try:
                client = self._ensure_client()
            except Exception as exc:
                self._log("ERROR", "答题初始化失败：" + str(exc))
                self._set_phase("idle")
                self.sig_done.emit("error", "雨课堂答题初始化失败：" + str(exc))
                return

            answer_fn = self._make_answer_fn()
            total = {"exercises": 0, "done": 0, "skipped": 0, "failed": 0}
            stopped = False
            try:
                for index, course in enumerate(courses):
                    if self._stop.is_set():
                        stopped = True
                        break
                    name = str(course.get("course_name") or course.get("title") or "")
                    self._log("INFO", "[" + str(index + 1) + "/" + str(len(courses))
                              + "] 开始答题：" + name)
                    try:
                        exercises = client.list_exercises(course.get("classroom_id"),
                                                          course.get("course_sign"))
                    except Exception as exc:
                        self._log("ERROR", "[" + name + "] 读取作业列表失败：" + str(exc))
                        continue
                    if not exercises:
                        self._log("INFO", "[" + name + "] 没有作业 / 测验")
                        continue

                    for ex_i, ex in enumerate(exercises):
                        if self._stop.is_set():
                            stopped = True
                            break
                        ex_name = str(ex.get("name") or "")
                        self._log("INFO", "[" + name + "] 作业 " + str(ex_i + 1) + "/"
                                  + str(len(exercises)) + "：" + ex_name)
                        self.sig_progress.emit({
                            "course": name, "video": ex_name, "rate": 0.0,
                            "course_index": index + 1, "course_total": len(courses),
                            "videos_done": total["done"], "videos_total": total["exercises"],
                            "exercise": True,
                        })

                        def on_progress(payload, _name=name, _ex=ex_name):
                            payload = dict(payload or {})
                            self.sig_progress.emit({
                                "course": _name, "video": _ex,
                                "rate": (float(payload.get("index") or 0)
                                         / max(1, float(payload.get("total") or 1))),
                                "course_index": index + 1, "course_total": len(courses),
                                "videos_done": total["done"],
                                "videos_total": total["exercises"],
                                "exercise": True,
                                "problem": payload.get("problem"),
                                "status": payload.get("status"),
                            })

                        try:
                            res = client.solve_exercise(
                                course.get("classroom_id"),
                                ex.get("id"),
                                course.get("sku_id"),
                                answer_fn=answer_fn,
                                on_progress=on_progress,
                                should_stop=self._stop.is_set,
                            )
                        except Exception as exc:
                            self._log("ERROR", "[" + name + "] 作业「" + ex_name
                                      + "」处理失败：" + str(exc))
                            continue
                        total["exercises"] += 1
                        total["done"] += int(res.get("done") or 0)
                        total["skipped"] += int(res.get("skipped") or 0)
                        total["failed"] += int(res.get("failed") or 0)
                        self._log("INFO", "[" + name + "] 「" + ex_name + "」答 " 
                                  + str(res.get("done") or 0) + "，跳过 "
                                  + str(res.get("skipped") or 0) + "，失败 "
                                  + str(res.get("failed") or 0))
            except Exception as exc:
                self._log("ERROR", "答题中断：" + str(exc))
                self._set_phase("ready")
                self.sig_done.emit("error", "雨课堂答题出错：" + str(exc))
                return

            if self._stop.is_set() or stopped:
                self._log("WARNING", "已停止雨课堂答题")
                self._set_phase("ready")
                self.sig_done.emit("stopped", "已停止（已答 " + str(total["done"]) + " 题）")
                return
            self._log("SUCCESS", "雨课堂答题完成：共 " + str(total["exercises"])
                      + " 个作业，答对 " + str(total["done"])
                      + "，跳过 " + str(total["skipped"]) + "，失败 " + str(total["failed"]))
            self._set_phase("ready")
            self.sig_done.emit("ok", "雨课堂答题完成（已答 " + str(total["done"]) + " 题）")

        self._spawn(job, "answer")

    # -------------------------------------------------------------- 扫码登录
    def login_auto(self):
        """从官方浏览器扫码后读取用户所选学校，不要求预填学校。"""
        def job():
            self._set_phase('logging_in')
            self._cancel_login.clear()
            try:
                from api.yuketang.browser_login import login_by_browser
                self._close_browser()
                profile = os.path.join(store.app_dir(), 'rain_profiles', safe_suffix(self.acc_id))
                os.makedirs(profile, exist_ok=True)
                info = login_by_browser(self._cancel_login.is_set, self._step,
                    profile_dir=profile,
                    on_browser=lambda pid: self.sig_browser.emit(self.acc_id, pid),
                    on_ready=lambda driver: setattr(self, '_browser_driver', driver))
                if self._cancel_login.is_set():
                    raise RuntimeError('已取消')
                self._drop_client()
                fields = {key: str(info.get(key) or '') for key in
                          ('domain', 'cookie', 'user_id', 'university_id', 'realname')}
                fields['login_mode'] = info.get('login_mode') or 'professional'
                account_store().update(self.acc_id, **fields)
                account_store().mark_logged_in(self.acc_id)
                self._account = _account_of(self.acc_id)
                self.sig_courses.emit([])
                self.sig_account.emit(self.account_info())
                self._set_phase('ready')
                self._step('官网已登录，可在内置浏览器查看课程；学校专业版自动任务需登录对应学校入口。'
                           if fields['login_mode'] == 'standard' else '扫码登录完成，已自动保存学校信息')
            except Exception as exc:
                self._close_browser()
                self._set_phase('ready' if self.logged_in() else 'idle')
                if not self._cancel_login.is_set():
                    self.sig_done.emit('error', str(exc))
        self._spawn(job, 'auto-login')

    def login(self, domain: str = ""):
        """扫码登录：后台开 WebSocket，二维码经 sig_qrcode 送到界面。

        分 4 步上报：获取学校信息 -> 已连接等待扫码 -> 扫码成功校验登录 -> 登录完成。
        失败时除了写 ERROR 日志，还会 sig_done.emit("error", 完整原因)，
        让页面把原因原文弹给用户。
        """
        domain = (str(domain or "").strip()
                  or str(self._account.get("domain") or "").strip()
                  or DEFAULT_DOMAIN)
        try:
            domain = self.configure_school(domain)
        except Exception as exc:
            self.sig_done.emit('error', str(exc))
            return

        def job():
            self._set_phase("logging_in")
            self._cancel_login.clear()
            self._step("1/4 正在获取学校信息（" + domain + "）")
            seen = {"n": 0}
            try:
                from api.yuketang import YuketangClient

                if not qrcode_available():
                    raise RuntimeError(
                        "二维码渲染不可用：缺少 Pillow（vendor 的 qrcode 也要它）")

                def on_qrcode(png: bytes):
                    seen["n"] += 1
                    self.sig_qrcode.emit(bytes(png or b""))
                    stamp = time.strftime("%H:%M:%S")
                    self._step("2/4 已连接，等待扫码（第 " + str(seen["n"])
                               + " 次刷新 " + stamp + "）")
                    self._log("INFO", "二维码已就绪，请用微信扫码"
                              "（约 60 秒刷新一次，" + stamp + "）")

                info = YuketangClient.login_by_qrcode(
                    domain,
                    on_qrcode,
                    should_stop=self._cancel_login.is_set,
                    timeout=300,
                )
            except Exception as exc:
                text = str(exc)
                if "已取消" in text:
                    self._log("INFO", "已取消扫码登录")
                    self._set_phase("idle")
                    return
                reason = "扫码登录失败：" + text
                self._log("ERROR", reason)
                self._set_phase("idle")
                self.sig_done.emit("error", reason)
                return

            self._step("3/4 扫码成功，正在校验登录")
            self._store_update({
                "domain": domain,
                "login_mode": "professional",
                "cookie": str(info.get("cookie") or ""),
                "user_id": str(info.get("user_id") or ""),
                "university_id": str(info.get("university_id") or ""),
                "realname": str(info.get("realname") or ""),
            })
            self._drop_client()
            who = (str(self._account.get("realname") or "")
                   or str(self._account.get("user_id") or "") or "雨课堂账号")
            self._log("SUCCESS", "登录成功：" + who)
            self.sig_account.emit(self.account_info())
            self._set_phase("ready")
            self._step("4/4 登录完成")

        self._spawn(job, "login")

    def cancel_login(self):
        self._cancel_login.set()

    # -------------------------------------------------------------- cookie 登录
    def login_with_cookie(self, acc_id: str, cookie: str, domain: str = "") -> None:
        """用浏览器里复制的 cookie 兜底登录（F12 -> csrftoken=...; sessionid=...）。

        fetch_identity() 校验成功才写档案；失败把 YuketangError 的原文
        经 sig_done.emit("error", ...) 透传给页面。
        """
        target = str(acc_id or self.acc_id)
        raw = str(cookie or "").strip()
        try:
            domain = self.configure_school(domain or self._account.get('domain') or '')
        except Exception as exc:
            self.sig_done.emit('error', str(exc))
            return

        def job():
            self._set_phase("logging_in")
            self._step("1/3 正在校验 cookie")
            if not raw:
                reason = ("cookie 为空：请从浏览器 F12 -> Network 里复制"
                          " csrftoken=...; sessionid=... 整段")
                self._log("ERROR", reason)
                self._set_phase("idle")
                self.sig_done.emit("error", reason)
                return

            dom = (str(domain or "").strip()
                   or str(self._account.get("domain") or "").strip()
                   or DEFAULT_DOMAIN)
            client = None
            try:
                from api.yuketang import YuketangClient
                client = YuketangClient(dom, cookie=raw)
                info = client.fetch_identity()
            except Exception as exc:
                reason = "cookie 登录失败：" + str(exc)
                self._log("ERROR", reason)
                try:
                    if client is not None:
                        client.close()
                except Exception:
                    pass
                self._set_phase("idle")
                self.sig_done.emit("error", reason)
                return

            self._step("2/3 校验通过，正在写入账号档案")
            fields = {
                "domain": dom,
                "login_mode": "professional",
                "cookie": raw,
                "user_id": str(info.get("user_id") or ""),
                "university_id": str(info.get("university_id") or ""),
                "realname": str(info.get("realname") or ""),
            }
            try:
                account_store().update(target, **fields)
                account_store().mark_logged_in(target)
            except Exception as exc:
                self._log("WARNING", "账号写入失败：" + str(exc))
            self._account = _account_of(self.acc_id)
            self._drop_client()
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass
            who = fields["realname"] or fields["user_id"] or "雨课堂账号"
            self._log("SUCCESS", "cookie 登录成功：" + who)
            self.sig_account.emit(self.account_info())
            self._set_phase("ready")
            self._step("3/3 登录完成")

        self._spawn(job, "cookie")

    def account_info(self) -> dict:
        a = self._account
        return {
            "acc_id": self.acc_id,
            "domain": str(a.get("domain") or ""),
            "realname": str(a.get("realname") or ""),
            "user_id": str(a.get("user_id") or ""),
            "university_id": str(a.get("university_id") or ""),
            "cookie": masked_cookie(a.get("cookie")),
            "logged_in": self.logged_in(),
            "login_mode": a.get('login_mode') or 'professional',
        }

    def logout(self):
        if self._browser_driver is not None:
            try:
                self._browser_driver.delete_all_cookies()
            except Exception:
                pass
        self._close_browser()
        self._drop_client()
        if self.acc_id:
            account_store().update(self.acc_id, cookie="", user_id="",
                                   university_id="", realname="")
        self._account = _account_of(self.acc_id)
        self._log("INFO", "已清除雨课堂登录状态")
        self.sig_account.emit(self.account_info())
        self._set_phase("idle")

    def _close_browser(self):
        driver, self._browser_driver = self._browser_driver, None
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        self.sig_browser_closed.emit(self.acc_id)

    # -------------------------------------------------------------- 课程
    def load_courses(self):
        def job():
            self._set_phase("loading")
            try:
                if self._account.get('login_mode') == 'standard':
                    from api.yuketang.standard_courses import list_courses
                    courses = list_courses(self._account.get('cookie'), self._account.get('user_id'),
                                           self._browser_driver)
                else:
                    client = self._ensure_client()
                    courses = client.list_courses()
            except Exception as exc:
                self._log("ERROR", "读取课程失败：" + str(exc))
                self.sig_courses.emit([])
                self._set_phase("ready" if self.logged_in() else "idle")
                return
            self._log("INFO", "读取到 " + str(len(courses)) + " 门雨课堂课程")
            self.sig_courses.emit(list(courses or []))
            self._set_phase("loaded")

        self._spawn(job, "courses")

    def _open_course_browser(self, course):
        from urllib.parse import urlsplit
        url = str(course.get('portal_url') or '')
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname != 'www.yuketang.cn'
                or not parsed.path.startswith(('/v2/web/studentLog/', '/v2/web/teacherLog/'))):
            raise RuntimeError('课程入口不可用，请先读取普通版课程。')
        if self._browser_driver is None:
            raise RuntimeError('浏览器尚未连接，请点击扫码识别学校重新打开会话。')
        if urlsplit(self._browser_driver.current_url).hostname != 'www.yuketang.cn':
            self._browser_driver.get('https://www.yuketang.cn/v2/web/index')
        # Match the official goLog method's non-authentication context.
        self._browser_driver.execute_script('''
            const course = arguments[0];
            const values = {uv_id:course.university_id, university_id:course.university_id,
                platform_id:course.platform_id || '3', classroom_id:course.classroom_id,
                classroomId:course.classroom_id};
            for (const [name, value] of Object.entries(values)) {
                if (value) {
                    localStorage.setItem(name, String(value)); window[name] = value;
                    document.cookie = name + '=' + encodeURIComponent(value) + '; path=/';
                } else {
                    localStorage.removeItem(name); delete window[name];
                    document.cookie = name + '=; max-age=0; path=/';
                }
            }
        ''', {key: course.get(key) or '' for key in ('university_id','platform_id','classroom_id')})
        self._browser_driver.get(url)

    def open_course(self, course):
        def job():
            self._set_phase("loading")
            try:
                self._open_course_browser(course)
                self._log("SUCCESS", "已打开课程：" + str(course.get("course_name") or ""))
            except Exception as exc:
                self.sig_done.emit("error", "打开课程失败：" + str(exc))
            finally:
                self._set_phase("ready" if self.logged_in() else "idle")
        return self._spawn(job, "open-course")

    def next_course(self):
        if self.phase == "running":
            self._next_course.set()

    def _start_browser_queue(self, courses):
        def job():
            self._set_phase("running")
            visited = 0
            try:
                if self._browser_driver is None:
                    raise RuntimeError("浏览器尚未连接，请先点击扫码识别学校。")
                # Validate current browser identity before opening saved course entries.
                from api.yuketang.standard_courses import list_courses
                current = list_courses(self._account.get("cookie"), self._account.get("user_id"), self._browser_driver)
                allowed = {(c["identity"], c["classroom_id"]) for c in current}
                for index, course in enumerate(courses):
                    if self._stop.is_set():
                        break
                    if course.get("identity") != 2:
                        raise RuntimeError("我教的课不属于学生学习任务，请仅选择我学的课。")
                    if (course.get("identity"), course.get("classroom_id")) not in allowed:
                        raise RuntimeError("课程已变更或不属于当前账号，请重新读取课程。")
                    self._next_course.clear()
                    self._open_course_browser(course)
                    visited += 1
                    self.sig_progress.emit({"course": course.get("course_name", ""),
                        "course_index": index + 1, "course_total": len(courses),
                        "browser_queue": True, "visited": visited, "rate": 0})
                    self._log("INFO", "课程已打开，请在浏览器学习；完成后点击下一门。不会自动提交或标记完成。")
                    while not self._stop.wait(0.2):
                        if self._next_course.is_set():
                            break
                stopped = self._stop.is_set()
                self.sig_done.emit("stopped" if stopped else "ok",
                    ("已停止浏览器学习队列" if stopped else "浏览器学习队列已结束")
                    + "（已打开 " + str(visited) + " 门；学习完成状态以官网为准）")
            except Exception as exc:
                self.sig_done.emit("error", "浏览器学习启动失败：" + str(exc))
            finally:
                self._set_phase("ready" if self.logged_in() else "idle")
        return self._spawn(job, "browser-study")

    # -------------------------------------------------------------- 刷课
    def start(self, selected: list, options: dict = None):
        """刷选中的课程。selected = [课程 dict, ...]（来自 sig_courses）。"""
        courses = [c for c in (selected or []) if isinstance(c, dict)]
        if not courses:
            self._log("WARNING", "没有勾选任何雨课堂课程")
            return
        if self._account.get("login_mode") == "standard":
            return self._start_browser_queue(courses)
        options = dict(options or {})
        workers = max(1, int(options.get("jobs", 1) or 1))

        def job():
            self._set_phase("running")
            try:
                client = self._ensure_client()
            except Exception as exc:
                self._log("ERROR", "刷课初始化失败：" + str(exc))
                self._set_phase("idle")
                self.sig_done.emit("error", "雨课堂刷课初始化失败：" + str(exc))
                return

            total = {"videos": 0, "done": 0, "skipped": 0, "failed": 0}
            stopped = False
            try:
                for index, course in enumerate(courses):
                    if self._stop.is_set():
                        stopped = True
                        break
                    name = str(course.get("course_name") or course.get("title") or "")
                    self._log("INFO", "[" + str(index + 1) + "/" + str(len(courses))
                              + "] 开始处理：" + name)
                    self.sig_progress.emit({
                        "course": name, "video": "", "rate": 0.0,
                        "course_index": index + 1, "course_total": len(courses),
                        "videos_done": total["done"], "videos_total": total["videos"],
                    })

                    def on_video_start(_video_id, video_name, _name=name):
                        self.sig_progress.emit({
                            "course": _name, "video": str(video_name), "rate": 0.0,
                            "course_index": index + 1, "course_total": len(courses),
                            "videos_done": total["done"], "videos_total": total["videos"],
                            "started": True,
                        })

                    def on_progress(payload, _name=name):
                        payload = dict(payload or {})
                        self.sig_progress.emit({
                            "course": _name,
                            "video": str(payload.get("video_name") or ""),
                            "rate": float(payload.get("rate") or 0.0),
                            "watch_length": payload.get("watch_length"),
                            "course_index": index + 1, "course_total": len(courses),
                            "videos_done": total["done"], "videos_total": total["videos"],
                        })

                    result = client.watch_course_videos(
                        course.get("classroom_id"),
                        course.get("course_sign"),
                        course.get("course_id"),
                        course.get("sku_id"),
                        on_progress=on_progress,
                        should_stop=self._stop.is_set,
                        on_video_start=on_video_start,
                        max_workers=workers,
                    )
                    result = result if isinstance(result, dict) else {}
                    total["videos"] += int(result.get("total") or 0)
                    total["done"] += int(result.get("done") or 0)
                    total["skipped"] += int(result.get("skipped") or 0)
                    total["failed"] += len(result.get("failed") or [])
                    self._log(
                        "INFO",
                        "[" + name + "] 视频 " + str(result.get("total") or 0)
                        + " 个：完成 " + str(result.get("done") or 0)
                        + "，跳过 " + str(result.get("skipped") or 0)
                        + "，失败 " + str(len(result.get("failed") or [])),
                    )
            except Exception as exc:
                self._log("ERROR", "刷课中断：" + str(exc))
                self._set_phase("ready")
                self.sig_done.emit("error", "雨课堂刷课出错：" + str(exc))
                return

            if self._stop.is_set() or stopped:
                self._log("WARNING", "已停止雨课堂刷课")
                self._set_phase("ready")
                self.sig_done.emit("stopped", "已停止（完成 "
                                   + str(total["done"]) + " 个视频）")
                return
            self._log("SUCCESS", "雨课堂刷课完成：完成 " + str(total["done"])
                      + "，跳过 " + str(total["skipped"]) + "，失败 " + str(total["failed"]))
            self._set_phase("ready")
            self.sig_done.emit("ok", "全部处理完毕（完成 " + str(total["done"])
                               + " 个视频）")

        self._spawn(job, "study")

    def stop(self):
        self._stop.set()

    def shutdown(self):
        self._stop.set()
        self._cancel_login.set()
        self._drop_client()
        self._close_browser()


# ------------------------------------------------------------------ 注册表
_runners: dict = {}
_runners_lock = threading.Lock()


def get_runner(acc_id: str = "") -> YuketangRunner:
    """按账号取 runner；没有就建一个（每个账号一个实例，互不干扰）。"""
    key = str(acc_id or "")
    if not key:
        key = str(account_store().active or "")
    with _runners_lock:
        runner = _runners.get(key)
        if runner is None:
            runner = YuketangRunner(key)
            _runners[key] = runner
        return runner


def all_runners() -> dict:
    """当前进程里的全部 runner 快照（acc_id -> runner）。"""
    with _runners_lock:
        return dict(_runners)


def drop_runner(acc_id: str) -> None:
    """删账号时清掉对应 runner（先停线程、关客户端，再从注册表摘掉）。"""
    key = str(acc_id or "")
    with _runners_lock:
        runner = _runners.pop(key, None)
    if runner is not None:
        try:
            runner.shutdown()
        except Exception:
            pass


def shutdown_all() -> None:
    """主窗口退出时调用：停止并释放所有账号的 runner。"""
    with _runners_lock:
        runners = list(_runners.values())
    for runner in runners:
        try:
            runner.shutdown()
        except Exception:
            pass
