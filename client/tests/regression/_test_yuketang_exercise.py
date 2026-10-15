# -*- coding: utf-8 -*-
"""雨课堂「作业 / 章节测验」答题自测（纯离线：mock requests）。

覆盖：
  * 题型归一（SingleChoice/MultipleChoice/FillBlank/TrueFalse → 五档）；
  * 答案归一（选项 key / 选项正文 / 「正确」 / 多选连写 AB / 无法匹配 → None）；
  * 提交请求体三类格式（单选多选 answer:[..]、填空 answers:{..}）；
  * 章节树里筛 leaf_type==6（含嵌套 leaf_list）；
  * get_exercise_problems 两步（leaf_info → get_exercise_list）；
  * submit_answer 打到的 URL 与 body；
  * solve_exercise 编排（自带答案优先 / 无答案跳过 / 已提交跳过 / 失败计数）。

跑法：cd chaoxing-main; .venv/Scripts/python.exe tests/regression/_test_yuketang_exercise.py
"""
from __future__ import annotations

import json
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

import api  # noqa: E402

# 空 logger 顶替 api.logger：测试不落 leke.log，也不把日志混进输出。
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


UNIVERSITY_ID = 3325
USER_ID = 12345
COOKIE = "csrftoken=csrf-abc; sessionid=sess-xyz"


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
    def __init__(self, router):
        self.router = router
        self.session = FakeSession(router)
        self.calls = self.session.calls
        self.exceptions = types.SimpleNamespace(
            Timeout=TimeoutError, ConnectionError=ConnectionError,
            ChunkedEncodingError=ConnectionError, RequestException=Exception,
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


# --------------------------------------------------------------------- 测试数据
CHAPTER = {
    "data": {
        "course_chapter": [
            {
                "id": 1,
                "name": "第一章",
                "section_leaf_list": [
                    {"id": 111, "name": "1.1 视频", "leaf_type": 0},
                    {"id": 900, "name": "1.2 章节测验", "leaf_type": 6},
                    {
                        "id": 2,
                        "name": "1.3 小节",
                        "leaf_type": None,
                        "leaf_list": [
                            {"id": 901, "name": "1.3.1 作业", "leaf_type": 6},
                        ],
                    },
                ],
            },
        ]
    }
}

LEAF_INFO = {
    "success": True,
    "data": {
        "name": "1.2 章节测验",
        "sku_id": "sku-1",
        "content_info": {"leaf_type_id": 7788},
    }
}

# 三道题：单选 / 多选 / 填空；含接口自带答案与一道无答案
PROBLEMS = {
    "success": True,
    "data": {
        "name": "1.2 章节测验",
        "font": "",
        "problems": [
            {
                "problem_id": 1001,
                "submission_status": 0,
                "user": {"answer": "B"},
                "content": {
                    "ProblemID": 1001, "Type": "SingleChoice", "TypeText": "单选题",
                    "Body": "<p>中国的首都是？</p>", "Score": 2,
                    "Options": [{"key": "A", "value": "上海"}, {"key": "B", "value": "北京"}],
                },
            },
            {
                "problem_id": 1002,
                "submission_status": 0,
                "user": {},
                "content": {
                    "ProblemID": 1002, "Type": "MultipleChoice", "TypeText": "多选题",
                    "Body": "<p>以下属于水果的有？</p>", "Score": 3,
                    "Options": [
                        {"key": "A", "value": "苹果"}, {"key": "B", "value": "香蕉"},
                        {"key": "C", "value": "石头"},
                    ],
                },
            },
            {
                "problem_id": 1003,
                "submission_status": 0,
                "user": {},
                "content": {
                    "ProblemID": 1003, "Type": "FillBlank", "TypeText": "填空题",
                    "Body": "<p>1 + 1 = ___</p>", "Score": 2, "Options": [],
                },
            },
        ],
    }
}


def make_client(router):
    fake = FakeRequests(router)
    yt.requests = fake
    client = yt.YuketangClient("buu.yuketang.cn", cookie=COOKIE,
                               user_id=str(USER_ID), university_id=str(UNIVERSITY_ID))
    return client, fake


def default_router(method, url, kwargs):
    if "/learn/course/chapter" in url:
        return FakeResponse(CHAPTER)
    if "/learn/leaf_info/" in url:
        return FakeResponse(LEAF_INFO)
    if "/exercise/get_exercise_list/" in url:
        return FakeResponse(PROBLEMS)
    if "/exercise/problem_apply/" in url:
        return FakeResponse({"success": True, "data": {"is_correct": True}})
    return FakeResponse(text="not found", status=404)


# ============================================================ 1. 题型归一
section("题型归一 _answer_type_of")
check("1.1 SingleChoice → single", yt._answer_type_of("SingleChoice") == "single")
check("1.2 MultipleChoice → multiple", yt._answer_type_of("MultipleChoice") == "multiple")
check("1.3 FillBlank → fill", yt._answer_type_of("FillBlank") == "fill")
check("1.4 TrueFalse → judge", yt._answer_type_of("TrueFalse") == "judge")
check("1.5 Judge → judge", yt._answer_type_of("Judge") == "judge")
check("1.6 空 → unknown", yt._answer_type_of("") == "unknown")
check("1.7 FillBlankQuestion 含关键字 → fill",
      yt._answer_type_of("FillBlankQuestion") == "fill")

# ============================================================ 2. 答案归一
section("答案归一 _normalize_choice_answer")
OPTS = [{"key": "A", "value": "北京"}, {"key": "B", "value": "上海"}, {"key": "C", "value": "广州"}]
check("2.1 单选 key", yt._normalize_choice_answer("A", "single", OPTS) == ["A"])
check("2.2 单选 选项正文", yt._normalize_choice_answer("北京", "single", OPTS) == ["A"])
check("2.3 多选连写 AB", yt._normalize_choice_answer("AB", "multiple", OPTS) == ["A", "B"])
check("2.4 多选逗号 A,B", yt._normalize_choice_answer("A,B", "multiple", OPTS) == ["A", "B"])
check("2.5 多选正文顿号", yt._normalize_choice_answer("上海、广州", "multiple", OPTS) == ["B", "C"])
check("2.6 无法匹配 → None", yt._normalize_choice_answer("Z", "single", OPTS) is None)
check("2.7 无选项 → None", yt._normalize_choice_answer("A", "single", []) is None)
check("2.8 空答案 → None", yt._normalize_choice_answer("", "single", OPTS) is None)

JOPTS = [{"key": "A", "value": "正确"}, {"key": "B", "value": "错误"}]
check("2.9 判断「正确」", yt._normalize_choice_answer("正确", "judge", JOPTS) == ["A"])
check("2.10 判断「错」", yt._normalize_choice_answer("错", "judge", JOPTS) == ["B"])
check("2.11 判断「对」", yt._normalize_choice_answer("对", "judge", JOPTS) == ["A"])
JOPTS_NUM = [{"key": "1", "value": "对"}, {"key": "2", "value": "错"}]
check("2.12 判断 key 是 1/2", yt._normalize_choice_answer("对", "judge", JOPTS_NUM) == ["1"])
check("2.13 判断无文字靠位置",
      yt._normalize_choice_answer("正确", "judge", [{"key": "A"}, {"key": "B"}]) == ["A"])

# ============================================================ 3. 提交请求体
section("提交请求体 _build_submit_payload")
p = yt._build_submit_payload(123, 55, ["答案1", "答案2"], "fill")
check("3.1 填空用 answers 字段 + 1 起 key",
      p == {"classroom_id": 123, "problem_id": 55, "answers": {"1": "答案1", "2": "答案2"}})
p = yt._build_submit_payload(123, 55, {"1": "x", "2": "y"}, "fill")
check("3.2 填空 dict 直传", p == {"classroom_id": 123, "problem_id": 55,
                                "answers": {"1": "x", "2": "y"}})
p = yt._build_submit_payload(123, 55, "A,B", "multiple")
check("3.3 多选 answer 列表", p == {"classroom_id": 123, "problem_id": 55,
                                   "answer": ["A", "B"]})
p = yt._build_submit_payload(123, 55, "A", "single")
check("3.4 单选 answer 列表", p == {"classroom_id": 123, "problem_id": 55, "answer": ["A"]})
check("3.5 空答案 → None", yt._build_submit_payload(123, 55, "", "single") is None)
check("3.6 无 problem_id → None", yt._build_submit_payload(123, None, "A", "single") is None)
check("3.7 classroom_id 是字符串也能转 int",
      yt._build_submit_payload("123", 55, "A", "single")["classroom_id"] == 123)

# ============================================================ 4. 章节筛作业
section("章节筛 leaf_type==6")
out = []
for ch in CHAPTER["data"]["course_chapter"]:
    for leaf in ch["section_leaf_list"]:
        yt._collect_exercise_leaves(leaf, out)
ids = sorted(int(item["id"]) for item in out)
check("4.1 找到 2 个作业（含嵌套）", ids == [900, 901], str(ids))
check("4.2 视频 / 小节被排除", all(item["leaf_type"] == 6 for item in out))


# ============================================================ 5. 拉题两步
section("get_exercise_problems 两步链路")
client, fake = make_client(default_router)
res = client.get_exercise_problems(5001, 900)
check("5.1 返回作业名", res["name"] == "1.2 章节测验", res["name"])
check("5.2 返回 3 道题", len(res["problems"]) == 3)
check("5.3 leaf_type_id 从 leaf_info 取到", res["leaf_type_id"] == 7788)
urls = [c["url"] for c in fake.calls]
check("5.4 先打 leaf_info", any("/learn/leaf_info/5001/900/" in u for u in urls))
check("5.5 再打 get_exercise_list/7788", any("/get_exercise_list/7788/" in u for u in urls))

# ============================================================ 6. 提交单题
section("submit_answer 打点")
client, fake = make_client(default_router)
client.submit_answer(5001, 1003, {"1": "2"}, "fill")
post = [c for c in fake.calls if c["method"] == "POST"]
check("6.1 打到 problem_apply", post and "/exercise/problem_apply/" in post[-1]["url"])
body = post[-1]["kwargs"].get("json")
check("6.2 填空 body 用 answers", body == {"classroom_id": 5001, "problem_id": 1003,
                                         "answers": {"1": "2"}}, str(body))
check("6.3 返回 success", client.submit_answer(5001, 1001, "A", "single") is not None)

# 无法归一的答案 → YuketangError
try:
    client.submit_answer(5001, 1001, "", "single")
    raised = False
except yt.YuketangError:
    raised = True
check("6.4 空答案抛 YuketangError", raised)

# ============================================================ 7. solve_exercise 编排
section("solve_exercise 编排")


def answer_fn_with_chain(parsed):
    """模拟题库链：单选/多选/填空各给一个答案；用题型区分。"""
    if parsed.get("type") == "single":
        return "B"
    if parsed.get("type") == "multiple":
        return ["A", "B"]
    if parsed.get("type") == "fill":
        return {"1": "2"}
    return None


client, fake = make_client(default_router)
progress = []
res = client.solve_exercise(5001, 900, "sku-1",
                            answer_fn=answer_fn_with_chain,
                            on_progress=lambda p: progress.append(p),
                            delay_range=(0, 0))
check("7.1 统计 total=3", res["total"] == 3)
check("7.2 全部提交成功 done=3", res["done"] == 3, str(res["done"]))
check("7.3 无失败", res["failed"] == 0)
check("7.4 无跳过", res["skipped"] == 0)
check("7.5 回调被调用 3 次", len(progress) == 3)
check("7.6 提交了 3 次 POST",
      len([c for c in fake.calls if c["method"] == "POST"]) == 3)

# 自带答案优先（answer_fn 不该被调用到无答案的题）
calls = {"n": 0}


def counting_fn(parsed):
    calls["n"] += 1
    return "B" if parsed.get("type") == "single" else None


client, fake = make_client(default_router)
res = client.solve_exercise(5001, 900, answer_fn=counting_fn, delay_range=(0, 0))
check("7.7 无答案的题被跳过 skipped=2", res["skipped"] == 2, str(res["skipped"]))
check("7.8 只成功 1 题", res["done"] == 1)


# 已提交过的题跳过
def router_submitted(method, url, kwargs):
    if "/learn/course/chapter" in url:
        return FakeResponse(CHAPTER)
    if "/learn/leaf_info/" in url:
        return FakeResponse(LEAF_INFO)
    if "/exercise/get_exercise_list/" in url:
        payload = json.loads(json.dumps(PROBLEMS))
        payload["data"]["problems"][0]["submission_status"] = 1
        return FakeResponse(payload)
    return FakeResponse({"success": True})


client, fake = make_client(router_submitted)
res = client.solve_exercise(5001, 900, answer_fn=answer_fn_with_chain, delay_range=(0, 0))
check("7.9 已提交的题被跳过", res["skipped"] == 1, str(res["skipped"]))
check("7.10 其余两题照常提交", res["done"] == 2)


# 提交失败计入 failed
def router_fail(method, url, kwargs):
    if "/learn/course/chapter" in url:
        return FakeResponse(CHAPTER)
    if "/learn/leaf_info/" in url:
        return FakeResponse(LEAF_INFO)
    if "/exercise/get_exercise_list/" in url:
        return FakeResponse(PROBLEMS)
    if "/exercise/problem_apply/" in url:
        return FakeResponse({"success": False, "msg": "答案错误"})
    return FakeResponse(text="not found", status=404)


client, fake = make_client(router_fail)
res = client.solve_exercise(5001, 900, answer_fn=answer_fn_with_chain, delay_range=(0, 0))
check("7.11 提交失败计入 failed=3", res["failed"] == 3, str(res["failed"]))

# should_stop 中断
client, fake = make_client(default_router)
res = client.solve_exercise(5001, 900, answer_fn=answer_fn_with_chain,
                            should_stop=lambda: True, delay_range=(0, 0))
check("7.12 should_stop 立即返回 total 但 0 提交", res["done"] == 0)

# ============================================================ 8. parse_problem
section("parse_problem")
parsed = yt.YuketangClient.parse_problem(PROBLEMS["data"]["problems"][0])
check("8.1 problem_id", parsed["problem_id"] == 1001)
check("8.2 type=single", parsed["type"] == "single")
check("8.3 题干去标签", parsed["body"] == "中国的首都是？", parsed["body"])
check("8.4 选项归一", parsed["options"][1] == {"key": "B", "value": "北京"})
check("8.5 user 透传（自带答案）", parsed.get("user", {}).get("answer") == "B")
check("8.6 submitted 字段", parsed["submitted"] == 0)

parsed2 = yt.YuketangClient.parse_problem(PROBLEMS["data"]["problems"][2])
check("8.7 填空题型", parsed2["type"] == "fill")
check("8.8 填空 options 空", parsed2["options"] == [])

print("")
if FAILS:
    print("===== 结果：" + str(PASSES) + " 项通过，" + str(len(FAILS)) + " 项失败 =====")
    for name in FAILS:
        print("  - " + name)
    sys.stdout.flush()
    os._exit(1)
print("===== 结果：" + str(PASSES) + " 项通过，0 项失败 =====")
sys.stdout.flush()
os._exit(0)
