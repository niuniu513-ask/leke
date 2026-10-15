# -*- coding: utf-8 -*-
"""可停止的章节调度器。

与 main.JobProcessor 的差异只有两点：
1. 每个循环都检查 stop_event，可以中途退出；
2. 完成判定用显式计数器（finished == total），而不是 queue.unfinished_tasks ——
   后者在「任务失败 -> 进重试队列」的瞬间会短暂归零，主循环可能误判为跑完了。

注意：章节处理结果用的是 main.ChapterResult（Enum），不是字符串。
"""
from __future__ import annotations

import threading
import time
import traceback
from queue import Empty, PriorityQueue

from api.config import GlobalConst as gc
from api.logger import logger


def _load_engine():
    """延迟导入：main 顶层会 import tqdm / api.*，放到真正要跑的时候再引入。"""
    import main as chaoxing_main
    return chaoxing_main.process_chapter, chaoxing_main.ChapterResult


#: 任务点类型 → 界面上的中文标签
JOB_TYPE_LABELS = {
    "video": "视频",
    "audio": "音频",
    "document": "文档",
    "workid": "章节测验",
    "read": "阅读",
    "live": "直播",
}


def describe_job(job: dict) -> tuple[str, str]:
    """把任务点字典变成 (类型中文名, 显示名)。

    超星不同任务类型的字段名不统一：video/live 用 name，read 用 title，
    document/workid 干脆没有名字 —— 这时退回类型名。
    """
    jtype = str(job.get("type", "") or "").lower()
    label = JOB_TYPE_LABELS.get(jtype, jtype or "任务点")
    name = str(job.get("name") or job.get("title") or "").strip()
    if not name:
        name = label
    return label, name


class StudyScheduler:
    def __init__(self, chaoxing, tasks, config: dict, stop_event=None,
                 on_start=None, on_done=None, on_job=None):
        self.chaoxing = chaoxing
        self.tasks = list(tasks)
        self.config = config or {}
        self.stop_event = stop_event or threading.Event()
        self.on_start = on_start
        self.on_done = on_done
        self.on_job = on_job

        self.max_tries = 5
        self.speed = float(self.config.get("speed", 1.0) or 1.0)
        self.retry_interval = float(self.config.get("retry_interval", 1.0) or 1.0)
        self.worker_num = max(1, int(self.config.get("jobs", 4) or 4))
        self.notopen_action = str(self.config.get("notopen_action", "retry") or "retry")
        if self.notopen_action == "ask":
            # 桌面版无法交互询问，按 retry 处理（界面上已有提示）
            self.notopen_action = "retry"
        # 「从头重刷」= 运行页勾选 / 设置页取消「只处理未完成的任务点」。
        # False 时已完成章节也会进 process_chapter，重新走任务点（timed 从头重播补时长）。
        self.skip_finished = bool(self.config.get("skip_finished", True))

        # 视频学习模式 fast / timed —— 「秒过」会让任务点通过但平台不记学习时长，
        # timed 会按真实节奏上报（耗时 ≈ 视频总长 ÷ 倍速）。
        mode = str(self.config.get("video_mode", "fast") or "fast").strip().lower()
        if mode not in ("fast", "timed"):
            mode = "fast"
        self.video_mode = mode
        gc.VIDEO_MODE = mode
        try:
            self.chaoxing.video_mode = mode
            # GUI 下 stderr 被 StreamProxy 接管，tqdm 的 \r 帧会不断重置其
            # 日志/进度通道判定，与多线程日志互相踩（表现为日志缺行）——
            # 通知 study_video 关掉进度条，进度改走日志。
            self.chaoxing.gui_mode = True
        except Exception:
            pass
        logger.info(f"视频学习模式: {mode}")

        self.task_queue: PriorityQueue = PriorityQueue()
        self.retry_queue: PriorityQueue = PriorityQueue()

        self.failed: list = []
        self.deferred: list = []
        self.total = len(self.tasks)
        self._finished = 0
        self._success = 0
        self._lock = threading.Lock()
        self._workers: list[threading.Thread] = []

    # ------------------------------------------------------------------ 计数
    @property
    def done_count(self) -> int:
        with self._lock:
            return self._finished

    @property
    def success_count(self) -> int:
        """本遍真正成功的章节数（failed 不算）。续刷时用它判断「有没有进展」。"""
        with self._lock:
            return self._success

    def _mark_finished(self, task, ok: bool = False):
        with self._lock:
            self._finished += 1
            if ok:
                self._success += 1
            done = self._finished
            failed = len(self.failed)
        if self.on_done:
            try:
                self.on_done(done, failed, task.point.get("title", ""))
            except Exception:
                pass

    def _is_over(self) -> bool:
        with self._lock:
            return self._finished >= self.total

    def _drain(self):
        try:
            while True:
                self.task_queue.get_nowait()
        except Empty:
            pass
        try:
            while True:
                self.retry_queue.get_nowait()
        except Empty:
            pass

    # ------------------------------------------------------------------ worker
    def _worker(self):
        process_chapter, ChapterResult = _load_engine()
        while not self.stop_event.is_set():
            try:
                task = self.task_queue.get(timeout=0.3)
            except Empty:
                if self._is_over():
                    return
                continue

            if self.on_start:
                try:
                    self.on_start(task)
                except Exception:
                    pass

            try:
                task.result = process_chapter(
                    self.chaoxing, task.course, task.point, self.speed,
                    on_job=self.on_job,
                    skip_finished=self.skip_finished,
                )
            except BaseException as e:
                logger.error(
                    f"章节处理异常 {task.course.get('title')} - "
                    f"{task.point.get('title')}: {type(e).__name__}: {e}"
                )
                logger.debug(traceback.format_exc())
                task.result = ChapterResult.ERROR

            if self.stop_event.is_set():
                return

            retry = False
            if task.result == ChapterResult.SUCCESS:
                logger.debug(f"完成: {task.course['title']} - {task.point['title']}")

            elif task.result == ChapterResult.DEFERRED:
                self.deferred.append(task)
                logger.warning('章节仅保存、未通过或提交状态未确认，等待核对，不计入完成')

            elif task.result == ChapterResult.NOT_OPEN:
                if self.notopen_action == "continue":
                    logger.warning(
                        f"章节未开启，跳过: {task.course['title']} - {task.point['title']}"
                    )
                else:
                    task.tries += 1
                    if task.tries >= self.max_tries:
                        logger.error(
                            f"章节未开启: {task.course['title']} - {task.point['title']}。"
                            "可能因上一章节的章节检测未完成，或该章节已关闭 —— "
                            "请手动处理后再重试。"
                        )
                        self.failed.append(task)
                    else:
                        retry = True

            elif task.result == ChapterResult.ERROR:
                task.tries += 1
                logger.warning(
                    f"重试任务 {task.course['title']} - {task.point['title']} "
                    f"({task.tries}/{self.max_tries} 次尝试)"
                )
                if task.tries >= self.max_tries:
                    logger.error(
                        f"任务重试次数达到上限: {task.course['title']} - {task.point['title']}"
                    )
                    self.failed.append(task)
                else:
                    retry = True

            else:
                logger.error(f"任务状态无效 {task.result} {task.point.get('title')}")
                self.failed.append(task)

            if retry:
                self.retry_queue.put(task)
            else:
                # 只有「收集时还没完成、这次又跑成功」才算本遍真有进展。
                # 收集时就已完成的章节（重刷场景）虽然也返回 SUCCESS，但不能算，
                # 否则续刷会永远认为有进展而停不下来。
                ok = (task.result == ChapterResult.SUCCESS
                      and not getattr(task, "already_finished", False))
                self._mark_finished(task, ok=ok)

    def _retry_worker(self):
        while not self.stop_event.is_set():
            try:
                task = self.retry_queue.get(timeout=0.3)
            except Empty:
                if self._is_over() and self.retry_queue.empty():
                    if self.stop_event.wait(self.retry_interval + 0.2):
                        return
                    if self.retry_queue.empty() and self._is_over():
                        return
                continue
            if self.stop_event.is_set():
                return
            # 先等待再重新入队，避免失败任务在等待期间已被另一线程执行。
            if self.stop_event.wait(max(0.0, self.retry_interval)):
                return
            self.task_queue.put(task)

    # ------------------------------------------------------------------ 入口
    def run(self) -> int:
        if not self.tasks:
            return 0
        for task in self.tasks:
            self.task_queue.put(task)

        for i in range(self.worker_num):
            th = threading.Thread(target=self._worker, name=f"worker-{i}", daemon=True)
            th.start()
            self._workers.append(th)
        retry_th = threading.Thread(target=self._retry_worker, name="retry", daemon=True)
        retry_th.start()

        while True:
            if self.stop_event.is_set():
                self._drain()
                break
            if self._is_over():
                break
            self.stop_event.wait(0.25)

        deadline = time.time() + 5.0
        for th in self._workers:
            remain = deadline - time.time()
            if remain <= 0:
                break
            th.join(timeout=remain)
        retry_th.join(timeout=max(0.0, deadline - time.time()))

        return self.done_count
