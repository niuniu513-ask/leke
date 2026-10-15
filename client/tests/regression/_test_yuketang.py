# -*- coding: utf-8 -*-
"""雨课堂接入自测（纯离线：mock requests / websocket / 只走 vendor 二维码）。

覆盖：vendor 包与模块卫生、课程解析、章节树嵌套解析、身份获取与缓存、
心跳包字段（sq/cp/u/c/v/classroomid/skuid）、watch_video 的完成/跳过/中断/
阻塞退避/停滞保护、watch_course_videos 统计、login_by_qrcode 成功与取消。

跑法：cd chaoxing-main; .venv/Scripts/python.exe tests/regression/_test_yuketang.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import types
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

import api  # noqa: E402

# 用空 logger 顶替 api.logger：测试不落 leke.log，也不会把日志混进输出。
# （注意：测试里不能调用 stream.install()，否则输出会被 StreamProxy 吞掉。）
_null_logger = types.ModuleType("api.logger")


class _NullLogger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


_null_logger.logger = _NullLogger()
sys.modules["api.logger"] = _null_logger
api.logger = _null_logger

from api.yuketang import client as yt  # noqa: E402

FAILS = []
PASSES = 0


def check(name, ok, extra=""):
    global PASSES
    if ok:
        PASSES += 1
        print("  [PASS] " + name + ("  " + extra if extra else ""))
    else:
        FAILS.append(name)
        print("  [FAIL] " + name + ("  " + extra if extra else ""))


def section(title):
    print("")
    print("[" + title + "]")


PNG_MAGIC = bytes([137, 80, 78, 71])

UNIVERSITY_ID = 3325
USER_ID = 12345
REALNAME = "测试同学"
SESSION_COOKIE = "sessionid=sess-xyz; Path=/; HttpOnly"
COOKIE = "csrftoken=csrf-abc; sessionid=sess-xyz"

COURSES = {
    "data": {
        "product_list": [
            {
                "course_name": "测试课程A",
                "classroom_id": 5001,
                "course_sign": "sign-aaa",
                "sku_id": "sku-1",
                "course_id": 9001,
                "university_id": 3325,
            },
            {
                "course_name": "测试课程B",
                "classroom_id": 5002,
                "course_sign": "sign-bbb",
                "sku_id": "sku-2",
                "course_id": 9002,
            },
        ]
    }
}

# section_leaf_list 里嵌套 leaf_list，且再嵌套一层
CHAPTER_FULL = {
    "data": {
        "course_chapter": [
            {
                "id": 1,
                "name": "第一章",
                "section_leaf_list": [
                    {"id": 111, "name": "1.1 视频A", "leaf_type": 0},
                    {"id": 999, "name": "1.2 PPT", "leaf_type": 3},
                    {
                        "id": 2,
                        "name": "1.3 小节",
                        "leaf_type": None,
                        "leaf_list": [
                            {"id": 112, "name": "1.3.1 视频B", "leaf_type": 0},
                            {"id": 998, "name": "1.3.2 讨论", "leaf_type": 4},
                            {
                                "id": 3,
                                "name": "1.3.3 更深一层",
                                "leaf_type": None,
                                "leaf_list": [
                                    {"id": 113, "name": "1.3.3.1 视频C", "leaf_type": 0},
                                ],
                            },
                        ],
                    },
                ],
            },
            {
                "id": 4,
                "name": "第二章",
                "section_leaf_list": [
                    {"id": 114, "name": "2.1 视频D", "leaf_type": 0},
                ],
            },
        ]
    }
}

CHAPTER_WATCH = {
    "data": {
        "course_chapter": [
            {
                "id": 1,
                "name": "第一章",
                "section_leaf_list": [
                    {"id": 311, "name": "会完成的视频", "leaf_type": 0},
                    {"id": 222, "name": "已看完的视频", "leaf_type": 0},
                    {"id": 333, "name": "停滞视频", "leaf_type": 0},
                ],
            }
        ]
    }
}


# --------------------------------------------------------------------- 假网络
class FakeResponse:
    def __init__(self, payload=None, text=None, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.raw = None
        if text is not None:
            self.text = text
        elif payload is not None:
            self.text = json.dumps(payload, ensure_ascii=False)
        else:
            self.text = ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


class FakeSession:
    def __init__(self, router):
        self._router = router
        self.headers = {}
        self.calls = []
        self.closed = False

    def request(self, method, url, timeout=None, **kwargs):
        self.calls.append({"method": method.upper(), "url": url, "kwargs": kwargs})
        return self._router(method.upper(), url, kwargs)

    def close(self):
        self.closed = True


class FakeRequests:
    """假 requests 模块：Session() 固定返回同一个假会话，便于断言调用。"""

    def __init__(self, router):
        self.router = router
        self.session = FakeSession(router)
        self.calls = self.session.calls
        self.exceptions = types.SimpleNamespace(
            Timeout=TimeoutError, ConnectionError=ConnectionError, RequestException=Exception
        )

    def Session(self):
        return self.session

    def get(self, url, headers=None, timeout=None, **kwargs):
        payload = {"headers": headers, "timeout": timeout}
        self.calls.append({"method": "GET", "url": url, "kwargs": payload})
        return self.router("GET", url, payload)

    def post(self, url, json=None, data=None, headers=None, timeout=None, **kwargs):
        payload = {"json": json, "data": data, "headers": headers}
        self.calls.append({"method": "POST", "url": url, "kwargs": payload})
        return self.router("POST", url, payload)


class FakeWebSocketApp:
    instances = []
    script = []

    def __init__(self, url, on_message=None, on_error=None, on_close=None):
        self.url = url
        self.on_message = on_message
        self.on_error = on_error
        self.on_close = on_close
        self.on_open = None
        self.sent = []
        self.closed = False
        self._closed_event = threading.Event()
        FakeWebSocketApp.instances.append(self)

    def send(self, payload):
        self.sent.append(payload)

    def close(self):
        self.closed = True
        self._closed_event.set()

    def run_forever(self, *args, **kwargs):
        try:
            if self.on_open:
                self.on_open(self)
            for message in list(FakeWebSocketApp.script):
                if self.closed:
                    break
                if self.on_message:
                    self.on_message(self, message)
            if not self.closed:
                self._closed_event.wait(5.0)
        finally:
            if self.on_close:
                self.on_close(self, None, None)


def make_state():
    return {
        "chapter": CHAPTER_FULL,
        "heartbeats": {},
        "heart_payloads": [],
        "blocking_left": {},
        "completed_videos": set(),
        "stalled_videos": set(),
        "missing_progress": set(),
        "problem_apply": 0,
        "calls": [],
    }


def make_router(state):
    def route(method, url, kwargs):
        state["calls"].append((method, url))
        if "get_custom_university_info" in url:
            return FakeResponse({"data": {"university_id": UNIVERSITY_ID}})
        if "check_user_session" in url:
            return FakeResponse({"data": {"user_id": USER_ID}})
        if "get_user_basic_info" in url:
            return FakeResponse({"data": {"user_info": {"name": REALNAME}}})
        if "verify-origin-system-bind" in url:
            return FakeResponse(
                {"data": {"user_id": USER_ID}},
                headers={"Set-Cookie": SESSION_COOKIE + ", csrftoken=csrf-abc; Path=/"},
            )
        if "user-courses" in url:
            return FakeResponse(COURSES)
        if "course/chapter" in url:
            return FakeResponse(state["chapter"])
        if "problem_apply" in url:
            state["problem_apply"] += 1
            return FakeResponse({"success": True})
        if "/video-log/heartbeat/" in url:
            body = kwargs.get("json") or {}
            hearts = body.get("heart_data") or []
            vid = str(hearts[0]["v"]) if hearts else ""
            state["heartbeats"][vid] = state["heartbeats"].get(vid, 0) + 1
            state["heart_payloads"].append(hearts)
            if state["blocking_left"].get(vid, 0) > 0:
                state["blocking_left"][vid] -= 1
                return FakeResponse(text="Expected available in 3 second.")
            return FakeResponse({"success": True})
        if "get_video_watch_progress" in url:
            query = parse_qs(urlparse(url).query)
            vid = (query.get("video_id") or [""])[0]
            if vid in state["missing_progress"]:
                return FakeResponse(text="完全不是 JSON 的响应")
            if vid in state["completed_videos"]:
                return FakeResponse({"data": {vid: {"completed": 1, "rate": 1.0, "watch_length": 999}}})
            beats = state["heartbeats"].get(vid, 0)
            if vid in state["stalled_videos"]:
                rate = 0.0
            else:
                rate = min(0.99, 0.5 * beats)
            return FakeResponse({"data": {vid: {"rate": rate, "watch_length": 100 + beats * 36, "completed": 0}}})
        return FakeResponse(text="not found", status=404)

    return route


def main():
    state = make_state()
    fake_requests = FakeRequests(make_router(state))
    yt.requests = fake_requests

    # ============================================================ [1] 模块卫生
    section("vendor 二维码与模块卫生")
    client = yt.YuketangClient("buu.yuketang.cn", cookie=COOKIE,
                               user_id=USER_ID, university_id=UNIVERSITY_ID)
    check("1.1 不预设学校", yt.DEFAULT_DOMAIN == "")
    check("1.2 类可导入且实例化不炸（缺 cookie 也允许）",
          yt.YuketangClient("campus.example.edu.cn") is not None and client.domain == "buu.yuketang.cn")
    check("1.3 域名规整（去协议/路径）",
          yt.YuketangClient("https://ustc.yuketang.cn/pro/portal/").domain == "ustc.yuketang.cn")
    check("1.4 cookie 解析 + platform_id 注入",
          client.csrftoken == "csrf-abc" and client.sessionid == "sess-xyz"
          and "platform_id=3" in client.cookie and "university_id=3325" in client.cookie)
    check("1.5 请求头关键字段",
          client.headers["university-id"] == "3325" and client.headers["xtbz"] == "cloud"
          and client.headers["x-csrftoken"] == "csrf-abc")

    check("1.6 二维码可用（qrcode 未装 → 走第②层 vendor）", yt.qrcode_available() is True)
    qr_mod = yt._load_qrcode()
    check("1.7 落到 vendor 副本", qr_mod.__name__.endswith("_vendor_qrcode"), qr_mod.__name__)
    png = yt.render_qrcode_png("https://xuetangx.com/login/ticket-abc")
    check("1.8 渲染出合法 PNG 字节", png[:4] == PNG_MAGIC and len(png) > 100,
          str(len(png)) + " bytes")

    src = open(yt.__file__, encoding="utf-8").read()
    lines = src.splitlines()
    top_bad = []
    for line in lines:
        for mod in ("websocket", "qrcode", "PIL"):
            if line.startswith("import " + mod) or line.startswith("from " + mod):
                top_bad.append(line)
    check("1.9 顶层无 websocket/qrcode/PIL 无保护 import", not top_bad, str(top_bad))
    prints = [l for l in lines if l.lstrip().startswith("print(")]
    check("1.10 新代码没有 print 调试", not prints, str(prints[:3]))
    check("1.11 延迟导入 api.logger", "from api.logger import logger" in src)
    check("1.12 包级延迟再导出", api.yuketang.YuketangClient is yt.YuketangClient
          and api.yuketang.DEFAULT_DOMAIN == yt.DEFAULT_DOMAIN)

    # ============================================================ [2] 课程解析
    section("课程解析")
    courses = client.list_courses()
    check("2.1 课程数量", len(courses) == 2, str(len(courses)))
    check("2.2 字段齐全",
          courses[0] == {"course_name": "测试课程A", "classroom_id": 5001,
                         "course_sign": "sign-aaa", "sku_id": "sku-1", "course_id": 9001},
          str(courses[0]))
    check("2.3 uv_id 拼进 URL",
          any("user-courses" in url and "uv_id=3325" in url for _, url in state["calls"]))

    # ============================================================ [3] 章节树
    section("章节树解析（嵌套 leaf_list）")
    videos = client.list_videos(5001, "sign-aaa")
    check("3.1 视频数量（跨章节、跨两层嵌套）", len(videos) == 4, str(len(videos)))
    check("3.2 顶层视频叶子",
          videos.get("111") == "1.1 视频A" and videos.get("114") == "2.1 视频D")
    check("3.3 第一层嵌套 leaf_list", videos.get("112") == "1.3.1 视频B")
    check("3.4 第二层嵌套 leaf_list", videos.get("113") == "1.3.3.1 视频C")
    check("3.5 非视频叶子被排除", "999" not in videos and "998" not in videos)
    check("3.6 cid/sign 拼进 URL",
          any("course/chapter" in url and "cid=5001" in url and "sign=sign-aaa" in url
              for _, url in state["calls"]))

    # ============================================================ [4] 身份
    section("fetch_identity 与缓存")
    fresh = yt.YuketangClient("buu.yuketang.cn", cookie=COOKIE)
    identity = fresh.fetch_identity()
    check("4.1 拿到 user_id / realname",
          identity["user_id"] == str(USER_ID) and identity["realname"] == REALNAME,
          str(identity))
    check("4.2 university_id 延迟补齐", identity["university_id"] == str(UNIVERSITY_ID)
          and fresh.headers["university-id"] == str(UNIVERSITY_ID))
    before = len([1 for _, url in state["calls"] if "check_user_session" in url])
    fresh.fetch_identity()
    after = len([1 for _, url in state["calls"] if "check_user_session" in url])
    check("4.3 已缓存 user_id 后不再请求", after == before, str(before) + " → " + str(after))
    check("4.4 相同 user_id 时免请求 progress 前置",
          fresh._ensure_identity()["user_id"] == str(USER_ID))

    # ============================================================ [5] 心跳包
    section("心跳包字段")
    hearts1, cp_next = client._build_heartbeat("111", 9001, 5001, "sku-1", 100)
    check("5.1 每批 3 条", len(hearts1) == 3)
    check("5.2 sq 依次 1/2/3", [h["sq"] for h in hearts1] == [1, 2, 3])
    check("5.3 cp 每包递增 learning_rate（12）",
          [h["cp"] for h in hearts1] == [100, 112, 124], str([h["cp"] for h in hearts1]))
    check("5.4 返回下一批 cp", cp_next == 136, str(cp_next))
    hearts2, cp_next2 = client._build_heartbeat("111", 9001, 5001, "sku-1", cp_next)
    check("5.5 第二批继续递增", [h["cp"] for h in hearts2] == [136, 148, 160] and cp_next2 == 172)
    first = hearts1[0]
    check("5.6 u/c/v/classroomid/skuid 正确",
          first["u"] == USER_ID and first["c"] == 9001 and first["v"] == 111
          and first["classroomid"] == "5001" and first["skuid"] == "sku-1",
          str({k: first[k] for k in ("u", "c", "v", "classroomid", "skuid")}))
    check("5.7 其余固定字段",
          first["i"] == 5 and first["et"] == "loadeddata" and first["p"] == "web"
          and first["n"] == "ali-cdn.xuetangx.com" and first["lob"] == "cloud4"
          and first["sp"] == 2 and first["fp"] == 0 and first["tp"] == 0
          and first["t"] == "video" and first["d"] == 4976.5)
    check("5.8 cc/pg/ts",
          first["cc"] == "111" and first["pg"].startswith("111_") and len(first["pg"]) == 8
          and first["ts"].isdigit())

    # ============================================================ [6] watch_video
    section("watch_video：完成 / 跳过 / 中断 / 退避 / 停滞")
    sleeps = []
    client._sleep = lambda seconds: sleeps.append(seconds)

    progress_log = []
    ok = client.watch_video("111", "视频A", 9001, 5001, "sku-1", on_progress=progress_log.append)
    rates = [p["rate"] for p in progress_log]
    check("6.1 rate>=0.95 停止并返回 True", ok is True and rates and rates[-1] >= 0.95,
          str(rates))
    check("6.2 on_progress 载荷完整且递增",
          rates == sorted(rates)
          and progress_log[-1]["video_id"] == "111"
          and progress_log[-1]["video_name"] == "视频A"
          and "watch_length" in progress_log[-1])
    check("6.3 两批心跳打完（0→0.5→0.99）", state["heartbeats"].get("111") == 2,
          str(state["heartbeats"].get("111")))

    state["completed_videos"].add("222")
    progress_log2 = []
    ok = client.watch_video("222", "已看完的视频", 9001, 5001, "sku-1",
                            on_progress=progress_log2.append)
    check("6.4 completed==1 时跳过并返回 True",
          ok is True and state["heartbeats"].get("222", 0) == 0
          and progress_log2 and progress_log2[0]["rate"] == 1.0)

    ok = client.watch_video("444", "被中断的视频", 9001, 5001, "sku-1",
                            should_stop=lambda: True)
    check("6.5 should_stop 立刻中断并返回 False",
          ok is False and state["heartbeats"].get("444", 0) == 0)

    state["blocking_left"]["555"] = 1
    before_apply = state["problem_apply"]
    ok = client.watch_video("555", "被阻塞的视频", 9001, 5001, "sku-1")
    check("6.6 网络阻塞退避：problem_apply 被调用", state["problem_apply"] == before_apply + 1
          and ok is True)
    check("6.7 网络阻塞退避：睡 delay+0.5（3.5 秒）", 3.5 in sleeps, str(sleeps))

    check("6.8 心跳包用 json= 提交", any(
        call["url"].endswith("/video-log/heartbeat/") and "json" in call["kwargs"]
        for call in fake_requests.calls))

    state["stalled_videos"].add("333")
    ok = client.watch_video("333", "停滞视频", 9001, 5001, "sku-1")
    check("6.9 进度停滞触发保护并返回 False", ok is False
          and state["heartbeats"].get("333") == client.max_stall,
          str(state["heartbeats"].get("333")))

    state["missing_progress"].add("666")
    prog = client.get_video_progress(9001, 5001, "666")
    check("6.10 进度响应非 JSON 时给安全默认值",
          prog == {"completed": False, "rate": 0.0, "watch_length": 0}, str(prog))
    state["missing_progress"].add("777")
    prog = client.get_video_progress(9001, 5001, "777")
    check("6.11 字段缺失不抛异常", isinstance(prog, dict) and prog["rate"] == 0.0)

    # ============================================================ [7] 整门课
    section("watch_course_videos 统计与中断")
    state["chapter"] = CHAPTER_WATCH
    state["heartbeats"] = {}
    state["problem_apply"] = 0
    starts = []
    progress_all = []
    result = client.watch_course_videos(
        5001, "sign-aaa", 9001, "sku-1",
        on_progress=progress_all.append,
        on_video_start=lambda vid, name: starts.append((vid, name)),
    )
    check("7.1 统计 total/done/skipped/failed",
          result["total"] == 3 and result["done"] == 1 and result["skipped"] == 1
          and result["failed"] == ["停滞视频"], str(result))
    check("7.2 on_video_start 每个视频一次",
          len(starts) == 3 and starts[0] == ("311", "会完成的视频"), str(starts))
    check("7.3 on_progress 被多个视频调用",
          len({p["video_id"] for p in progress_all}) >= 2,
          str(sorted({p["video_id"] for p in progress_all})))

    state["heartbeats"] = {}
    starts2 = []
    flag = {"n": 0}

    def stop_after_first():
        flag["n"] += 1
        return flag["n"] > 1

    result2 = client.watch_course_videos(
        5001, "sign-aaa", 9001, "sku-1",
        should_stop=stop_after_first,
        on_video_start=lambda vid, name: starts2.append((vid, name)),
    )
    check("7.4 should_stop 提前中断整门课（被中断的视频不计入 failed）",
          len(starts2) == 1 and result2["total"] == 3 and result2["done"] == 0
          and result2["skipped"] == 0 and result2["failed"] == [],
          str(result2) + " starts=" + str(starts2))

    # ============================================================ [8] 扫码登录
    section("login_by_qrcode（假 WebSocket + 真 vendor 二维码）")
    fake_ws = types.SimpleNamespace(WebSocketApp=FakeWebSocketApp)
    yt._load_websocket = lambda: fake_ws

    FakeWebSocketApp.instances = []
    FakeWebSocketApp.script = [
        json.dumps({"ticket": "tk-1", "qrcode": "https://xuetangx.com/login/qr-xyz"}),
        json.dumps({"op": "loginsuccess", "Auth": "auth-token-1", "UserID": USER_ID}),
    ]
    qr_images = []
    login = yt.YuketangClient.login_by_qrcode(
        "buu.yuketang.cn", on_qrcode=qr_images.append, timeout=10)
    check("8.1 登录结果四字段",
          set(login) == {"cookie", "user_id", "university_id", "realname"}, str(sorted(login)))
    check("8.2 cookie 从 Set-Cookie 汇总",
          "sessionid=sess-xyz" in login["cookie"] and "csrftoken=csrf-abc" in login["cookie"],
          login["cookie"])
    check("8.3 user_id / university_id / realname",
          login["user_id"] == str(USER_ID) and login["university_id"] == str(UNIVERSITY_ID)
          and login["realname"] == REALNAME, str(login))
    check("8.4 on_qrcode 收到 PNG 字节",
          len(qr_images) == 1 and qr_images[0][:4] == PNG_MAGIC, str(len(qr_images)))
    ws_instance = FakeWebSocketApp.instances[-1] if FakeWebSocketApp.instances else None
    check("8.5 打开的是 wss://{domain}/wsapp/",
          ws_instance is not None and ws_instance.url == "wss://buu.yuketang.cn/wsapp/",
          ws_instance.url if ws_instance else "no instance")
    check("8.6 发过 requestlogin 且登录后关闭连接",
          ws_instance is not None
          and any('"requestlogin"' in payload for payload in ws_instance.sent)
          and ws_instance.closed is True)

    FakeWebSocketApp.instances = []
    FakeWebSocketApp.script = []
    calls = {"n": 0}

    def stop_later():
        calls["n"] += 1
        return calls["n"] > 1

    cancel_error = None
    try:
        yt.YuketangClient.login_by_qrcode(
            "buu.yuketang.cn", on_qrcode=lambda data: None,
            should_stop=stop_later, timeout=10)
    except yt.YuketangError as exc:
        cancel_error = exc
    check("8.7 should_stop → YuketangError(已取消)",
          cancel_error is not None and "已取消" in str(cancel_error),
          str(cancel_error))
    check("8.8 取消后 websocket 被关闭",
          bool(FakeWebSocketApp.instances) and FakeWebSocketApp.instances[-1].closed is True)

    # ============================================================ [9] 收尾
    section("close 与异常类型")
    client.close()
    check("9.1 close 可重复调用", (client.close() or True) is True)
    check("9.2 YuketangError 是 Exception 子类", issubclass(yt.YuketangError, Exception))

    print("")
    print("===== 结果：" + str(PASSES) + " 项通过，" + str(len(FAILS)) + " 项失败 =====")
    for name in FAILS:
        print("  FAIL: " + name)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
