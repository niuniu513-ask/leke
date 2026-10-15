# -*- coding: utf-8 -*-
"""U校园（Unipus）适配层：把 vendor 引擎接进乐课的 Qt 结构（多账号版）。

vendor 引擎（api/unipus/unipus_engine.py）是一个控制台形态的 Selenium 脚本：
- 引擎日志走模块级 gui_log_queue（queue.Queue）；
- UCampusBot(config_path, skip_check=True) 构造即打开 Edge 可见窗口；
- bot._login() 做 SSO 账密登录（遇验证码需人工在浏览器里完成）；
- AISolver.process_selected_tabs / solve_current_page 是同步阻塞调用。

本文件负责：
- 账号存 unipus_accounts.json（AccountStore 档案，与学习通的 accounts.json 分离），
  旧的单账号 unipus.json 会被 AccountStore 自动迁移；
- 每个账号一个 UnipusRunner：多账号并行 = 多个 Edge 窗口各跑各的，
  runner 注册表由 get_runner / all_runners / drop_runner / shutdown_all 管理；
- 引擎的 Config 需要一个 json 文件路径 —— 连接时写临时文件、构造完即删；
- UnipusRunner 用后台线程包住阻塞调用，gui_log_queue 由泵线程转发为 Qt 信号；
- 任务扫描逻辑从原 fluent_ui.py 抽取（课程目录 Unit 页 + 章节内部 level1/level2）。

注意：vendor 引擎在本模块内「延迟导入」（方法内部 import），
因此未安装 selenium / 引擎缺失时，导入本文件与构建页面都不会报错。

已知限制：vendor 的 gui_log_queue 是模块级单例，多账号同时在线时
引擎侧那几条日志由多个泵线程争抢，归属可能串线；适配层自身的 _log
（绝大多数信息）始终带正确的 acc_id。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading

from PySide6.QtCore import QObject, Signal

from gui_app import store
from gui_app.platform_accounts import AccountStore, safe_suffix

#: 旧单账号文件名（AccountStore 迁移后会改名成 .migrated）
UNIPUS_FILE = "unipus.json"
#: 新的多账号档案文件名
UNIPUS_ACCOUNTS_FILE = "unipus_accounts.json"

_UNIPUS_DEFAULTS = {
    "username": "",
    "password": "",
    "token_full": "",   # 可选：官方 token（普通用户留空即可，走账密登录）
    # AI 覆盖项：留空 = 复用 config.ini [tiku] 的 endpoint / key / model
    "ai_endpoint": "",
    "ai_key": "",
    "ai_model": "",
}


# ------------------------------------------------------------------ 配置读写
_store: AccountStore | None = None
_store_lock = threading.Lock()


def account_store() -> AccountStore:
    """进程内唯一的 U校园账号档案（页面与 runner 共用同一份）。"""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = AccountStore(UNIPUS_ACCOUNTS_FILE, UNIPUS_FILE,
                                      dict(_UNIPUS_DEFAULTS)).load()
    return _store


def unipus_path() -> str:
    """旧单账号文件路径（档案迁移后只剩 .migrated，保留给兼容/排障用）。"""
    return os.path.join(store.app_dir(), UNIPUS_FILE)


def unipus_load(acc_id: str = "") -> dict:
    """读某个账号的配置；acc_id 为空时取当前账号。"""
    st = account_store()
    aid = str(acc_id or st.active or "")
    out = dict(_UNIPUS_DEFAULTS)
    acc = st.get(aid) if aid else {}
    for key in _UNIPUS_DEFAULTS:
        if key in (acc or {}):
            out[key] = acc[key]
    return out


def unipus_save(patch: dict, acc_id: str = "") -> list[str]:
    """合并保存某个账号的配置，返回变更描述（密码/token 打码）。

    档案里没有对应账号时自动建一条（保持旧 unipus_save 的调用手感）。
    """
    st = account_store()
    aid = str(acc_id or st.active or "")
    if not aid or not st.get(aid):
        aid = str(st.add().get("id") or "")
    st.update(aid, **(patch or {}))
    out = []
    for k, v in (patch or {}).items():
        shown = "******" if k in ("password", "token_full") else v
        out.append(f"{k} = {shown}")
    return out


def resolve_ai(acc_id: str = "") -> tuple[str, str, str]:
    """AI 配置：(endpoint, key, model)。

    该账号的覆盖字段优先；任一为空则回退 config.ini [tiku] 段
    （与学习通 AI 答题共用同一份配置，不用重复填）。
    """
    u = unipus_load(acc_id)
    endpoint = str(u.get("ai_endpoint") or "").strip()
    key = str(u.get("ai_key") or "").strip()
    model = str(u.get("ai_model") or "").strip()
    if not (endpoint and key and model):
        try:
            cp = store.read()
            sec = dict(cp.items("tiku"))
        except Exception:
            sec = {}
        endpoint = endpoint or str(sec.get("endpoint", "") or "").strip()
        key = key or str(sec.get("key", "") or "").strip()
        model = model or str(sec.get("model", "") or "").strip()
    return endpoint, key, model


def ai_override_keys(acc_id: str = "") -> list[str]:
    """该账号显式填过的 AI 覆盖字段名（给界面展示「覆盖 / 继承」用）。"""
    u = unipus_load(acc_id)
    return [k for k in ("ai_endpoint", "ai_key", "ai_model")
            if str(u.get(k) or "").strip()]


def _mask_key(key: str) -> str:
    key = key or ""
    if len(key) <= 8:
        return "******"
    return key[:5] + "******" + key[-4:]


# ------------------------------------------------------------------ 引擎适配
class UnipusRunner(QObject):
    """U校园引擎 runner：一个账号一个实例，阻塞调用全部搬进后台线程。

    phase 状态机：
      idle → connecting → login_wait → ready → scanning → scanned
                                                ↘ running → ready
      任何失败回到 idle（已连接时）或保持 ready（扫描/处理失败时）。

    跨线程只传纯数据：sig_tasks 只带 index/display/is_compulsory，
    WebElement（t._element）永远留在 runner 线程内，绝不跨信号。
    """

    sig_browser = Signal(str, int)     # account, owned WebDriver service PID
    sig_log = Signal(str)              # 引擎日志（gui_log_queue 泵 + 适配层事件）
    sig_login_step = Signal(str, str)  # acc_id, 步骤文案
    sig_state = Signal(str)            # phase 变化
    sig_tasks = Signal(list)           # 扫描结果视图 [{index, display, is_compulsory}]
    sig_done = Signal(str, str)        # kind(process/quick), status(ok/stopped/error)

    def __init__(self, acc_id: str = "default", parent=None):
        super().__init__(parent)
        self.acc_id = str(acc_id or "default")
        self._bot = None
        self._solver = None
        self._tabs: list[dict] = []      # 完整扫描结果（含 _element，不跨信号）
        self._phase = "idle"
        self._processed = 0              # 已处理任务数（摘要展示用）
        self._lock = threading.Lock()
        self._generation = 0
        self._pump_stop = threading.Event()
        self._pump_thread: threading.Thread | None = None

    # ---- 状态 ----
    @property
    def phase(self) -> str:
        return self._phase

    @property
    def connected(self) -> bool:
        return self._solver is not None

    @property
    def processed(self) -> int:
        return int(self._processed)

    def _set_phase(self, p: str):
        self._phase = p
        self.sig_state.emit(p)

    def _log(self, text: str):
        """页面信号 + loguru 双写（进 exe 旁的 leke.log，出问题有事后证据）。"""
        text = str(text)
        self.sig_log.emit(text)
        try:
            from api.logger import logger          # 延迟导入：顶层不硬依赖 loguru
            # loguru 用 str.format 的 {} 占位符（不是 %s）：写 "%s" 会把整行
            # 原样记下来、acc_id 和正文全丢，日志就白写了。
            logger.info("[U校园/{}] {}", self.acc_id, text)
        except Exception:
            pass

    def _step(self, text: str):
        self.sig_login_step.emit(self.acc_id, str(text))

    # ---- 账号 ----
    def account(self) -> dict:
        return unipus_load(self.acc_id)

    def resolve_ai_cfg(self) -> tuple[str, str, str]:
        return resolve_ai(self.acc_id)

    # ---- 连接（开 Edge → 登录 → 组装 solver）----
    def connect(self):
        with self._lock:
            if self._phase != "idle":
                return
            self._phase = "connecting"
            self._generation += 1
            generation = self._generation
        self.sig_state.emit("connecting")
        self._step("连接中")

        u = self.account()
        if not (str(u.get("username") or "").strip()
                and str(u.get("password") or "").strip()):
            self._log("请先填写 U校园 账号和密码，再点「连接并登录」。")
            self._step("缺少账号或密码")
            self._set_phase("idle")
            return
        endpoint, key, model = self.resolve_ai_cfg()
        if not (endpoint and key and model):
            self._log("AI 配置不完整：请先到「AI 大模型」页配置 AI 大模型"
                      "（endpoint / key / model），U校园答题与学习通共用这一份配置。")
            self._step("AI 配置不完整")
            self._set_phase("idle")
            return

        self._start_pump()
        threading.Thread(
            target=self._connect_thread, args=(dict(u), generation), daemon=True,
            name=f"unipus-connect-{safe_suffix(self.acc_id)}").start()

    def _write_engine_config(self, u: dict) -> str:
        endpoint, key, model = self.resolve_ai_cfg()
        fd, path = tempfile.mkstemp(prefix="leke_unipus_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf8") as f:
            json.dump({
                "url": "",
                "username": u.get("username") or "",
                "password": u.get("password") or "",
                "token_full": u.get("token_full") or "",
                "api_key": key,
                "base_url": endpoint,
                "model": model,
                "temperature": 0.3,
                "max_tokens": 2000,
                "debug_mode": False,
            }, f, ensure_ascii=False)
        return path

    def _connect_thread(self, u: dict, generation=None):
        from api.unipus import unipus_engine as vendor
        generation = self._generation if generation is None else generation
        bot = None
        try:
            cfg_file = self._write_engine_config(u)
            try:
                self._log("正在启动 Edge 浏览器并打开 U校园登录页…")
                self._step("启动 Edge")
                # 每个账号一个持久化 profile：登录一次后 cookie/localStorage
                # 落盘，下次连接直接复用会话，不用重新输账密、过验证码
                # （学习通式「填一次账密，以后自动连」体验）。
                profile = os.path.join(
                    store.app_dir(), "unipus_profiles",
                    safe_suffix(self.acc_id))
                bot = vendor.UCampusBot(cfg_file, skip_check=True, profile_dir=profile)
                with self._lock:
                    if generation != self._generation:
                        bot.driver.quit()
                        return
                    self._bot = bot
            finally:
                try:
                    os.remove(cfg_file)
                except OSError:
                    pass

            service = getattr(bot.driver, "service", None)
            process = getattr(service, "process", None)
            if process is not None:
                self.sig_browser.emit(self.acc_id, int(process.pid))

            # 弹窗自动点掉（我知道了 / 系统通知等）
            threading.Thread(
                target=bot.popup_watcher.run, daemon=True,
                name=f"unipus-popup-{safe_suffix(self.acc_id)}").start()

            with self._lock:
                if generation != self._generation:
                    return
                self._set_phase("login_wait")
            self._step("等待人工登录")
            self._log("正在登录 U校园 —— 如遇验证码，请在课程浏览器中手动完成。")
            ok = bool(bot._login())
            if generation != self._generation or self._bot is not bot:
                return
            if not ok:
                self._log("登录未完成：请检查账号密码（或浏览器中的验证状态）后重试。")
                self._step("登录未完成")
                self.shutdown()
                return

            solver = vendor.AISolver(bot.driver, bot.config)
            with self._lock:
                if generation != self._generation or self._bot is not bot:
                    solver.request_stop()
                    return
                self._solver = solver
            try:
                account_store().mark_logged_in(self.acc_id)
            except Exception:
                pass
            self._log("登录成功！可以「扫描任务列表」批量处理，或翻到题目页「快速处理当前页」。")
            with self._lock:
                if generation != self._generation:
                    return
                self._step("登录成功")
                self._set_phase("ready")
        except Exception as e:
            if generation != self._generation:
                return
            self._log(f"连接失败：{e}")
            self._log("常见原因：未安装 Edge 浏览器、网络不通、EdgeDriver 下载失败。")
            self._step("连接失败")
            self.shutdown()
            self._set_phase("idle")

    # ---- 日志泵 ----
    def _start_pump(self):
        if self._pump_thread and self._pump_thread.is_alive():
            return
        self._pump_stop.clear()

        def _pump():
            from api.unipus.unipus_engine import gui_log_queue
            while not self._pump_stop.is_set():
                try:
                    item = gui_log_queue.get(timeout=0.3)
                except Exception:
                    continue
                text = str(item).strip()
                if text:
                    self.sig_log.emit(text)

        self._pump_thread = threading.Thread(
            target=_pump, daemon=True,
            name=f"unipus-log-pump-{safe_suffix(self.acc_id)}")
        self._pump_thread.start()

    # ---- 扫描 ----
    def scan(self):
        if self._phase not in ("ready", "scanned") or not self.connected:
            return
        self._set_phase("scanning")
        self._log("正在扫描任务列表（课程目录 Unit 页或章节内部页）…")
        threading.Thread(
            target=self._scan_thread, daemon=True,
            name=f"unipus-scan-{safe_suffix(self.acc_id)}").start()

    def _scan_thread(self):
        import time

        from api.unipus import unipus_engine as vendor

        bot, solver = self._bot, self._solver
        generation = self._generation
        if bot is None or solver is None:
            return
        driver = bot.driver
        try:
            tabs: list[dict] = []
            course_tabs = self._scan_course_directory(driver, vendor)
            if course_tabs:
                tabs = course_tabs
                self._log(f"课程目录页发现 {len(tabs)} 个任务")
            else:
                level1 = solver._get_level1_tabs()
                if level1:
                    for l1_idx, l1_tab in enumerate(level1):
                        if not vendor.WebDriverHelper.safe_click(driver, l1_tab["element"]):
                            continue
                        time.sleep(1.2)
                        level2 = solver._get_level2_tabs()
                        if not level2:
                            tabs.append({
                                "l1_idx": l1_idx, "l2_idx": -1,
                                "l1_title": l1_tab["title"], "l2_title": "",
                                "display": l1_tab["title"],
                                "is_l2": False, "is_compulsory": True})
                        else:
                            for l2_idx, l2_tab in enumerate(level2):
                                tabs.append({
                                    "l1_idx": l1_idx, "l2_idx": l2_idx,
                                    "l1_title": l1_tab["title"],
                                    "l2_title": l2_tab["title"],
                                    "display": f"  - {l2_tab['title']}",
                                    "is_l2": True, "is_compulsory": True})
                    self._log(f"章节内部页发现 {len(tabs)} 个任务")

            if generation != self._generation or not self.connected:
                return
            self._tabs = tabs
            if not tabs:
                self._log("未扫描到任何任务。请确认浏览器当前停在 U校园 的课程页面内。")
                self.sig_tasks.emit([])
                self._set_phase("ready")
                return
            self.sig_tasks.emit(self._tab_views())
            self._set_phase("scanned")
            n_comp = sum(1 for t in tabs if t.get("is_compulsory"))
            n_done = sum(1 for t in tabs if t.get("is_finished"))
            self._log(f"扫描完成：共 {len(tabs)} 个任务"
                      f"（必修 {n_comp} 个，已完成 {n_done} 个默认跳过）。")
            if n_done >= len(tabs):
                self._log("该页任务点均已完成，无需处理（如需重做请手动勾选）。")
        except Exception as e:
            if generation != self._generation:
                return
            self._log(f"扫描失败：{e}")
            self._set_phase("ready")

    @staticmethod
    def _task_finished(driver, chapter) -> bool:
        """启发式检测课程目录任务点的完成标记。

        在任务项内找完成图标（class 含 finish/complete/done/check）或
        「已完成/已学习」文案；找不到就当未完成 —— 误判方向只会
        「已完成的仍列出」，不会跳过真实未完成的任务，安全侧可接受。
        """
        try:
            return bool(driver.execute_script(r"""
                const task = arguments[0];
                const sel = '[class*="finish" i],[class*="complete" i],'
                          + '[class*="done" i],[class*="check" i],'
                          + '[class*="status" i]';
                for (const el of task.querySelectorAll(sel)) {
                    const cls = (el.getAttribute('class') || '').toLowerCase();
                    if (!/unfinished|incomplete|not.?complete|undone/.test(cls)
                        && /(?:^|[\s_-])(?:finished|completed|done)(?:$|[\s_-])/.test(cls)) return true;
                    const t = (el.innerText || el.textContent || '').trim();
                    if (t && t.length <= 6 && /已完成|已学习|已练习|已满/.test(t)) return true;
                }
                const text = (task.innerText || '').trim();
                return /已完成|已学习|已练习/.test(text);
            """, chapter))
        except Exception:
            return False

    @staticmethod
    def _scan_course_directory(driver, vendor) -> list[dict]:
        """课程目录页扫描（从原 fluent_ui.py 的 _scan_course_directory 抽取）。"""
        import time

        tabs: list[dict] = []
        try:
            unit_container = vendor.WebDriverWait(driver, 5).until(
                vendor.EC.presence_of_element_located(
                    (vendor.By.CLASS_NAME,
                     "unipus-tabs_unitTabScrollContainer__fXBxR"))
            )
            unit_tabs = unit_container.find_elements(
                vendor.By.CSS_SELECTOR, ":scope > *")
            vendor.gui_log_queue.put(f"课程目录页，找到 {len(unit_tabs)} 个 Unit")
            for unit_idx, unit_tab in enumerate(unit_tabs):
                name_counts: dict[str, int] = {}
                try:
                    driver.execute_script("arguments[0].click();", unit_tab)
                except Exception:
                    unit_tab.click()
                time.sleep(0.8)
                chapters = driver.find_elements(
                    vendor.By.CSS_SELECTOR,
                    '[class*="courses-unit_taskItemInnerLayout__"],'
                    ' [class*="courses-unit_nodeItemInnerLayout__"]')
                for chapter in chapters:
                    try:
                        name_elem = chapter.find_element(
                            vendor.By.CSS_SELECTOR,
                            '[class*="courses-unit_taskTypeName__"],'
                            ' [class*="courses-unit_nodeTypeName__"]')
                        name = name_elem.text.strip()
                        if not name:
                            continue
                        try:
                            chapter.find_element(
                                vendor.By.CSS_SELECTOR,
                                '[class*="courses-unit_taskRequireIcon__"],'
                                ' [class*="courses-unit_nodeRequireIcon__"]')
                            is_compulsory = True
                        except vendor.NoSuchElementException:
                            is_compulsory = False
                        prefix = "[必修]" if is_compulsory else "[选修]"
                        finished = UnipusRunner._task_finished(driver, chapter)
                        if finished:
                            prefix = "[已完成]" + prefix
                        section_title = UnipusRunner._extract_course_task_section(
                            driver, chapter)
                        name_occurrence = name_counts.get(name, 0)
                        name_counts[name] = name_occurrence + 1
                        section_part = (f" - {section_title}" if section_title
                                        else f" - #{name_occurrence + 1}")
                        tabs.append({
                            "l1_idx": len(tabs), "l2_idx": -1,
                            "l1_title": name, "l2_title": section_title,
                            "display": f"{prefix} Unit{unit_idx + 1}"
                                       f"{section_part} - {name}",
                            "is_l2": False, "is_compulsory": is_compulsory,
                            "is_finished": finished,
                            "_element": name_elem, "_unit_idx": unit_idx,
                            "_section_title": section_title,
                            "_name_occurrence": name_occurrence})
                    except Exception:
                        continue
        except Exception as e:
            vendor.logger.debug(f"课程目录扫描未命中: {e}")
        return tabs

    @staticmethod
    def _extract_course_task_section(driver, chapter) -> str:
        """课程目录任务的前置 section 标题（原 fluent_ui.py 逻辑原样抽取）。"""
        try:
            section = driver.execute_script("""
                const task = arguments[0];
                const clean = (text) => (text || '').replace(/\\s+/g, ' ').trim();
                const root = task.closest('[class*="courses-unit"]') || task.parentElement;
                const taskText = clean(task.innerText);
                const sectionWords = /(section\\s*[a-z]|language\\s+focus|reading|listening|speaking|writing|grammar|vocabulary|pronunciation)/i;
                const headingSelectors = ['[class*="section"]', '[class*="title"]', '[class*="name"]', 'h1,h2,h3,h4,h5'];
                const headings = [];
                for (const selector of headingSelectors) {
                    for (const el of root.querySelectorAll(selector)) {
                        const text = clean(el.innerText);
                        if (!text || text === taskText || text.length > 80) continue;
                        if (sectionWords.test(text)) headings.push({ el, text });
                    }
                }
                const taskTop = task.getBoundingClientRect().top;
                const before = headings
                    .filter(item => item.el.getBoundingClientRect().top < taskTop)
                    .sort((a, b) => b.el.getBoundingClientRect().top - a.el.getBoundingClientRect().top)
                    .slice(0, 2)
                    .map(item => item.text)
                    .reverse();
                return [...new Set(before)].join(' / ');
            """, chapter)
            return (section or "").strip()
        except Exception:
            return ""

    # ---- 处理 ----
    def _tab_views(self) -> list[dict]:
        """跨信号的任务视图：index/display/is_compulsory/is_finished。
        WebElement（_element）永远不出线程。"""
        return [{
            "index": i,
            "display": t.get("display", ""),
            "is_compulsory": bool(t.get("is_compulsory", False)),
            "is_finished": bool(t.get("is_finished", False)),
        } for i, t in enumerate(self._tabs)]

    def selected_tabs(self, indices) -> list[dict]:
        return [self._tabs[i] for i in indices if 0 <= i < len(self._tabs)]

    def process(self, indices):
        if not self.connected or self._phase not in ("scanned", "ready"):
            return
        tabs = self.selected_tabs(indices)
        if not tabs:
            self._log("还没有可处理的任务：请先扫描任务列表并勾选。")
            return
        self._set_phase("running")
        self._solver.clear_stop()
        threading.Thread(
            target=self._process_thread, args=(tabs,), daemon=True,
            name=f"unipus-process-{safe_suffix(self.acc_id)}").start()

    def _process_thread(self, tabs: list[dict]):
        solver = self._solver
        generation = self._generation
        if solver is None:
            return
        try:
            chapter = tabs[0].get("l1_title", "") or "selected_chapter"
            self._log(f"开始处理选中的 {len(tabs)} 个任务…")
            result = solver.process_selected_tabs(tabs, chapter)
            # 只有明确的 (done, failed) 返回值才统计成功
            if isinstance(result, tuple) and len(result) == 2:
                done, failed = int(result[0] or 0), list(result[1] or [])
            else:
                done, failed = 0, [t.get('display', '未知任务') for t in tabs]
            if generation != self._generation:
                return
            if done < len(tabs) and not failed:
                failed = ['部分任务未确认完成']
            if solver._should_stop():
                self._log("批量处理已停止。")
                self.sig_done.emit("process", "stopped")
            else:
                self._processed += int(done or 0)
                if failed:
                    self._log(f"批量处理完成：成功 {done} 个，"
                              f"未完成 {len(failed)} 个（每个只做一遍，不重试）：")
                    for name in failed[:10]:
                        self._log(f"  · {name}")
                    if len(failed) > 10:
                        self._log(f"  · …等共 {len(failed)} 个")
                    self._log("未完成的任务下次可单独勾选重跑。")
                else:
                    self._log(f"批量处理完成：{done} 个任务全部成功。")
                self.sig_done.emit("process", "ok" if not failed and done == len(tabs) else "error")
        except Exception as e:
            self._log(f"批量处理失败：{e}")
            self.sig_done.emit("process", "error")
        finally:
            if generation == self._generation and self.connected:
                self._set_phase("ready")

    def quick(self):
        if not self.connected or self._phase not in ("ready", "scanned"):
            return
        self._set_phase("running")
        self._solver.clear_stop()
        threading.Thread(
            target=self._quick_thread, daemon=True,
            name=f"unipus-quick-{safe_suffix(self.acc_id)}").start()

    def _quick_thread(self):
        solver = self._solver
        generation = self._generation
        if solver is None:
            return
        try:
            ok = bool(solver.solve_current_page("当前页"))
            if generation != self._generation:
                return
            if ok:
                self._processed += 1
                self._log("当前页处理完成。")
                self.sig_done.emit("quick", "ok")
            else:
                self._log("当前页处理未完成（可能没有可自动作答的题目）。")
                self.sig_done.emit("quick", "error")
        except Exception as e:
            self._log(f"快速处理失败：{e}")
            self.sig_done.emit("quick", "error")
        finally:
            if generation == self._generation and self.connected:
                self._set_phase("ready")

    def stop(self):
        if self._solver:
            self._solver.request_stop()
            self._log("已请求停止，等待当前步骤收尾…")

    # ---- 断开 / 退出清理 ----
    def shutdown(self):
        """关闭浏览器、停泵线程并回到 idle（窗口退出时也调用）。"""
        self._pump_stop.set()
        with self._lock:
            self._generation += 1
            bot, solver = self._bot, self._solver
            self._bot = None
            self._solver = None
            self._tabs = []
            self._pump_thread = None
        if solver is not None:
            solver.request_stop()
        if bot is not None and getattr(bot, "driver", None):
            try:
                bot.driver.quit()
            except Exception:
                pass
        self._set_phase("idle")


# ------------------------------------------------------------------ runner 注册表
_runners: dict[str, UnipusRunner] = {}
_runners_lock = threading.Lock()


def get_runner(acc_id: str = "") -> UnipusRunner:
    """取（必要时创建）某个账号的 runner；acc_id 为空时取当前账号。

    每个账号一个实例 = 每个账号一个独立的 Edge 会话。
    """
    st = account_store()
    aid = str(acc_id or st.active or "default")
    with _runners_lock:
        runner = _runners.get(aid)
        if runner is None:
            runner = UnipusRunner(aid)
            _runners[aid] = runner
        return runner


def all_runners() -> dict[str, UnipusRunner]:
    """当前进程里所有账号的 runner（副本，随便遍历）。"""
    with _runners_lock:
        return dict(_runners)


def drop_runner(acc_id: str) -> None:
    """删除账号时调用：关掉它的浏览器并把它从注册表里摘掉。"""
    with _runners_lock:
        runner = _runners.pop(str(acc_id), None)
    if runner is not None:
        try:
            runner.shutdown()
        except Exception:
            pass


def shutdown_all() -> None:
    """主窗口退出时调用：逐个关闭所有账号的浏览器与泵线程。"""
    for runner in list(all_runners().values()):
        try:
            runner.shutdown()
        except Exception:
            pass
