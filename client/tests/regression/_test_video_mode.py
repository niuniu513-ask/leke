# -*- coding: utf-8 -*-
"""验证 study_video 的两种模式：fast（秒过）与 timed（计时长）。

不联网：把 SessionManager 和 video_progress_log 都换成桩，
只检查「发出了哪些上报、playingTime 怎么走、isdrag 是多少」。

跑法：.venv\\Scripts\\python.exe tests/regression/_test_video_mode.py
"""
from __future__ import annotations

import os
import random
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

import api.base as base  # noqa: E402
from api.config import GlobalConst as gc  # noqa: E402

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


# ---------------------------------------------------------------- 桩
class FakeResp:
    def __init__(self, payload):
        self._p = payload
        self.status_code = 200
        self.text = "ok"

    def json(self):
        return self._p


class FakeSession:
    """ananas/status 固定返回 video info；multimedia/log 由采集器接管。"""

    def __init__(self, duration: int):
        self.duration = duration
        self.info_calls = 0
        self.last_url = ""
        self.cookies = {}

    def get(self, url, params=None, headers=None, timeout=None):
        self.last_url = url
        if "ananas/status" in url:
            self.info_calls += 1
            return FakeResp({
                "status": "success",
                "duration": self.duration,
                "dtoken": "FAKE_DTOKEN",
                "crc": "crc",
                "key": "key",
            })
        raise AssertionError("不应该直接请求 multimedia/log（已被 video_progress_log 接管）")


def make_cx(duration: int, play_time_ms: int, mode: str):
    """构造一个只带 study_video 所需部件的 Chaoxing 实例。"""
    cx = object.__new__(base.Chaoxing)
    cx.video_mode = mode
    cx.get_fid = lambda: "FAKE_FID"
    cx.get_uid = lambda: "FAKE_UID"

    calls: list[dict] = []
    sess = FakeSession(duration)
    cx._fake_session = sess
    cx._calls = calls

    def fake_log(_session, _course, _job, _job_info, _dtoken, _duration,
                 _playingTime, _type="Video", _isdrag=3, headers=None):
        calls.append({
            "playingTime": int(_playingTime),
            "duration": int(_duration),
            "isdrag": int(_isdrag),
            "t": time.time(),
        })
        # 和真实服务器一致：进度到位就算通过
        passed = int(_playingTime) >= int(_duration)
        return passed, 200

    cx.video_progress_log = fake_log

    course = {"clazzId": "C1", "courseId": "CO1", "cpi": "CPI1"}
    job = {
        "name": "测试视频.mp4", "jobid": "J1", "objectid": "O1",
        "otherinfo": "nodeId_1-cpi_2-rt_d-ds_1-ff_d-be_0_0-vt_1-v_6-enc_abc",
        "playTime": play_time_ms, "rt": "0.9",
        "videoFaceCaptureEnc": "", "attDuration": "", "attDurationEnc": "",
    }
    return cx, course, job, sess


def patch_session(sess):
    base.SessionManager.get_session = staticmethod(lambda: sess)


# ---------------------------------------------------------------- 用例
def case_fast():
    print("\n[1] fast 模式：应当只发一次上报，playingTime = duration，isdrag = 4")
    cx, course, job, sess = make_cx(duration=1940, play_time_ms=0, mode="fast")
    patch_session(sess)
    t0 = time.time()
    res = cx.study_video(course, job, {}, _speed=1.0, _type="Video")
    cost = time.time() - t0
    calls = cx._calls
    check("返回 SUCCESS", res == base.StudyResult.SUCCESS, str(res))
    check("只上报了 1 次", len(calls) == 1, f"实际 {len(calls)} 次")
    check("playingTime 直接等于 duration", calls[0]["playingTime"] == 1940,
          f"playingTime={calls[0]['playingTime']}")
    check("isdrag = 4（秒过/拖拽语义）", calls[0]["isdrag"] == 4,
          f"isdrag={calls[0]['isdrag']}")
    check("耗时极短（<5 秒）", cost < 5, f"{cost:.2f}s")
    print(f"  → 秒过 1940 秒视频，真实耗时 {cost:.2f}s，平台能记到的时长≈0")


def case_timed_new():
    print("\n[2] timed 模式（全新视频）：playingTime 必须递增，isdrag = 3（正常播放）")
    random.uniform = lambda a, b: 30          # 固定心跳间隔，便于断言
    try:
        cx, course, job, sess = make_cx(duration=180, play_time_ms=0, mode="timed")
        patch_session(sess)
        t0 = time.time()
        res = cx.study_video(course, job, {}, _speed=60.0, _type="Video")
        cost = time.time() - t0
    finally:
        random.uniform = __import__("random").Random().uniform  # 还原

    calls = cx._calls
    check("返回 SUCCESS", res == base.StudyResult.SUCCESS, str(res))
    check("首次上报不是 duration（没有跳）", calls[0]["playingTime"] < 180,
          f"first={calls[0]['playingTime']}")
    check("playingTime 严格递增",
          all(calls[i]["playingTime"] < calls[i + 1]["playingTime"]
              for i in range(len(calls) - 1)),
          str([c["playingTime"] for c in calls]))
    check("全部 isdrag = 3", all(c["isdrag"] == 3 for c in calls),
          str([c["isdrag"] for c in calls]))
    check("最后一次 playingTime = duration", calls[-1]["playingTime"] == 180,
          f"last={calls[-1]['playingTime']}")
    check("上报次数 > 1（真实节奏）", len(calls) > 1, f"{len(calls)} 次")
    check("首帧落在第一段之后（>= 心跳间隔）", calls[0]["playingTime"] >= 30,
          f"first={calls[0]['playingTime']}")
    print(f"  → 180 秒视频按 60 倍速走完，发 {len(calls)} 次心跳，真实耗时 {cost:.2f}s")


def case_timed_finished():
    print("\n[3] timed 模式（任务点已完成）：必须从头重播，不能原地报 100%")
    random.uniform = lambda a, b: 30
    try:
        # playTime 已到底 = 任务点已完成
        cx, course, job, sess = make_cx(duration=180, play_time_ms=180_000, mode="timed")
        patch_session(sess)
        cx.study_video(course, job, {}, _speed=60.0, _type="Video")
    finally:
        random.uniform = __import__("random").Random().uniform

    calls = cx._calls
    check("首帧从小进度重新开始（说明已重播）", calls[0]["playingTime"] < 180,
          f"first={calls[0]['playingTime']}")
    check("末帧到 duration", calls[-1]["playingTime"] == 180)
    print(f"  → 已完成的任务点被重播，发出 {len(calls)} 次心跳，可重新累计时长")


def case_timed_partial():
    print("\n[4] timed 模式（看到一半）：应当从已有进度续播，不重头")
    random.uniform = lambda a, b: 30
    try:
        cx, course, job, sess = make_cx(duration=300, play_time_ms=120_000, mode="timed")
        patch_session(sess)
        cx.study_video(course, job, {}, _speed=60.0, _type="Video")
    finally:
        random.uniform = __import__("random").Random().uniform

    calls = cx._calls
    check("首帧 >= 已有进度（120s）", calls[0]["playingTime"] >= 120,
          f"first={calls[0]['playingTime']}")
    check("末帧到 duration", calls[-1]["playingTime"] == 300)
    print(f"  → 从 120s 续播到 300s，发 {len(calls)} 次心跳")


def case_gc_fallback():
    print("\n[5] 实例未设 video_mode 时，回落到 gc.VIDEO_MODE")
    random.uniform = lambda a, b: 30
    try:
        cx, course, job, sess = make_cx(duration=120, play_time_ms=0, mode="fast")
        del cx.video_mode                       # 走 gc 回落
        old = gc.VIDEO_MODE
        gc.VIDEO_MODE = "timed"
        patch_session(sess)
        try:
            cx.study_video(course, job, {}, _speed=60.0, _type="Video")
        finally:
            gc.VIDEO_MODE = old
    finally:
        random.uniform = __import__("random").Random().uniform

    calls = cx._calls
    check("按 timed 行为执行（首次不是 duration）", calls[0]["playingTime"] < 120,
          f"first={calls[0]['playingTime']}")
    check("isdrag = 3", calls[0]["isdrag"] == 3)


def case_progress_frames():
    print("\n[6] GUI 进度帧：开播即到、浮点秒、session 恒定、seq 递增")
    random.uniform = lambda a, b: 30
    try:
        cx, course, job, sess = make_cx(duration=180, play_time_ms=0, mode="timed")
        patch_session(sess)
        order: list[str] = []
        frames: list[dict] = []
        inner = cx.video_progress_log

        def logging_log(*a, **kw):
            order.append("report")           # 上报（会被网络阻塞）
            return inner(*a, **kw)

        def on_frame(frame):
            order.append("frame")
            frames.append(frame)

        cx.video_progress_log = logging_log
        cx.video_progress_cb = on_frame
        cx.study_video(course, job, {}, _speed=60.0, _type="Video")
    finally:
        random.uniform = __import__("random").Random().uniform

    check("有进度帧下发", len(frames) > 1, f"{len(frames)} 帧")
    check("首帧在开播时就发（play_time≈0，不用空等一轮循环）",
          frames[0]["play_time"] < 1.0, f"first={frames[0]['play_time']}")
    check("play_time 是浮点（整秒取整正是「跳格」的来源）",
          isinstance(frames[0]["play_time"], float),
          type(frames[0]["play_time"]).__name__)
    check("duration / speed 随帧下发",
          frames[0]["duration"] == 180.0 and frames[0]["speed"] == 60.0,
          str({k: frames[0][k] for k in ("duration", "speed")}))
    check("seq 逐帧递增",
          [f["seq"] for f in frames] == list(range(1, len(frames) + 1)),
          str([f["seq"] for f in frames]))
    check("session 在同一视频内恒定（界面据此判断重播）",
          len({f["session"] for f in frames}) == 1)
    check("play_time 单调不减",
          all(frames[i]["play_time"] <= frames[i + 1]["play_time"]
              for i in range(len(frames) - 1)),
          str([round(f["play_time"], 1) for f in frames]))
    first_report = order.index("report")
    check("上报之前已经发出进度帧（请求阻塞时界面不会冻住）",
          order[first_report - 1] == "frame", str(order[:6]))


def main():
    print("=" * 62)
    print("study_video 视频模式验证")
    print("=" * 62)
    case_fast()
    case_timed_new()
    case_timed_finished()
    case_timed_partial()
    case_gc_fallback()
    case_progress_frames()
    print("\n" + "=" * 62)
    print(f"结果：{PASSES} 通过 / {len(FAILS)} 失败")
    if FAILS:
        for f in FAILS:
            print("  失败项:", f)
    print("=" * 62)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
