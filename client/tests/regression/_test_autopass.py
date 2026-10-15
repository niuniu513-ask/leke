# -*- coding: utf-8 -*-
"""验证「刷课只能刷一遍」这个缺陷的修复（自动续刷）。不联网、不依赖真实账号。

缺陷回顾：学习通是「章节测验通过 / 前置任务点完成才解锁下一章」的链式结构，
而章节列表是在开跑前一次性读出来的 —— 一遍跑完，当时还没开放的章节就再也没人管。

修复方式：每一遍处理完都重新读取一次章节列表，把新解锁的章节接着处理，
直到连续若干遍都没有新完成的章节为止。这是程序的固有行为，没有开关。

覆盖：
1. 续刷常量（兜底上限 / 无进展阈值 / 遍间隔）
2. describe_job 任务点显示名
3. StudyScheduler.success_count 只统计「本遍真有进展」的章节
4. main.process_chapter 的 on_job 回调序列（任务点级进度）
5. Runner.start_study 的续刷循环：
   刷完一遍自动重读列表 / 新解锁章节被补刷 / 连续无进展即停 /
   首遍无任务 / 兜底上限 / 中途停止 / 去掉了 rounds 配置项

运行：.venv/Scripts/python.exe tests/regression/_test_autopass.py
"""
from __future__ import annotations

import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

PASS = []
FAIL = []


def check(name: str, ok: bool, extra: str = ""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK' if ok else 'XX'}] {name}" + (f"  {extra}" if extra else ""))


# ====================================================================== 1
print("\n[1] 续刷常量")
import gui_app.worker as W  # noqa: E402
from gui_app.worker import MAX_PASSES, NO_PROGRESS_PASSES, PASS_GAP  # noqa: E402

check("兜底上限 >= 5", MAX_PASSES >= 5, f"MAX_PASSES={MAX_PASSES}")
check("无进展阈值 >= 2", NO_PROGRESS_PASSES >= 2, f"NO_PROGRESS_PASSES={NO_PROGRESS_PASSES}")
check("遍间隔是正数", PASS_GAP > 0, f"PASS_GAP={PASS_GAP}")
check("旧配置项已彻底移除（不再有 parse_rounds）", not hasattr(W, "parse_rounds"))


# ====================================================================== 2
print("\n[2] describe_job 任务点显示名")
from gui_app.study_engine import describe_job  # noqa: E402

check("视频取 name", describe_job({"type": "video", "name": "1.1 函数.mp4"}) == ("视频", "1.1 函数.mp4"))
check("阅读取 title", describe_job({"type": "read", "title": "材料 A"}) == ("阅读", "材料 A"))
check("测验无名回落类型", describe_job({"type": "workid"}) == ("章节测验", "章节测验"))
check("空字典不炸", describe_job({}) == ("任务点", "任务点"))


# ====================================================================== 3
print("\n[3] StudyScheduler.success_count 只算真实进展")


class FakeCR:
    SUCCESS = "success"
    ERROR = "error"
    NOT_OPEN = "not_open"
    PENDING = "pending"


def _run_scheduler(tasks, results_map, on_job=None):
    """用桩替掉 process_chapter，跑一遍调度器。"""
    import gui_app.study_engine as se

    def fake_process_chapter(chaoxing, course, point, speed, on_job=None, skip_finished=True):
        if on_job is not None:
            on_job(course, point, {"type": "video", "name": f"{point['title']}.mp4"}, 1, 1)
        return results_map[point["title"]]

    old = se._load_engine
    se._load_engine = lambda: (fake_process_chapter, FakeCR)
    try:
        sched = se.StudyScheduler(chaoxing=None, tasks=tasks, config={}, on_job=on_job)
        sched.run()
        return sched
    finally:
        se._load_engine = old


from gui_app.worker import ChapterTask  # noqa: E402

# 三个章节：A 本来就没完成->成功；B 收集时就已完成->成功但不计入进展；C 失败
tA = ChapterTask(0, {"title": "A", "has_finished": False}, {"title": "课程"})
tB = ChapterTask(1, {"title": "B", "has_finished": True}, {"title": "课程"})
tC = ChapterTask(2, {"title": "C", "has_finished": False}, {"title": "课程"})
sched = _run_scheduler([tA, tB, tC], {"A": FakeCR.SUCCESS, "B": FakeCR.SUCCESS, "C": FakeCR.ERROR})

check("done_count = 3（含失败）", sched.done_count == 3, f"得到 {sched.done_count}")
check("success_count = 1（B 不算进展，C 失败）", sched.success_count == 1, f"得到 {sched.success_count}")
check("失败数 = 1", len(sched.failed) == 1)
check("already_finished 正确标记", tB.already_finished is True and tA.already_finished is False)

# on_job 透传（调度器 -> process_chapter -> 回调）
seen = []
_run_scheduler([ChapterTask(0, {"title": "A", "has_finished": False}, {"title": "课程"})],
               {"A": FakeCR.SUCCESS},
               on_job=lambda c, p, j, i, n: seen.append((p["title"], j["name"], i, n)))
check("on_job 被调用且带章节/任务点/序号",
      seen == [("A", "A.mp4", 1, 1)], f"得到 {seen}")


# ====================================================================== 4
print("\n[4] main.process_chapter 的 on_job 回调")


class _StubLimiter:
    def limit_rate(self, *a, **k):
        pass


class _StubCX:
    def __init__(self, jobs):
        self.rate_limiter = _StubLimiter()
        self._jobs = jobs

    def get_job_list(self, course, point, keep_passed=False):
        return self._jobs, {"notOpen": False}


import main as chaoxing_main  # noqa: E402

jobs = [{"type": "video", "name": "1.1.mp4"},
        {"type": "workid"},
        {"type": "document"}]
seq = []
_orig_process_job = chaoxing_main.process_job
chaoxing_main.process_job = lambda cx, course, job, info, speed, on_job=None, skip_work=False: (
    on_job(course, job) if on_job else None
) or chaoxing_main.StudyResult.SUCCESS
try:
    r = chaoxing_main.process_chapter(
        _StubCX(jobs), {"title": "课程X"}, {"title": "第1章", "has_finished": False}, 1.0,
        on_job=lambda c, p, j, i, n: seq.append((p["title"], j.get("name") or j["type"], i, n)))
    seq2 = []
    r2 = chaoxing_main.process_chapter(
        _StubCX(jobs), {"title": "课程X"}, {"title": "第3章", "has_finished": False}, 1.0)
finally:
    chaoxing_main.process_job = _orig_process_job

check("返回 SUCCESS", r == chaoxing_main.ChapterResult.SUCCESS)
check("on_job 按任务点顺序触发 3 次", len(seq) == 3, f"得到 {seq}")
check("序号从 1 递增且总数正确",
      [x[2] for x in seq] == [1, 2, 3] and all(x[3] == 3 for x in seq), f"得到 {seq}")
check("章节名透传正确", all(x[0] == "第1章" for x in seq))
check("已完成章节不再回调 on_job", (
    chaoxing_main.process_chapter(_StubCX(jobs), {"title": "C"}, {"title": "已绿", "has_finished": True},
                                 1.0, on_job=lambda *a: seq.append("BAD"))
    == chaoxing_main.ChapterResult.SUCCESS and "BAD" not in seq
))
check("不给 on_job 也不崩", r2 == chaoxing_main.ChapterResult.SUCCESS)


# ====================================================================== 5
print("\n[5] Runner.start_study 的自动续刷")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import gui_app.study_engine as SE  # noqa: E402

# 桩：题库初始化直接返回 None
import api.answer as answer_mod  # noqa: E402

_orig_get_tiku = answer_mod.Tiku.get_tiku_from_config
answer_mod.Tiku.get_tiku_from_config = staticmethod(lambda *a, **k: None)

# 把遍间隔压到毫秒级，测试不必真等 3 秒
_orig_gap = W.PASS_GAP
W.PASS_GAP = 0.01

_orig_collect = W.Runner._collect_tasks
_harness = {"plan": [], "collect_calls": 0, "sched_calls": 0, "built": []}


def _fake_collect(self, cx, course_list, skip_finished, give_up=None):
    """按脚本返回「这一遍读到的待处理章节数」——用来模拟后来才解锁的章节。"""
    _harness["collect_calls"] += 1
    _harness.setdefault("give_up_log", []).append(set(give_up or set()))
    idx = min(_harness["collect_calls"] - 1, len(_harness["plan"]) - 1)
    n = _harness["plan"][idx]
    return [ChapterTask(k, {"title": f"ch{k}", "has_finished": False, "id": f"p{k}"},
                        {"title": "课程", "courseId": "c1"})
            for k in range(n) if f"c1:p{k}" not in (give_up or set())]


class _FakeScheduler:
    """按脚本返回本遍的 success_count / failed。"""

    def __init__(self, chaoxing, tasks, config, stop_event=None,
                 on_start=None, on_done=None, on_job=None):
        idx = _harness["sched_calls"]
        _harness["sched_calls"] += 1
        self.tasks = tasks
        self.stop_event = stop_event
        self.on_job = on_job
        script = _harness["script"]
        s = script[min(idx, len(script) - 1)]
        self.success_count = min(s[0], len(tasks))
        self.failed = list(tasks[:s[1]])

    def run(self):
        _harness["built"].append(len(self.tasks))
        # 模拟触发一次任务点回调（验证 on_job 会走到界面信号）
        if self.on_job is not None:
            self.on_job({"title": "课程"}, {"title": "第1章"},
                        {"type": "video", "name": "1.1.mp4"}, 1, 2)


_orig_scheduler = SE.StudyScheduler

#: 测试用账号 id —— 多账号改造后所有调用都要带 acc_id
ACC = "default"


def _install_harness(script, plan):
    _harness.update({"plan": plan, "collect_calls": 0,
                     "sched_calls": 0, "built": [], "script": script,
                     "give_up_log": []})
    W.Runner._collect_tasks = _fake_collect
    SE.StudyScheduler = _FakeScheduler


def _restore():
    W.Runner._collect_tasks = _orig_collect
    SE.StudyScheduler = _orig_scheduler


class _CX:
    def __init__(self):
        self.tiku = None
        self.kwargs = {}


COMMON = {"skip_finished": True, "speed": "1.0", "jobs": "1"}


def _run_study(common, timeout=15):
    from PySide6.QtCore import Qt

    events = {"finished": None, "passes": [], "totals": [], "current": [], "total": []}
    r = W.Runner()
    r.session(ACC, "测试账号").chaoxing = _CX()
    r.sig_finished.connect(lambda a, k, s, m: events.update(finished=(k, s, m)),
                           Qt.DirectConnection)
    r.sig_study_pass.connect(lambda a, n: events["passes"].append(n), Qt.DirectConnection)
    r.sig_study_totals.connect(lambda a, d: events["totals"].append(d), Qt.DirectConnection)
    r.sig_study_current.connect(lambda a, d: events["current"].append(d), Qt.DirectConnection)
    r.sig_study_total.connect(lambda a, n: events["total"].append(n), Qt.DirectConnection)
    r.start_study(ACC, [{"title": "课程", "courseId": "1", "clazzId": "2", "cpi": "3"}],
                  common, {"provider": ""})
    t0 = time.time()
    while events["finished"] is None and time.time() - t0 < timeout:
        time.sleep(0.05)
    return events


# --- 5.1 一遍刷完，第二遍确认没得刷了 ---
_install_harness([(3, 0)], [3, 0])
ev = _run_study(COMMON)
_restore()
check("5.1 只调度 1 次", _harness["sched_calls"] == 1, f"得到 {_harness['sched_calls']}")
check("5.1 读章节列表 2 次（第 2 遍是回来确认）", _harness["collect_calls"] == 2,
      f"得到 {_harness['collect_calls']}")
check("5.1 结束状态 ok", ev["finished"][1] == "ok", f"得到 {ev['finished']}")
check("5.1 文案「共检查 2 遍，完成 3 个章节」",
      "共检查 2 遍，完成 3 个章节" in (ev["finished"][2] or ""), f"得到 {ev['finished'][2]}")
check("5.1 遍次信号 = [1, 2]", ev["passes"] == [1, 2], f"得到 {ev['passes']}")
check("5.1 累计完成 3", ev["totals"][-1]["total_done"] == 3, f"得到 {ev['totals'][-1]}")
check("5.1 统计里带 pass 字段且无 rounds", ev["totals"][-1].get("pass") == 1
      and "rounds" not in ev["totals"][-1], f"得到 {ev['totals'][-1]}")

# --- 5.2 核心：后来才解锁的章节被自动补刷 ---
_install_harness([(3, 0), (2, 0)], [3, 2, 0])
ev = _run_study(COMMON)
_restore()
check("5.2 调度 2 次（第 1 遍 3 个 + 第 2 遍补刷 2 个）", _harness["sched_calls"] == 2,
      f"得到 {_harness['sched_calls']}")
check("5.2 每一遍都重新读章节列表", _harness["collect_calls"] == 3,
      f"得到 {_harness['collect_calls']}")
check("5.2 各遍待处理数 = [3, 2]", ev["total"] == [3, 2], f"得到 {ev['total']}")
check("5.2 累计完成 5（修复前这里只有 3）", ev["totals"][-1]["total_done"] == 5,
      f"得到 {ev['totals'][-1]}")
check("5.2 遍次信号 = [1, 2, 3]", ev["passes"] == [1, 2, 3], f"得到 {ev['passes']}")
check("5.2 文案「共检查 3 遍，完成 5 个章节」",
      "共检查 3 遍，完成 5 个章节" in (ev["finished"][2] or ""), f"得到 {ev['finished'][2]}")
check("5.2 任务点回调送到界面信号", bool(ev["current"]), f"得到 {len(ev['current'])} 条")
check("5.2 任务点详情字段齐全",
      ev["current"][0].get("job") == "1.1.mp4" and ev["current"][0].get("job_type") == "视频"
      and ev["current"][0].get("job_index") == 1 and ev["current"][0].get("job_total") == 2
      and ev["current"][0].get("pass") == 1,
      f"得到 {ev['current'][0]}")

# --- 5.3 连续两遍无新进展就收手（防死循环）---
_install_harness([(2, 0), (0, 0), (0, 0)], [5, 5, 5])
ev = _run_study(COMMON)
_restore()
check("5.3 有进展后连续 2 遍零进展 -> 调度 3 次", _harness["sched_calls"] == 3,
      f"得到 {_harness['sched_calls']}")
check("5.3 结束状态 ok", ev["finished"][1] == "ok", f"得到 {ev['finished']}")
check("5.3 累计只算真成功的 2 个", ev["totals"][-1]["total_done"] == 2,
      f"得到 {ev['totals'][-1]}")

# --- 5.4 首遍就没有待处理章节 ---
_install_harness([(0, 0)], [0])
ev = _run_study(COMMON)
_restore()
check("5.4 首遍无任务：不调度", _harness["sched_calls"] == 0,
      f"得到 {_harness['sched_calls']}")
check("5.4 遍次信号只到 1", ev["passes"] == [1], f"得到 {ev['passes']}")
check("5.4 文案 = 没有需要处理的任务点",
      "没有需要处理的任务点" in (ev["finished"][2] or ""), f"得到 {ev['finished'][2]}")

# --- 5.5 每遍都有进展时由兜底上限收口 ---
# 注：进展判定按「收集到的章节集合是否变化」，故 plan 递增以模拟
# 「不断有新章节解锁」的真进展；集合恒定会被判定为无进展提前收口。
_orig_max = W.MAX_PASSES
W.MAX_PASSES = 4
_install_harness([(1, 0)] * 8, [3, 4, 5, 6, 7, 8, 9, 10])
ev = _run_study(COMMON)
_restore()
W.MAX_PASSES = _orig_max
check("5.5 一直有进展 -> 跑满兜底上限 4 遍就停", _harness["sched_calls"] == 4,
      f"得到 {_harness['sched_calls']}")
check("5.5 累计完成 = 4 遍 x 1", ev["totals"][-1]["total_done"] == 4,
      f"得到 {ev['totals'][-1]}")
check("5.5 结束状态 ok", ev["finished"][1] == "ok", f"得到 {ev['finished']}")

# --- 5.6 中途停止 ---
_install_harness([(1, 0), (1, 0), (1, 0)], [3, 3, 3])
_stop_flag = {"runner": None}
_orig_run = _FakeScheduler.run


def _run_and_stop(self):
    _orig_run(self)
    if _harness["sched_calls"] == 1 and _stop_flag["runner"] is not None:
        _stop_flag["runner"].request_stop()


_FakeScheduler.run = _run_and_stop
events = {"finished": None}
from PySide6.QtCore import Qt  # noqa: E402

r = W.Runner()
r.session(ACC, "测试账号").chaoxing = _CX()
_stop_flag["runner"] = r
r.sig_finished.connect(lambda a, k, s, m: events.update(finished=(k, s, m)),
                       Qt.DirectConnection)
r.start_study(ACC, [{"title": "课程", "courseId": "1", "clazzId": "2", "cpi": "3"}],
              COMMON, {"provider": ""})
t0 = time.time()
while events["finished"] is None and time.time() - t0 < 15:
    time.sleep(0.05)
_FakeScheduler.run = _orig_run
_restore()
check("5.6 状态 stopped", events["finished"][1] == "stopped", f"得到 {events['finished']}")
check("5.6 不再进入下一遍", _harness["sched_calls"] == 1, f"得到 {_harness['sched_calls']}")
check("5.6 文案含「已停止」", "已停止" in (events["finished"][2] or ""),
      f"得到 {events['finished'][2]}")

W.PASS_GAP = _orig_gap
answer_mod.Tiku.get_tiku_from_config = _orig_get_tiku

# --- 5.7 失败章节熔断：同一章节连续 3 遍失败即放弃，其余照常推进 ---
# p0 每遍失败、p1 每遍"成功"（视频类，但服务端暂未回写完成）。
# 第 4 遍起 p0 不应再被收集；p1 还在列表里直到服务端标记完成。
# plan 每遍给 2（p0..p1）：第 4 遍 p0 被熔断过滤后只剩 p1，
# 第 5 遍 plan 给 0 → 已无待处理章节收尾。
_install_harness([(1, 1), (1, 1), (1, 1), (1, 1)], [2, 2, 2, 2, 0])
ev = _run_study(COMMON)
_restore()
glog = _harness.get("give_up_log", [])
check("5.7 第 1~3 遍无熔断", all(not g for g in glog[:3]),
      f"得到 {[sorted(g) for g in glog[:3]]}")
check("5.7 第 4 遍起 p0 被熔断", glog[3:] and all("c1:p0" in g for g in glog[3:]),
      f"得到 {[sorted(g) for g in glog[3:]]}")
check("5.7 调度 4 次（第 4 遍只剩 p1）", _harness["sched_calls"] == 4,
      f"得到 {_harness['sched_calls']}")
check("5.7 各遍待处理数 = [2, 2, 2, 1]", ev["total"] == [2, 2, 2, 1],
      f"得到 {ev['total']}")
check("5.7 熔断不拖垮整体结束", ev["finished"][1] == "ok", f"得到 {ev['finished']}")

# --- 5.8 服务端始终不回写「已完成」：集合恒定 → 及时停，不跑满兜底上限 ---
# 复现「一直在多遍循环刷」：章节每遍都号称处理成功，但服务端 has_finished
# 始终是 False，收集到的章节集合原封不动。旧逻辑靠 success_count 判定，
# 每遍都 >0 把 empty_streak 清零 → 跑满 20 遍；新逻辑靠集合判定，2 遍无变化即停。
_install_harness([(3, 0)] * 20, [3] * 20)
ev = _run_study(COMMON)
_restore()
check("5.8 集合恒定 -> 3 遍即停（不再跑满 20 遍）",
      _harness["sched_calls"] == 3, f"得到 {_harness['sched_calls']}")
check("5.8 结束状态 ok", ev["finished"][1] == "ok", f"得到 {ev['finished']}")


# ======================================================================
print(f"\n{'=' * 56}")
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  失败:", f)
sys.exit(1 if FAIL else 0)
