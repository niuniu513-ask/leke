# -*- coding: utf-8 -*-
"""后台任务执行器（多账号）。

线程模型：所有重活跑在 threading.Thread 里，通过 Qt Signal 回主线程更新界面
（PySide6 从非 GUI 线程 emit 会自动排队到接收者所在线程，安全）。

多账号：每个账号一份独立会话（各自一个 cookie 文件、一个 Chaoxing 实例、
一个停止事件），因此可以同时登录多个账号、并行刷课而不会串号。
为此**所有信号的第一个参数都是 acc_id**，界面按账号路由。

与 main.py 的关系：复用 main.process_chapter / ChapterTask 等核心逻辑，
但调度器自己实现，以便支持「停止」——原版 JobProcessor 一旦开跑无法中断。
"""
from __future__ import annotations

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal

from api.logger import logger


def _friendly(e: BaseException) -> str:
    """异常转界面文案：网络类给中文提示，其余保留「类型: 信息」。

    延迟导入 api.base（它比较重，且启动顺序上要等 stream.install 完成）。
    """
    try:
        from api.base import friendly_error

        return friendly_error(e)
    except Exception:
        return f"{type(e).__name__}: {e}"


class ChapterResult:
    SUCCESS = "success"
    ERROR = "error"
    NOT_OPEN = "not_open"
    PENDING = "pending"


class ChapterTask:
    """与 main.ChapterTask 等价（避免 import main 时把 argparse 一起拖进来）。"""

    __slots__ = ("index", "point", "course", "result", "tries", "already_finished")

    def __init__(self, index: int, point: dict, course: dict):
        self.index = index
        self.point = point
        self.course = course
        self.result = ChapterResult.PENDING
        self.tries = 0
        # 收集任务时就已完成的章节：后续即使返回 SUCCESS 也不算「本遍有进展」，
        # 否则「重刷已完成章节」的场景会让续刷永远停不下来。
        self.already_finished = bool(point.get("has_finished"))

    def __lt__(self, other):
        return self.index < other.index


#: 兜底上限：极端情况下（例如服务端一直不回写完成状态）防止无限续刷
MAX_PASSES = 20

#: 连续这么多遍都没有新完成的章节就收手 —— 说明剩下的确实刷不动了
NO_PROGRESS_PASSES = 2

#: 同一章节连续这么多遍处理失败就熔断放弃 —— 答错的章节每遍重撞是
#: 「一直刷停不下来」的主要体感来源（视频能过 → 系统认为有进展 →
#: 但测验章节永远过不了 → 被反复重做）。放弃后其余章节照常推进。
GIVE_UP_AFTER = 3

#: 两遍之间停顿几秒，让服务端的完成状态先落库，也顺便降低风控概率
PASS_GAP = 3.0


class AccountSession:
    """单个账号的运行期状态（会话、停止事件、刷课标记）。"""

    __slots__ = ("acc_id", "name", "chaoxing", "realname", "stop_event",
                 "studying", "logging", "cookie_path")

    def __init__(self, acc_id: str, name: str = ""):
        self.acc_id = str(acc_id)
        self.name = name or str(acc_id)
        self.chaoxing = None
        self.realname = ""
        self.stop_event = threading.Event()
        self.studying = False
        self.logging = False
        self.cookie_path = ""

    @property
    def logged_in(self) -> bool:
        return self.chaoxing is not None


class Runner(QObject):
    """登录 / 拉课程 / 刷课 三类后台作业的统一入口（多账号）。

    为了向后兼容旧调用，所有方法都保留 ``acc_id`` 作为第一个参数；
    不传时使用 ``DEFAULT_ACC``（单账号场景 = 老行为）。
    """

    sig_chapters_versioned = Signal(str, str, list, str, str)
    # ---- 所有信号第一个参数都是 acc_id，界面按账号路由 ----
    sig_login = Signal(str, bool, str, str)          # acc_id, ok, msg, realname
    sig_courses = Signal(str, list)                  # acc_id, 基础课程列表
    sig_chapters = Signal(str, str, list, str)       # acc_id, courseId, 章节点列表, 错误(""=成功)
    sig_course_progress = Signal(str, int, object)   # acc_id, 行号, {"done":n,"total":n,"error":str|None}
    sig_study_total = Signal(str, int)               # acc_id, 本遍发现的待处理章节总数
    sig_study_pass = Signal(str, int)                # acc_id, 当前是第几遍检查
    sig_study_update = Signal(str, int, int, str)    # acc_id, 本遍已处理, 失败数, 当前章节
    sig_study_current = Signal(str, object)          # acc_id, dict: 正在处理的课程/章节/任务点
    sig_video_progress = Signal(str, object)         # acc_id, dict: 视频播放实时进度（心跳每秒一帧）
    sig_study_totals = Signal(str, object)           # acc_id, dict: 累计 {"total_done":n,"failed":n,"pass":n}
    sig_state = Signal(str, str)                     # acc_id, idle / logging / courses / studying / stopping
    sig_status = Signal(str, str)                    # acc_id, 状态栏文案
    sig_finished = Signal(str, str, str, str)        # acc_id, kind(login|courses|study), status, msg

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sessions: dict[str, AccountSession] = {}
        self._lock = threading.Lock()
        self._course_reads: set[str] = set()
        self._course_refresh_pending: set[str] = set()
        # 多账号共享的题库实例（v1.5.1f）：本地知识库命中率高、AI 连接只建一份
        self._shared_tiku = None
        self._shared_tiku_fp = ""
        self._tiku_lock = threading.Lock()

    def _get_shared_tiku(self, tiku_conf: dict):
        """多账号共享同一个题库实例。

        - 云端题库由服务器持久化共享，谁先答过的题其他账号直接命中，
          不重复消耗 AI token，也不再向远端题库重复查询；
        - AI / SiliconFlow 的连接与配置只初始化一份；
        - 配置（provider / 密钥 / 模型）变化时自动重建，保证设置页改完即生效。
        """
        import json as _json
        with self._tiku_lock:
            fp = _json.dumps(tiku_conf or {}, ensure_ascii=False, sort_keys=True,
                             default=str)
            if self._shared_tiku is None or self._shared_tiku_fp != fp:
                from api.answer import Tiku
                from gui_app.store import config_path
                tiku = Tiku.get_tiku_from_config(tiku_conf or None,
                                                 config_path=config_path())
                tiku.init_tiku()
                self._shared_tiku = tiku
                self._shared_tiku_fp = fp
                logger.info("题库实例已创建（多账号共享）: "
                            f"{getattr(tiku, 'name', type(tiku).__name__)}")
            return self._shared_tiku

    # ------------------------------------------------------------------ 会话
    def session(self, acc_id: str = "", name: str = "",
                cookie_path: str = "") -> AccountSession:
        """取（或建）某个账号的运行期状态。"""
        key = str(acc_id or "default")
        with self._lock:
            st = self._sessions.get(key)
            if st is None:
                st = AccountSession(key, name)
                self._sessions[key] = st
            if name:
                st.name = name
            if cookie_path:
                st.cookie_path = cookie_path
            return st

    def sessions(self) -> list[AccountSession]:
        with self._lock:
            return list(self._sessions.values())

    def is_studying(self, acc_id: str) -> bool:
        st = self._sessions.get(str(acc_id or "default"))
        return bool(st and st.studying)

    def is_logged_in(self, acc_id: str) -> bool:
        st = self._sessions.get(str(acc_id or "default"))
        return bool(st and st.logged_in)

    @property
    def studying(self) -> bool:
        """是否有任一账号在刷课（兼容旧调用）。"""
        return any(s.studying for s in self.sessions())

    # ------------------------------------------------------------------ 停止
    def request_stop(self, acc_id: str = ""):
        """请求停止。acc_id 为空 = 停止全部账号。"""
        if not acc_id:
            targets = [s for s in self.sessions() if s.studying]
            for st in targets:
                st.stop_event.set()
                self.sig_state.emit(st.acc_id, "stopping")
                self.sig_status.emit(st.acc_id, "正在停止…当前任务点会先收尾，请稍候")
            return
        st = self.session(acc_id)
        st.stop_event.set()
        self.sig_state.emit(st.acc_id, "stopping")
        self.sig_status.emit(st.acc_id, "正在停止…当前任务点会先收尾，请稍候")

    # ------------------------------------------------------------------ 登录
    def login(self, acc_id: str, payload: dict):
        """登录一个账号。payload 至少要有 username / password / use_cookies。"""
        payload = payload or {}
        st = self.session(acc_id, str(payload.get("name") or ""),
                          str(payload.get("cookie_path") or ""))
        username = str(payload.get("username") or "")
        password = str(payload.get("password") or "")
        use_cookies = bool(payload.get("use_cookies", False))
        cookie_path = str(payload.get("cookie_path") or "") or None

        st.logging = True
        self.sig_state.emit(st.acc_id, "logging")
        self.sig_status.emit(st.acc_id, f"{st.name}：正在登录…")

        def job():
            try:
                from api.base import Account, Chaoxing

                cx = Chaoxing(account=Account(username, password),
                              session_key=st.acc_id, cookie_path=cookie_path)
                result = cx.login(login_with_cookies=use_cookies)
                ok = bool(result.get("status"))
                msg = str(result.get("msg") or "")
                name = ""
                if ok:
                    st.chaoxing = cx
                    try:
                        name = cx.get_name() or ""
                    except Exception:
                        name = ""
                    st.realname = name
                else:
                    st.chaoxing = None
                    # 丢掉脏会话，下次重登从干净的 cookie 文件重建
                    try:
                        from api.base import SessionManager
                        SessionManager.drop_session(st.acc_id)
                    except Exception:
                        pass
                self.sig_login.emit(st.acc_id, ok, msg, name)
                self.sig_finished.emit(st.acc_id, "login", "ok" if ok else "error", msg)
            except BaseException as e:
                msg = _friendly(e)
                logger.error(f"[{st.acc_id}] 登录异常: {type(e).__name__}: {e}")
                self.sig_login.emit(st.acc_id, False, msg, "")
                self.sig_finished.emit(st.acc_id, "login", "error", msg)
            finally:
                st.logging = False
                self.sig_state.emit(st.acc_id, "idle")

        threading.Thread(target=job, name=f"login-{st.acc_id}", daemon=True).start()

    def logout(self, acc_id: str):
        st = self.session(acc_id)
        st.chaoxing = None
        st.realname = ""
        try:
            from api.base import SessionManager

            SessionManager.drop_session(st.acc_id)
        except Exception:
            pass
        self.sig_state.emit(st.acc_id, "idle")
        self.sig_status.emit(st.acc_id, f"{st.name}：已退出登录")

    # ------------------------------------------------------------ 课程 + 进度
    def load_chapters(self, acc_id: str, course: dict):
        """读取一门课程的章节列表（异步，结果走 sig_chapters）。"""
        def emit_result(points, error):
            cid = str(course.get('courseId', ''))
            self.sig_chapters.emit(acc_id, cid, points, error)
            self.sig_chapters_versioned.emit(acc_id, cid, points, error, str(course.get('_chapter_request', '')))
        st = self.session(acc_id)
        if st.chaoxing is None:
            emit_result([], '尚未登录')
            return
        course_id = str(course.get("courseId", ""))
        self.sig_status.emit(acc_id, f"{st.name}：正在读取章节：{course.get('title', '')}")

        def job():
            try:
                pl = st.chaoxing.get_course_point(
                    course["courseId"], course["clazzId"], course.get("cpi"))
                points = list((pl or {}).get("points") or [])
                emit_result(points, '')
                self.sig_status.emit(acc_id, f"{st.name}：章节读取完成（{len(points)} 个）")
            except BaseException as e:
                logger.error(f"[{acc_id}] 读取章节失败: {type(e).__name__}: {e}")
                emit_result([], _friendly(e))

        threading.Thread(target=job, name=f"chapters-{acc_id}", daemon=True).start()

    def load_courses(self, acc_id: str, with_progress: bool = True, workers: int = 4,
                     force_refresh: bool = False):
        st = self.session(acc_id)
        if st.chaoxing is None:
            self.sig_finished.emit(st.acc_id, "courses", "error", "尚未登录")
            return
        with self._lock:
            if st.acc_id in self._course_reads:
                if force_refresh:
                    self._course_refresh_pending.add(st.acc_id)
                return
            self._course_reads.add(st.acc_id)
        self.sig_state.emit(st.acc_id, "courses")
        self.sig_status.emit(st.acc_id, f"{st.name}：正在读取课程列表…")

        def job():
            try:
                courses = st.chaoxing.get_course_list() or []
                self.sig_courses.emit(st.acc_id, list(courses))
                self.sig_status.emit(
                    st.acc_id, f"{st.name}：课程列表读取完成，共 {len(courses)} 门")

                if not with_progress or not courses:
                    self.sig_finished.emit(st.acc_id, "courses", "ok", f"{len(courses)} 门")
                    return

                self.sig_status.emit(
                    st.acc_id, f"{st.name}：正在读取章节进度 (0/{len(courses)})…")

                def probe(idx_course):
                    idx, c = idx_course
                    try:
                        pl = st.chaoxing.get_course_point(
                            c["courseId"], c["clazzId"], c.get("cpi")
                        )
                        pts = pl['points']
                        if not isinstance(pts, list) or not pts:
                            raise ValueError('未读到可核对章节，完成状态未确认')
                        done = sum(1 for p in pts if p.get("has_finished"))
                        info = {"done": done, "total": len(pts), "error": None}
                    except BaseException as e:
                        info = {"done": 0, "total": 0, "error": _friendly(e)}
                    info.update(course_id=str(c['courseId']), clazz_id=str(c['clazzId']))
                    return idx, info

                done_cnt = 0
                with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
                    for idx, info in pool.map(probe, list(enumerate(courses))):
                        done_cnt += 1
                        self.sig_course_progress.emit(st.acc_id, idx, info)
                        if done_cnt % 2 == 0 or done_cnt == len(courses):
                            self.sig_status.emit(
                                st.acc_id,
                                f"{st.name}：正在读取章节进度 ({done_cnt}/{len(courses)})…")

                self.sig_finished.emit(st.acc_id, "courses", "ok", f"{len(courses)} 门")
            except BaseException as e:
                logger.error(f"[{st.acc_id}] 读取课程失败: {type(e).__name__}: {e}")
                self.sig_finished.emit(st.acc_id, "courses", "error", _friendly(e))
            finally:
                with self._lock:
                    self._course_reads.discard(st.acc_id)
                    refresh_again = st.acc_id in self._course_refresh_pending
                    self._course_refresh_pending.discard(st.acc_id)
                self.sig_state.emit(st.acc_id, "idle")
                if refresh_again:
                    self.load_courses(st.acc_id, True, workers)

        threading.Thread(target=job, name=f"courses-{st.acc_id}", daemon=True).start()

    # ------------------------------------------------------------------ 刷课
    def _collect_tasks(self, st: AccountSession, course_list: list[dict],
                       skip_finished: bool, give_up: set | None = None,
                       deferred_keys: set | None = None) -> list:
        """向服务端拉取章节列表并生成任务。

        每一遍都要重新拉一次 —— 前面章节完成后，后面「需解锁」的章节才会开放，
        用旧列表永远刷不到它们（这就是「只能刷一遍」的根因之一）。
        give_up: 已熔断放弃的章节键（courseId:pointId），收集时直接跳过。
        """
        tasks = []
        give_up = give_up or set()
        for course in course_list:
            if st.stop_event.is_set():
                return tasks
            # 课程级章节选择：{"selected_points": [pointId, …]}；缺省 = 全部章节
            only_points = course.get("selected_points") or None
            self.sig_status.emit(
                st.acc_id, f"{st.name}：正在读取章节: {course.get('title', '')}")
            pl = st.chaoxing.get_course_point(course["courseId"], course["clazzId"],
                                              course.get("cpi"))
            for i, point in enumerate((pl or {}).get("points") or []):
                if only_points and str(point.get("id")) not in only_points:
                    continue
                if skip_finished and point.get("has_finished"):
                    continue
                key = f"{course.get('courseId')}:{point.get('id')}"
                if key in (deferred_keys or set()):
                    logger.warning('章节存在草稿、未通过或回执待核对，本次不重复发送')
                    continue
                if key in give_up:
                    logger.warning(
                        f"[{st.acc_id}] 跳过熔断章节（连续 {GIVE_UP_AFTER} 遍失败已放弃）: "
                        f"{point.get('title', '')}（course={course.get('title', '')}）")
                    continue
                tasks.append(ChapterTask(i, point, course))
        return tasks

    def _make_job_reporter(self, acc_id: str, pass_no: int, plan: int):
        """构造任务点级回调：把「正在处理哪个视频」实时送到界面。"""
        def report(course, point, job, index, total):
            from gui_app.study_engine import describe_job

            label, name = describe_job(job)
            self.sig_study_current.emit(acc_id, {
                "pass": int(pass_no),
                "plan": int(plan),
                "course": str(course.get("title", "") or ""),
                "chapter": str(point.get("title", "") or ""),
                "job": name,
                "job_type": label,
                "job_index": int(index),
                "job_total": int(total),
            })
        return report

    def start_study(self, acc_id: str, course_list: list[dict], common: dict,
                    tiku_conf: dict):
        st = self.session(acc_id)
        if st.chaoxing is None:
            self.sig_finished.emit(st.acc_id, "study", "error", "尚未登录")
            return
        if st.studying:
            self.sig_finished.emit(st.acc_id, "study", "error", "该账号已有任务在运行")
            return

        st.stop_event.clear()
        st.studying = True
        self.sig_state.emit(st.acc_id, "studying")
        self.sig_status.emit(st.acc_id, f"{st.name}：正在准备章节列表…")

        def job():
            status = "ok"
            message = ""
            try:
                from api.answer import Tiku
                from gui_app import study_engine
                from gui_app.store import config_path

                # 1) 题库（多账号共享同一实例 —— 见 _get_shared_tiku）
                tiku = None
                try:
                    tiku = self._get_shared_tiku(tiku_conf)
                except BaseException as e:
                    logger.error(f"[{st.acc_id}] 题库初始化失败，将以无题库模式运行: {e}")
                    tiku = None

                cx = st.chaoxing
                cx.tiku = tiku
                # 视频播放实时进度：study_video 心跳循环每秒回调一次，
                # 直接转发成界面信号 → 运行页该账号卡片的播放进度条（v1.5.1e）
                def _on_video_progress(info: dict, _aid=st.acc_id):
                    self.sig_video_progress.emit(_aid, info)
                cx.video_progress_cb = _on_video_progress
                cx.kwargs.update({
                    "query_delay": float(tiku_conf.get("delay", 0) or 0),
                    "work_max_retries": int(common.get("work_max_retries", 3) or 3),
                    "work_redo_enabled": bool(common.get("work_redo_enabled", False)),
                })

                # 大模型连通性检查（不阻塞界面，只提示）
                provider = str(tiku_conf.get("provider", "") or "")
                if tiku is not None and any(
                        n.strip() in ("AI", "SiliconFlow") for n in provider.split(",")):
                    if str(tiku_conf.get("check_llm_connection", "true")).lower() == "true":
                        self.sig_status.emit(st.acc_id, f"{st.name}：正在校验大模型连接…")
                        try:
                            if not tiku.check_llm_connection():
                                logger.warning(
                                    f"[{st.acc_id}] 大模型连接检查失败，仍继续运行")
                        except BaseException as e:
                            logger.warning(f"[{st.acc_id}] 大模型连接检查异常，已忽略: {e}")

                # 2) 续刷循环
                #    学习通是「章节测验通过 / 前置任务点完成才解锁下一章」的链式结构，
                #    而章节列表是在开跑前一次性读出来的。所以一遍跑完之后，那些当时
                #    还没开放的章节就再也没人管 —— 表现就是「刷课只能刷一遍」。
                #    修法：每一遍都重新读一次章节列表，把新解锁的章节捞回来继续处理，
                #    直到连续若干遍彻底没有新完成的章节为止。
                skip_finished = bool(common.get("skip_finished", True))
                logger.info(
                    f"[{st.acc_id}] 刷课方式: 自动续刷（每遍重新读取章节列表，"
                    f"连续 {NO_PROGRESS_PASSES} 遍无新进展即停，兜底上限 {MAX_PASSES} 遍）")

                total_done = 0
                failed_all = 0
                pass_no = 0
                empty_streak = 0
                prev_keys: set | None = None   # 上一遍收集到的章节键集合
                nothing_to_do = False
                give_up: set = set()          # 已熔断放弃的章节键
                deferred_keys: set = set()
                fail_streak: dict = {}        # 章节键 -> 连续失败遍数
                self.sig_study_totals.emit(st.acc_id,
                                           {"total_done": 0, "failed": 0, "pass": 0})

                while True:
                    if st.stop_event.is_set():
                        status = "stopped"
                        break

                    pass_no += 1
                    self.sig_study_pass.emit(st.acc_id, pass_no)
                    prefix = f"第 {pass_no} 遍：" if pass_no > 1 else ""
                    if pass_no > 1:
                        logger.info(f"[{st.acc_id}] ===== 第 {pass_no} 遍检查开始 =====")
                    self.sig_status.emit(st.acc_id, f"{st.name}：{prefix}正在读取章节列表…")

                    tasks = self._collect_tasks(st, course_list, skip_finished,
                                                give_up=give_up, deferred_keys=deferred_keys)
                    if st.stop_event.is_set():
                        status = "stopped"
                        break
                    if not tasks:
                        if pass_no == 1:
                            nothing_to_do = True
                            logger.info(f"[{st.acc_id}] 所选课程没有需要处理的任务点")
                        else:
                            logger.info(f"[{st.acc_id}] 第 {pass_no} 遍检查：已无待处理章节，结束")
                        break

                    self.sig_study_total.emit(st.acc_id, len(tasks))
                    self.sig_status.emit(
                        st.acc_id, f"{st.name}：{prefix}开始处理 {len(tasks)} 个章节")

                    scheduler = study_engine.StudyScheduler(
                        chaoxing=cx,
                        tasks=tasks,
                        config=common,
                        stop_event=st.stop_event,
                        on_start=lambda t: self.sig_status.emit(
                            st.acc_id, f"{st.name}：正在处理: {t.point.get('title', '')}"),
                        on_done=lambda done, failed, title: self.sig_study_update.emit(
                            st.acc_id, done, failed, title),
                        on_job=self._make_job_reporter(st.acc_id, pass_no, len(tasks)),
                    )
                    scheduler.run()
                    for task in scheduler.deferred:
                        deferred_keys.add(f"{task.course.get('courseId')}:{task.point.get('id')}")

                    # 逐章节累计连续失败遍数；连续 GIVE_UP_AFTER 遍失败即熔断。
                    # 注意本章在下一遍服务端回写完成就自动退出熔断计数
                    # （收集时已完成章节直接跳过，不会进 failed）。
                    failed_titles: dict = {}
                    for t in scheduler.failed:
                        key = f"{t.course.get('courseId')}:{t.point.get('id')}"
                        failed_titles[key] = t.point.get("title", "")
                    gave_up_now = 0
                    for key, title in failed_titles.items():
                        fail_streak[key] = fail_streak.get(key, 0) + 1
                        if fail_streak[key] >= GIVE_UP_AFTER and key not in give_up:
                            give_up.add(key)
                            gave_up_now += 1
                            logger.warning(
                                f"[{st.acc_id}] 章节「{title}」"
                                f"连续 {GIVE_UP_AFTER} 遍失败，熔断放弃"
                                f"（多半是题库无答案且答不对，建议配置 AI 兜底后重试）")
                    for key in list(fail_streak):
                        if key not in failed_titles:
                            # 本遍没碰它（被熔断跳过）或已通过 —— 通过时清零
                            fail_streak[key] = 0

                    total_done += scheduler.success_count
                    failed_all += len(scheduler.failed)
                    self.sig_study_totals.emit(st.acc_id, {
                        "total_done": total_done,
                        "failed": failed_all,
                        "pass": pass_no,
                    })

                    if st.stop_event.is_set():
                        status = "stopped"
                        break

                    # 从头重刷：收集时就是全量章节（含已完成），不存在「新解锁」要捞，
                    # 一遍把全部内容重新处理完就达成补时长的目的 —— 跑第二遍只会
                    # 把同样的视频再全量重播一次，纯属双倍耗时。
                    if not skip_finished:
                        logger.info(
                            f"[{st.acc_id}] 从头重刷模式：全部章节已重新处理一遍，结束"
                            f"（共 {scheduler.total} 个章节，失败 {len(scheduler.failed)} 个；"
                            f"新完成 {scheduler.success_count} 个不计入重播）")
                        break

                    # 进展判定看「服务端章节集合是否真的变化」——
                    # 某个章节被服务端回写完成后，它就会从下一遍的收集里消失，
                    # 集合随之变化；反之若本遍号称成功、集合却原封不动，说明
                    # 全是白干（视频刷了没计时长 / 测验答错没标完成），必须停下。
                    # 不能再靠 success_count：那种「章节重复处理、每遍都算成功」
                    # 的场景会让它每遍 >0，把 empty_streak 一直清零 ——
                    # 这正是「一直在多遍循环刷」的根因。
                    cur_keys = {f"{t.course.get('courseId')}:{t.point.get('id')}"
                                for t in tasks}
                    if (prev_keys is not None and cur_keys == prev_keys
                            and not gave_up_now):
                        empty_streak += 1
                    else:
                        empty_streak = 0
                    prev_keys = cur_keys
                    if empty_streak >= NO_PROGRESS_PASSES:
                        logger.info(
                            f"[{st.acc_id}] 连续 {NO_PROGRESS_PASSES} 遍章节集合无变化"
                            f"（没有任何章节被服务端标记完成），续刷结束")
                        break
                    if pass_no >= MAX_PASSES:
                        logger.warning(f"[{st.acc_id}] 续刷已达兜底上限 {MAX_PASSES} 遍，停止")
                        break

                    logger.info(
                        f"[{st.acc_id}] 第 {pass_no} 遍结束"
                        f"（新完成 {scheduler.success_count} 个章节），"
                        f"{PASS_GAP:.0f} 秒后重新检查章节列表…"
                    )
                    self.sig_status.emit(
                        st.acc_id,
                        f"{st.name}：第 {pass_no} 遍结束，{PASS_GAP:.0f} 秒后重新检查章节列表…")
                    if st.stop_event.wait(PASS_GAP):
                        status = "stopped"
                        break

                # A locally exhausted queue is not server-confirmed completion.
                verification = None
                if status != 'stopped':
                    verification = self.verify_course_completion(st, course_list)
                    if verification['errors']:
                        status = 'unverified'
                    elif verification['remaining'] or verification['total'] == 0:
                        status = 'incomplete'

                # 3) 结果文案
                if status == "stopped":
                    message = f"{st.name}：已停止，共完成 {total_done} 个章节（中断于第 {pass_no} 遍）"
                    logger.warning(message)
                elif nothing_to_do:
                    message = f"{st.name}：没有需要处理的任务点"
                else:
                    message = f"{st.name}：共检查 {pass_no} 遍，完成 {total_done} 个章节"
                    if failed_all:
                        message += f"，失败 {failed_all} 个"
                    logger.info(message)
                if verification is not None:
                    message = (f"{st.name}：本地流程已结束；平台确认完成 "
                               f"{verification['done']}/{verification['total']} 个所选章节，"
                               f"剩余 {verification['remaining']} 个")
                    if verification['errors']:
                        message += f"；{verification['errors']} 门课程读取失败，完成状态未确认"
                    elif not verification['total']:
                        message += '；未读到可核对章节，不能确认全部完成'
                    if deferred_keys:
                        message += f'；{len(deferred_keys)} 个章节存在草稿、未通过或回执待核对'

                # 4) 章节学习次数（可选的收尾动作）
                if (status != "stopped"
                        and bool(common.get("add_learning_count", False))):
                    from api.process import increase_learning_count_for_course
                    self.sig_status.emit(st.acc_id, f"{st.name}：正在增加章节学习次数…")
                    for course in course_list:
                        if st.stop_event.is_set():
                            break
                        try:
                            increase_learning_count_for_course(cx, course, common)
                        except BaseException as e:
                            logger.error(f"[{st.acc_id}] 增加章节学习次数失败: {e}")

            except KeyboardInterrupt:
                status = "stopped"
                message = f"{st.name}：已停止"
            except BaseException as e:
                status = "error"
                message = f"{st.name}：{_friendly(e)}"
                logger.error(f"[{st.acc_id}] 刷课异常: {type(e).__name__}: {e}")
                logger.error(traceback.format_exc())
            finally:
                st.studying = False
                self.sig_state.emit(st.acc_id, "idle")
                self.sig_finished.emit(st.acc_id, "study", status, message)

        threading.Thread(target=job, name=f"study-{st.acc_id}", daemon=True).start()

    def verify_course_completion(self, st, courses):
        """Read-only final reconciliation, including chapters excluded by circuit breaking."""
        result = dict(done=0, total=0, remaining=0, errors=0)
        for course in courses:
            try:
                data = st.chaoxing.get_course_point(course['courseId'], course['clazzId'], course.get('cpi'))
                points = data['points']
                selected = course.get('selected_points')
                if selected:
                    wanted = {str(value) for value in selected}
                    points = [p for p in points if str(p.get('id')) in wanted]
                    if {str(p.get('id')) for p in points} != wanted:
                        raise ValueError('Selected chapters missing from server response')
                total = len(points)
                done = sum(1 for p in points if p.get('has_finished') is True)
                result['total'] += total
                result['done'] += done
                result['remaining'] += total - done
            except Exception:
                result['errors'] += 1
        return result
