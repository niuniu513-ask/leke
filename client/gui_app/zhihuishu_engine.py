# -*- coding: utf-8 -*-
"""智慧树（知到）适配层。

职责边界
--------
- 上游引擎在 api/zhihuishu/（vendor，见同目录 NOTICE.md）；
- 本模块是引擎和界面之间的**唯一桥梁**：账号读写、后台线程、信号回抛；
- page_zhihuishu.py 只画界面，不直接碰引擎。

多账号（每账号一个 runner）
---------------------------
沿用 v1.5.5 起 U校园 / 雨课堂的固定套路：

* 账号档案 zhihuishu_accounts.json（gui_app.platform_accounts.AccountStore）；
* 旧 zhihuishu.json 在档案为空时自动迁移成第一条记录（并改名 .migrated）；
* get_runner(acc_id) 按账号取 runner，每个 runner 自带 acc_id / 自己的
  Fucker / 自己的 stop 事件 / 自己的 phase，因此多账号可真正并行刷课。

三条设计约束（沿用 U校园 / 雨课堂踩出来的经验）
-----------------------------------------------
1. **引擎延迟导入**：本模块在 import 期绝不触碰 api.zhihuishu，
   缺依赖时只是"智慧树用不了"，程序照常启动。
2. **不跨线程传对象**：信号里只走 dict / str，不传 requests.Session、不传控件、
   不传 ObjDict（引擎的返回是 dict 子类，统一转成普通 dict 再发）。
3. **停止靠 threading.Event**：引擎的进度钩子读它；GUI 线程只置位。
   钩子里若发现已停止就抛 TimeLimitExceeded，zhidao 侧会整体退出课程循环。

登录方式
--------
智慧树是**账密登录**（passport.zhihuishu.com），不需要扫码、不需要浏览器。
若遇到"需要验证码 / 短信 / 设备二次认证"，引擎给的原文会翻译成人话提示用户
"请先在浏览器登录一次智慧树"，同时保留 cookie 兜底登录（F12 复制 cookie）。

日志（沿用 v1.5.5 双写规矩）
-----------------------------
_log(level, text) 除了发 sig_log 给页面日志框，还写 loguru（leke.log）——
api.logger **延迟导入**，因为启动时 stream.install() 必须先于它。
统一前缀 [智慧树/<acc_id>] 方便 grep。loguru 用 `{}` 占位符，不是 %s。
"""
from __future__ import annotations

import json
import os
import threading

from PySide6.QtCore import QObject, Signal

from gui_app import store
from gui_app.platform_accounts import AccountStore, mask, safe_suffix

#: 旧单账号文件名（迁移来源）
ZHIHUISHU_FILE = "zhihuishu.json"

#: 多账号档案文件名
ACCOUNTS_FILE = "zhihuishu_accounts.json"

#: 默认播放倍速（引擎里 zhidao 默认 1.5 / hike 默认 1.25，这里统一给 1.5）
DEFAULT_SPEED = 1.5

#: 每条账号记录的字段与默认值
ACCOUNT_DEFAULTS = {
    "username": "",
    "password": "",
    "realname": "",
    "cookie": "",       # 登录后的 cookie（JSON 串），用于免密复用
    "uuid": "",
    "speed": DEFAULT_SPEED,
}

#: loguru 统一前缀模板（第一条参数是 acc_id，第二条是正文）
LOG_TAG = "[智慧树/{}] {}"

#: 引擎登录异常原文 → 用户能看懂的处理建议
_LOGIN_HINTS = (
    ("Username or password invalid", "账号或密码错误"),
    ("Multiple invalid attempts", "尝试次数过多已触发风控验证码，请先在浏览器登录一次智慧树"),
    ("requires captcha", "需要图形验证码，请先在浏览器登录一次智慧树"),
    ("SMS verification", "需要短信验证，请先在浏览器登录一次智慧树"),
    ("need auth", "该设备需要二次认证，请先在浏览器登录一次智慧树，或用 cookie 登录"),
    ("No cookies found", "登录未拿到会话，请稍后重试"),
)

#: 进程内账号档案（懒加载）
_STORE = None
_STORE_LOCK = threading.Lock()


# ------------------------------------------------------------------ 账号档案
def account_store() -> AccountStore:
    """进程内唯一的智慧树账号档案（首次访问时加载 / 迁移，空档案自动建一条）。"""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            loaded = AccountStore(ACCOUNTS_FILE, ZHIHUISHU_FILE, ACCOUNT_DEFAULTS).load()
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


def zhihuishu_path() -> str:
    """旧单账号文件路径（迁移成功后会被改名成 .migrated）。"""
    return os.path.join(store.app_dir(), ZHIHUISHU_FILE)


def _account_of(acc_id: str) -> dict:
    """把档案里的某条记录补全成"带全部默认值"的 dict（找不到就给默认值）。"""
    data = dict(ACCOUNT_DEFAULTS)
    acc = account_store().get(str(acc_id)) if acc_id else {}
    if acc:
        data.update(acc)
    return data


# ------------------------------------------------------------------ 旧接口兼容
def zhihuishu_load() -> dict:
    """当前账号的字段（兼容旧单账号调用）。"""
    return _account_of(account_store().active)


def zhihuishu_save(patch: dict) -> list:
    """把 patch 合并进当前账号并落盘，返回变更描述（密码 / cookie 打码）。"""
    book = account_store()
    acc_id = book.active
    if not acc_id:
        acc_id = book.add("账号 1")["id"]
    book.update(acc_id, **(patch or {}))
    out = []
    for key, value in (patch or {}).items():
        shown = "******" if key in ("password", "cookie") else value
        out.append(str(key) + " = " + str(shown))
    return out


def masked_cookie(cookie: str) -> str:
    """cookie 打码（复用共享件的 mask）。"""
    return mask(cookie)


def humanize_login_error(exc) -> str:
    """把引擎抛出的英文异常翻译成"原因 + 建议"。"""
    text = str(exc)
    for marker, hint in _LOGIN_HINTS:
        if marker.lower() in text.lower():
            return "登录失败：" + hint
    return "登录失败：" + text


# ------------------------------------------------------------------ runner
class ZhihuishuRunner(QObject):
    """智慧树引擎 runner：**一个账号一个实例**，阻塞调用全部搬进后台线程。

    phase 状态机::

        idle -> logging_in -> ready -> loading -> loaded
                              ↘ running -> ready

    任何失败都回 idle（并带错误文案）。
    """

    sig_log = Signal(str, str)          # level, text
    sig_state = Signal(str)             # phase 变化
    sig_account = Signal(dict)          # 账号信息（不含密码原文）
    sig_courses = Signal(list)          # 课程列表（共享课 + 校内课）
    sig_progress = Signal(dict)         # 刷课进度
    sig_done = Signal(str, str)         # status(ok/stopped/error), message
    sig_login_step = Signal(str, str)   # acc_id, 登录步骤文案

    def __init__(self, acc_id: str = "", parent=None):
        super().__init__(parent)
        self.acc_id = str(acc_id or "")
        self._phase = "idle"
        self._lock = threading.Lock()
        self._client = None
        self._tle = None                      # TimeLimitExceeded，用 stopping 中断
        self._stop = threading.Event()
        self._thread = None
        self._account = _account_of(self.acc_id)

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
        """重新从档案读取本账号字段（切换账号 / 保存账密后用）。"""
        self._account = _account_of(self.acc_id)
        self._drop_client()
        return dict(self._account)

    def _set_phase(self, phase: str):
        self._phase = str(phase)
        self.sig_state.emit(self._phase)

    def _log(self, level: str, text: str):
        """双写：页面日志框 + loguru（leke.log）。api.logger 延迟导入。"""
        level = str(level or "INFO").upper()
        text = str(text)
        self.sig_log.emit(level, text)
        try:
            from api.logger import logger           # 延迟导入，见模块 docstring
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

    # -------------------------------------------------------------- 引擎事件
    def _on_engine_event(self, kind: str, data):
        """引擎线程回调：只发信号；已停止时抛 TimeLimitExceeded 中断当前课程。"""
        if self._stop.is_set():
            tle = self._tle
            if tle is not None:
                raise tle("用户已停止")
        d = data or {}
        if kind == "chapter":
            self.sig_progress.emit({"phase": "chapter",
                                    "chapter": str(d.get("name") or "")})
        elif kind == "video":
            self.sig_progress.emit({"phase": "video",
                                    "video": str(d.get("name") or ""),
                                    "video_id": str(d.get("video_id") or ""),
                                    "rate": 0.0})
        elif kind == "progress":
            total = float(d.get("total") or 0)
            played = float(d.get("played") or 0)
            self.sig_progress.emit({
                "phase": "playing",
                "video_id": str(d.get("video_id") or ""),
                "played": played, "total": total,
                "rate": (played / total) if total > 0 else 0.0,
            })

    # -------------------------------------------------------------- 客户端
    def _make_client(self):
        """按本账号已存字段造一个引擎实例。缺依赖时抛异常，由调用方兜住。"""
        from api.zhihuishu import Fucker, TimeLimitExceeded   # 延迟导入

        self._tle = TimeLimitExceeded
        account = self._account
        cookie = {}
        raw = str(account.get("cookie") or "").strip()
        if raw:
            try:
                cookie = json.loads(raw)
            except Exception:
                cookie = {}
        speed = account.get("speed")
        try:
            speed = float(speed) if speed else None
        except Exception:
            speed = None
        client = Fucker(cookies=cookie or None, speed=speed)
        client.on_event = self._on_engine_event
        return client

    def _ensure_client(self):
        if self._client is None:
            self._client = self._make_client()
        return self._client

    def _drop_client(self):
        client, self._client = self._client, None
        if client is not None:
            try:
                client.session.close()
            except Exception:
                pass

    def _spawn(self, target, kind: str):
        if self.busy():
            self._log("WARNING", "已有智慧树任务在跑，忽略本次操作")
            return False
        self._stop.clear()
        name = "zhihuishu-" + str(kind) + "-" + safe_suffix(self.acc_id)
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

    def _persist_cookie(self, client) -> str:
        """把引擎 session 里的 cookie 存成 JSON 串。"""
        try:
            import requests as _rq
            jar = getattr(client, "cookies", None)
            data = _rq.utils.dict_from_cookiejar(jar) if jar is not None else {}
        except Exception:
            data = {}
        try:
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            return ""

    # -------------------------------------------------------------- 账密登录
    def login(self, username: str = "", password: str = ""):
        """账密登录：账号密码走 passport.zhihuishu.com，不需要浏览器。"""
        user = str(username or self._account.get("username") or "").strip()
        pwd = str(password or self._account.get("password") or "")

        def job():
            self._set_phase("logging_in")
            self._step("1/3 正在连接智慧树服务器")
            if not user or not pwd:
                reason = "账号或密码为空，请先填写"
                self._log("ERROR", reason)
                self._set_phase("idle")
                self.sig_done.emit("error", reason)
                return
            try:
                client = self._make_client()
                self._step("2/3 正在校验账号密码")
                client.login(user, pwd, interactive=False)
            except Exception as exc:
                reason = humanize_login_error(exc)
                self._log("ERROR", reason)
                self._set_phase("idle")
                self.sig_done.emit("error", reason)
                return

            self._step("3/3 正在保存登录状态")
            self._client = client
            self._store_update({
                "username": user,
                "password": pwd,
                "cookie": self._persist_cookie(client),
                "uuid": str(getattr(client, "uuid", "") or ""),
            })
            who = str(self._account.get("realname") or "") or user
            self._log("SUCCESS", "登录成功：" + who)
            self.sig_account.emit(self.account_info())
            self._set_phase("ready")

        self._spawn(job, "login")

    def login_with_cookie(self, cookie: str) -> None:
        """用浏览器里复制的 cookie 兜底登录（遇到验证码 / 短信 / 设备认证时用）。"""
        raw = str(cookie or "").strip()

        def job():
            self._set_phase("logging_in")
            self._step("1/3 正在校验 cookie")
            if not raw:
                reason = ("cookie 为空：请在浏览器登录智慧树后，F12 → Network "
                          "复制请求头里的 Cookie 整段")
                self._log("ERROR", reason)
                self._set_phase("idle")
                self.sig_done.emit("error", reason)
                return
            # 允许用户直接粘贴 "k=v; k2=v2" 这种浏览器格式
            data = self._parse_cookie_text(raw)
            try:
                self._store_update({"cookie": json.dumps(data, ensure_ascii=False)})
                client = self._make_client()
                self._step("2/3 正在校验登录态")
                # 拉一次课程列表即可确认 cookie 有效
                client.getZhidaoList()
            except Exception as exc:
                reason = "cookie 无效或已过期：" + str(exc)
                self._log("ERROR", reason)
                self._set_phase("idle")
                self.sig_done.emit("error", reason)
                return
            self._client = client
            self._log("SUCCESS", "cookie 登录成功")
            self.sig_account.emit(self.account_info())
            self._set_phase("ready")
            self._step("3/3 完成")

        self._spawn(job, "cookie")

    @staticmethod
    def _parse_cookie_text(raw: str) -> dict:
        """解析两种 cookie 输入：JSON 串 或 浏览器 "k=v; k2=v2" 串。"""
        text = str(raw or "").strip()
        if text.startswith("{"):
            try:
                return dict(json.loads(text))
            except Exception:
                pass
        out = {}
        for part in text.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def account_info(self) -> dict:
        a = self._account
        return {
            "acc_id": self.acc_id,
            "username": str(a.get("username") or ""),
            "realname": str(a.get("realname") or ""),
            "password": "******" if a.get("password") else "",
            "cookie": masked_cookie(a.get("cookie")),
            "speed": a.get("speed") or DEFAULT_SPEED,
            "logged_in": self.logged_in(),
        }

    def logout(self):
        self._drop_client()
        if self.acc_id:
            account_store().update(self.acc_id, cookie="", uuid="", realname="")
        self._account = _account_of(self.acc_id)
        self._log("INFO", "已清除智慧树登录状态")
        self.sig_account.emit(self.account_info())
        self._set_phase("idle")

    # -------------------------------------------------------------- 课程
    def load_courses(self):
        """拉取课程：共享课（知到）+ 校内学分课，合成统一结构。"""

        def job():
            self._set_phase("loading")
            try:
                client = self._ensure_client()
            except Exception as exc:
                self._log("ERROR", "智慧树初始化失败：" + str(exc))
                self._set_phase("idle")
                self.sig_done.emit("error", "智慧树初始化失败：" + str(exc))
                return

            out = []
            sources = (
                ("zhidao", "读取共享课", client.getZhidaoList, "secret", "共享课（知到）"),
                ("hike", "读取校内学分课", client.getHikeList, "courseId", "校内学分课"),
            )
            for kind, label, fn, id_key, subtitle in sources:
                try:
                    rows = fn() or []
                except Exception as exc:
                    self._log("WARNING", label + "失败：" + str(exc))
                    continue
                got = 0
                for row in rows:
                    try:
                        data = dict(row)
                    except Exception:
                        continue
                    cid = str(data.get(id_key) or "").strip()
                    title = str(data.get("courseName") or data.get("name") or "").strip()
                    if not cid or not title:
                        continue
                    out.append({
                        "title": title,
                        "course_id": cid,
                        "kind": kind,
                        "subtitle": subtitle,
                    })
                    got += 1
                self._log("INFO", label + "：" + str(got) + " 门")

            if not out:
                self._log("WARNING", "没有读到任何课程（可能是登录态失效，或本学期无课）")
            else:
                self._log("SUCCESS", "共读取到 " + str(len(out)) + " 门课程")
            self.sig_courses.emit(out)
            self._set_phase("loaded")

        self._spawn(job, "courses")

    # -------------------------------------------------------------- 刷课
    def start(self, selected: list, options: dict = None):
        """处理选中的课程。selected = [课程 dict, ...]（来自 sig_courses）。"""
        courses = [c for c in (selected or []) if isinstance(c, dict)]
        if not courses:
            self._log("WARNING", "没有勾选任何智慧树课程")
            return

        def job():
            self._set_phase("running")
            try:
                client = self._ensure_client()
            except Exception as exc:
                self._log("ERROR", "刷课初始化失败：" + str(exc))
                self._set_phase("idle")
                self.sig_done.emit("error", "智慧树刷课初始化失败：" + str(exc))
                return

            done = failed = stopped = 0
            total = len(courses)
            for index, course in enumerate(courses):
                if self._stop.is_set():
                    stopped = 1
                    break
                name = str(course.get("title") or course.get("course_id") or "")
                self.sig_progress.emit({
                    "course": name, "chapter": "", "video": "", "rate": 0.0,
                    "course_index": index + 1, "course_total": total,
                    "courses_done": done,
                })
                self._log("INFO", "[" + str(index + 1) + "/" + str(total)
                          + "] 开始处理：" + name)
                try:
                    client.fuckCourse(str(course.get("course_id") or ""))
                    done += 1
                except Exception as exc:
                    if self._stop.is_set() or "已停止" in str(exc):
                        stopped = 1
                        break
                    failed += 1
                    self._log("ERROR", "[" + name + "] 处理失败：" + str(exc))

            if stopped or self._stop.is_set():
                self._log("WARNING", "已停止智慧树刷课")
                self._set_phase("ready")
                self.sig_done.emit("stopped", "已停止（完成 " + str(done) + " 门课程）")
                return
            self._log("SUCCESS", "智慧树处理完成：完成 " + str(done)
                      + "，失败 " + str(failed))
            self._set_phase("ready")
            self.sig_done.emit("ok", "全部处理完毕（完成 " + str(done) + " 门课程）")

        self._spawn(job, "study")

    def stop(self):
        self._stop.set()

    def shutdown(self):
        self._stop.set()
        self._drop_client()


# ------------------------------------------------------------------ 注册表
_runners: dict = {}
_runners_lock = threading.Lock()


def get_runner(acc_id: str = "") -> ZhihuishuRunner:
    """按账号取 runner；没有就建一个（每个账号一个实例，互不干扰）。"""
    key = str(acc_id or "")
    if not key:
        key = str(account_store().active or "")
    with _runners_lock:
        runner = _runners.get(key)
        if runner is None:
            runner = ZhihuishuRunner(key)
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
        gz = _runners.pop(key, None)
    if gz is not None:
        try:
            gz.shutdown()
        except Exception:
            pass


def shutdown_all() -> None:
    """主窗口退出时调用：停止并释放所有账号的 runner。"""
    with _runners_lock:
        runners = list(_runners.values())
    for gz in runners:
        try:
            gz.shutdown()
        except Exception:
            pass
