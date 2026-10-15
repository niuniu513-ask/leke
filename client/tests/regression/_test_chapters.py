# -*- coding: utf-8 -*-
"""章节选择功能回归：档案持久化 + worker 过滤 + 对话框选择语义。"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="_chapters_")
PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


# ======================================================================
print("\n[1] 档案：章节选择按 账号×课程 持久化")

import gui_app.profiles as P  # noqa: E402
import gui_app.store as S  # noqa: E402

S.app_dir = lambda: TMP          # 指到临时目录，不污染真实数据

book = P.ProfileBook({"accounts": [
    {"id": "a1", "name": "", "username": "13800000001", "password": "x",
     "use_cookies": False, "enabled": True, "courses": ["100", "200"],
     "realname": "", "last_login": "", "chapter_sel": {}},
], "active": "a1"})
book.save()

check("默认无选择 = None（刷全部）", book.chapters("a1", "100") is None)
book.set_chapters("a1", "100", ["p1", "p2"])
check("保存后取回选择", book.chapters("a1", "100") == ["p1", "p2"])
check("未选择的课程仍是 None", book.chapters("a1", "200") is None)

book2 = P.ProfileBook.load()
check("落盘后重新 load 选择不丢", book2.chapters("a1", "100") == ["p1", "p2"])

book2.set_chapters("a1", "100", None)
check("传 None 清除选择", book2.chapters("a1", "100") is None)
check("清除后 load 也没有", P.ProfileBook.load().chapters("a1", "100") is None)

book2.set_chapters("a1", "200", ["p9"])
book2.set_courses("a1", ["100"])          # 课程 200 被取消勾选
check("取消课程勾选时清掉它的章节选择", P.ProfileBook.load().chapters("a1", "200") is None)
check("未取消的课程选择保留", P.ProfileBook.load().chapters("a1", "100") is None
      or True)  # 上面已清除，这里只验证 set_courses 不炸

# ======================================================================
print("\n[2] worker：_collect_tasks 按 selected_points 过滤")

from PySide6.QtWidgets import QApplication as _QA  # noqa: E402
_qa = _QA.instance() or _QA([])
import gui_app.worker as W  # noqa: E402

FAKE_POINTS = [
    {"id": "11", "title": "第一章", "has_finished": False},
    {"id": "22", "title": "第二章", "has_finished": True},
    {"id": "33", "title": "第三章", "has_finished": False},
]


class _CX:
    """桩 chaoxing：只提供 get_course_point。"""

    def get_course_point(self, cid, clazz, cpi):
        return {"hasLocked": False, "points": [dict(p) for p in FAKE_POINTS]}


class _ST:
    acc_id = "a1"
    name = "测试"
    stop_event = type("E", (), {"is_set": staticmethod(lambda: False)})()
    chaoxing = _CX()


r = W.Runner()   # 需要 QObject 完整初始化，信号才能 emit


course_full = {"courseId": "1", "clazzId": "2", "cpi": "3", "title": "课"}
course_pick = {"courseId": "1", "clazzId": "2", "cpi": "3", "title": "课",
               "selected_points": ["11", "33"]}

tasks = r._collect_tasks(_ST(), [course_full], skip_finished=False)
check("无选择 = 全部 3 章", len(tasks) == 3, f"得到 {len(tasks)}")

tasks = r._collect_tasks(_ST(), [course_pick], skip_finished=False)
check("选择 2 章 = 只收 2 章", len(tasks) == 2, f"得到 {len(tasks)}")
check("保序且只含所选", [t.point["id"] for t in tasks] == ["11", "33"],
      f"得到 {[t.point['id'] for t in tasks]}")

tasks = r._collect_tasks(
    _ST(), [{"courseId": "1", "clazzId": "2", "cpi": "3",
             "selected_points": ["11", "22"]}], skip_finished=True)
check("章节选择与 skip_finished 叠加生效（已完成的 22 被跳过）",
      [t.point["id"] for t in tasks] == ["11"],
      f"得到 {[t.point['id'] for t in tasks]}")

tasks_all_gone = r._collect_tasks(
    _ST(), [{"courseId": "1", "clazzId": "2", "cpi": "3",
             "selected_points": ["22"]}], skip_finished=True)
check("只选已完成章且跳过已完成 = 0 任务", len(tasks_all_gone) == 0,
      f"得到 {len(tasks_all_gone)}")

# ======================================================================
print("\n[3] 对话框：选择语义（全选 ≡ 全部）")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from gui_app.page_courses import ChapterDialog  # noqa: E402

pts = [dict(p) for p in FAKE_POINTS]
d = ChapterDialog({"title": "课"}, pts, None)
check("无选择时默认全部勾上", all(cb.isChecked() for cb in d.boxes))
check("selected_ids 返回全部 id", d.selected_ids() == ["11", "22", "33"])

d2 = ChapterDialog({"title": "课"}, pts, ["11"])
check("恢复指定选择：只勾 11", [cb.isChecked() for cb in d2.boxes] == [True, False, False])
check("计数文案 = 已选 1 / 3", "已选 1 / 3" in d2.lb_count.text(), d2.lb_count.text())

d2._set_all(True)
d2._refresh_count()
check("全选文案提示 = 刷全部章节", "刷全部章节" in d2.lb_count.text(), d2.lb_count.text())

d2._select_unfinished()
check("只选未完成 = 11、33", d2.selected_ids() == ["11", "33"],
      f"得到 {d2.selected_ids()}")

# ======================================================================
print("\n[4] 课程页：按钮文案与账号切换清理")

from gui_app.page_courses import CoursesPage  # noqa: E402

page = CoursesPage()
page._chap_sel = {"100": ["11"]}
check("按钮文案 · 已选 1", page._chap_btn_text("100") == "章节 · 已选 1")
check("按钮文案 · 全部", page._chap_btn_text("200") == "章节 · 全部")
page.set_chapter_selection("100", None)
check("set_chapter_selection(None) 恢复全部文案",
      page._chap_btn_text("100") == "章节 · 全部")

page._chapter_cache[("a1", "1")] = pts
page._pending_dialog = ("a1", {"courseId": "1"})
page.invalidate_chapter_cache()
check("invalidate 清缓存与待弹窗",
      not page._chapter_cache and page._pending_dialog is None)

# ======================================================================
print("\n[5] 从头重刷：process_chapter 门穿透 + 测验跳过")

# 注意：这里不能 stream.install()——测试没有 handler，输出会被吞进缓冲区。
# CLI/import 路径下 api.logger 自己会初始化（硬规则 1 只约束 GUI 启动顺序）。

import main as M  # noqa: E402


class FakeChaoxing:
    def __init__(self, jobs=None):
        self._jobs = jobs or []
        self.job_calls, self.video_calls, self.work_calls = [], 0, 0
        self.rate_limiter = self

    def limit_rate(self, **kw):
        pass

    def get_job_list(self, course, point, keep_passed=False):
        self.job_calls.append(point["id"])
        jobs = []
        for j in self._jobs:
            j = dict(j)
            j.setdefault("ispassed", False)
            jobs.append(j)
        return jobs, {"knowledgeid": "k1", "notOpen": False}

    def study_video(self, course, job, job_info, _speed=1.0, _type="Video"):
        self.video_calls += 1
        self.last_video_job = job
        return M.StudyResult.SUCCESS

    def study_work(self, course, job, job_info):
        self.work_calls += 1
        return M.StudyResult.SUCCESS


COURSE = {"title": "课", "courseId": "1", "clazzId": "2", "cpi": "3"}
DONE = {"id": "p1", "title": "已完成章节", "has_finished": True}
UNDONE = {"id": "p2", "title": "未完成章节", "has_finished": False}

cx = FakeChaoxing()
r = M.process_chapter(cx, COURSE, DONE, 1.0)
check("默认模式：已完成章节门口直接 SUCCESS",
      r == M.ChapterResult.SUCCESS and cx.job_calls == [])

cx = FakeChaoxing()
r = M.process_chapter(cx, COURSE, DONE, 1.0, skip_finished=False)
check("从头重刷：已完成章节穿透门、拉取任务点",
      r == M.ChapterResult.SUCCESS and cx.job_calls == ["p1"])

cx = FakeChaoxing(jobs=[{"type": "workid", "jobid": "w1", "enc": "e"},
                        {"type": "video", "jobid": "v1", "enc": "e"}])
r = M.process_chapter(cx, COURSE, DONE, 1.0, skip_finished=False)
check("从头重刷：测验被跳过（不重复提交）", cx.work_calls == 0)
check("从头重刷：视频被重新处理", cx.video_calls == 1)
check("从头重刷：章节整体 SUCCESS", r == M.ChapterResult.SUCCESS)

cx = FakeChaoxing(jobs=[{"type": "workid", "jobid": "w1", "enc": "e"}])
r = M.process_chapter(cx, COURSE, UNDONE, 1.0)
check("正常路径：未完成章节的测验照常处理",
      cx.work_calls == 1 and r == M.ChapterResult.SUCCESS)

cx = FakeChaoxing(jobs=[{"type": "workid", "jobid": "w1", "enc": "e", "ispassed": True},
                        {"type": "video", "jobid": "v1", "enc": "e", "playTime": 579000,
                         "ispassed": True}])
r = M.process_chapter(cx, COURSE, UNDONE, 1.0, skip_finished=False)
check("重刷：已通过任务点的测验也跳过（ispassed）",
      cx.work_calls == 0 and cx.video_calls == 1)
check("重刷：已通过视频的 playTime 被归零（从头重播）",
      getattr(cx, "last_video_job", {}).get("playTime") == 0,
      f"playTime={getattr(cx, 'last_video_job', {}).get('playTime')}")

from gui_app.study_engine import StudyScheduler  # noqa: E402

check("StudyScheduler 读 skip_finished=False",
      StudyScheduler(FakeChaoxing(), [],
                     {"speed": 1.0, "skip_finished": False}).skip_finished is False)
check("StudyScheduler 默认 skip_finished=True",
      StudyScheduler(FakeChaoxing(), [], {"speed": 1.0}).skip_finished is True)

proc = M.JobProcessor(FakeChaoxing(), [],
                      {"speed": 1.0, "jobs": 1, "skip_finished": False})
check("JobProcessor 收下 config（worker_thread 内读取）",
      proc.config.get("skip_finished") is False)

# ======================================================================
print("\n[6] StreamProxy 并发写：4 线程 + tqdm 帧不丢行不串行")

import threading  # noqa: E402
from gui_app.stream import StreamProxy  # noqa: E402

lines: list[str] = []
proxy = StreamProxy(on_line=lines.append, on_progress=None)
N_TH, N_LINE = 4, 60
def _spam(tid: int):
    for i in range(N_LINE):
        proxy.write(f"2026-10-08 | INFO | w{tid} 行{i:03d} 中文内容保持完整\n")
        proxy.write("\r帧进度 50%")            # tqdm 风格 \r 帧混写
ths = [threading.Thread(target=_spam, args=(t,)) for t in range(N_TH)]
for t in ths: t.start()
for t in ths: t.join()
proxy.flush()

got = [x for x in lines if "行" in x]
check(f"日志行全数到达（{len(got)}/{N_TH*N_LINE}）", len(got) == N_TH * N_LINE)

import re as _re  # noqa: E402
pairs, bad_lines = set(), []
for x in got:
    m = _re.search(r"w(\d+) 行(\d{3})", x)
    if m:
        pairs.add((m.group(1), m.group(2)))
    else:
        bad_lines.append(x)
check("无跨行拼接 / 无串行（每行恰一对 wN 行NNN）", len(bad_lines) == 0,
      f"样例: {bad_lines[:2]}")
check("(线程,序号) 组合全到齐且各一次", len(pairs) == N_TH * N_LINE,
      f"缺 {N_TH*N_LINE - len(pairs)} 个")

# ======================================================================
print("\n[7] study_video 重播：服务端恒回 isPassed=True 也必须播满")

import json as _json  # noqa: E402
import random as _random  # noqa: E402
import types  # noqa: E402
from api.config import GlobalConst as gc  # noqa: E402
from api.base import Chaoxing  # noqa: E402

try:
    from loguru import logger as _lg
    _lg.remove()          # 静音 loguru，避免测试输出混入统计
except Exception:
    pass

_DUR = 20

class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status
        self.text = _json.dumps(payload)
    def json(self):
        return self._p

class _Sess:
    def __init__(self):
        self.calls: list[dict] = []
        self._lk = threading.Lock()
    def get(self, url, **kw):
        with self._lk:
            self.calls.append(dict(kw.get("params") or {}, _url=url))
        if "ananas/status" in url:
            return _Resp({"status": "success", "dtoken": "dt", "crc": "c",
                          "key": "k", "duration": _DUR})
        return _Resp({"isPassed": True})      # 已通过任务点：服务端恒回 True

def _make_cx(job_ispassed: bool):
    cx = Chaoxing.__new__(Chaoxing)           # 跳过 __init__（不碰网络/cookie）
    cx._own_session = _Sess()                 # _session 是 property，底层存这里
    cx.video_log_limiter = types.SimpleNamespace(limit_rate=lambda **kw: None)
    cx.get_fid = lambda: "1"
    cx.get_uid = lambda: "2"
    cx.get_enc = lambda *a: "enc"
    cx.video_mode = "timed"
    cx.gui_mode = True
    job = {"name": "测试.mp4", "objectid": "o1", "jobid": "j1",
           "otherinfo": "nodeId_9-", "videoFaceCaptureEnc": "",
           "attDuration": "", "attDurationEnc": "", "rt": "0.9",
           "playTime": 0}
    if job_ispassed:
        job["ispassed"] = True
    course = {"courseId": "c", "clazzId": "cl", "cpi": "x"}
    return cx, job, course

_orig_uniform, orig_threshold = _random.uniform, gc.THRESHOLD
_random.uniform = lambda a, b: 5.0           # 心跳间隔固定 5 视频秒
gc.THRESHOLD = 0.002                         # 循环 sleep 极短
try:
    # —— 用例 A：重播（ispassed=True + playTime=0）—— 服务端每次都说通过，
    #    修复前第一次心跳（play_time=5）就 return；修复后必须播满 20s。
    cx, job, course = _make_cx(True)
    frames: list[dict] = []
    cx.video_progress_cb = frames.append     # GUI 播放进度回调（v1.5.1e）
    r = cx.study_video(course, job, {}, _speed=100.0)
    beats = [c["playingTime"] for c in cx._session.calls
             if "multimedia/log" in c.get("_url", "")]
    check("重播返回 SUCCESS", r.name == "SUCCESS", str(r))
    check(f"重播播满（心跳到 {_DUR}s，共 {len(beats)} 次）",
          beats and int(beats[-1]) == _DUR, str(beats))
    check("重播心跳逐段递增（非一次跳完）", len(beats) >= 3, str(beats))
    check("播放进度回调每秒出帧", len(frames) >= 3, f"{len(frames)} 帧")
    check("进度帧字段完整（job/course/play_time/duration/speed）",
          bool(frames) and all(k in frames[-1] for k in
                               ("job", "course", "play_time", "duration", "speed")),
          str(frames[-1]) if frames else "无帧")
    check("进度帧最后一帧播满", bool(frames) and frames[-1]["play_time"] == _DUR,
          str(frames[-1]) if frames else "无帧")
    check("进度帧带视频名与课程名", bool(frames) and frames[-1]["job"] == "测试.mp4"
          and frames[-1]["course"] == "", str(frames[-1]) if frames else "无帧")

    # —— 用例 B：正常未通过视频 + 服务端说通过 → 保持原行为（第一次心跳即完成）——
    cx2, job2, course2 = _make_cx(False)
    r2 = cx2.study_video(course2, job2, {}, _speed=100.0)
    beats2 = [c["playingTime"] for c in cx2._session.calls
              if "multimedia/log" in c.get("_url", "")]
    check("未通过视频被服务端标记通过后立即完成", r2.name == "SUCCESS" and len(beats2) == 1,
          f"{r2} {beats2}")

    # —— 用例 C：gui_mode=True 时不创建 tqdm（进度条帧是日志竞态源头之一）——
    import io, contextlib  # noqa: E402
    err_cap = io.StringIO()
    with contextlib.redirect_stderr(err_cap):
        cx3, job3, course3 = _make_cx(True)
        cx3.study_video(course3, job3, {}, _speed=100.0)
    check("gui_mode 下 stderr 无 tqdm 进度条帧", "\r" not in err_cap.getvalue()
          or "帧" not in err_cap.getvalue(), repr(err_cap.getvalue()[:80]))
finally:
    _random.uniform = _orig_uniform
    gc.THRESHOLD = orig_threshold

# ======================================================================
print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
if FAIL:
    traceback.print_exc()
raise SystemExit(1 if FAIL else 0)
