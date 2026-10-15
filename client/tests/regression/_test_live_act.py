# -*- coding: utf-8 -*-
"""课堂互动（签到 / 主题讨论 / 评分 / 投票 / 问卷 / 随堂练习 + 雨课堂课堂互动）回归。

覆盖
----
* 加密与签名实现：AES-ECB / MD5 / inf_enc / signToken / 设备指纹 / App UA
* ``CXLive`` 各接口的请求参数构造（把 HTTP 打桩，不联网）
* 活动模型与页面纯函数：parse_quiz_html / 自动填答案
* ``YKTLive`` 的签到与答题 payload
* 导航装配（「课堂互动」页已挂上侧栏）

跑法：.venv\\Scripts\\python.exe tests/regression/_test_live_act.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("QT_QPA_PLATFORM", None)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from api import cx_live as L          # noqa: E402
from api import ykt_live as Y         # noqa: E402

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


# ---------------------------------------------------------------- 打桩工具

def make_live(json_result=None, text_result="success"):
    """造一个不打网络的 CXLive：把 _text / _json 换成记账桩。"""
    live = L.CXLive(session=None, uid="1001", fid="1024", name="张三")
    live.calls = []
    live.json_result = {} if json_result is None else json_result

    def _text(method, url, **kw):
        live.calls.append({"method": method, "url": url, "kwargs": kw})
        return text_result

    def _json(method, url, **kw):
        live.calls.append({"method": method, "url": url, "kwargs": kw})
        return live.json_result

    live._text = _text
    live._json = _json
    return live


def last_kwargs(live):
    return live.calls[-1]["kwargs"]


# ---------------------------------------------------------------- 测试主体

def main() -> int:
    # =============================================== [1] 加密与签名
    print("[1] 加密与签名实现")
    check("md5('abc')", L.md5_hex("abc") == "900150983cd24fb0d6963f7d28e17f72")
    check("md5(整数)",
          L.md5_hex(123) == "202cb962ac59075b964b07152d234b70")
    # 已知向量（与 Node/OpenSSL 的 aes-128-ecb 逐字节一致，见本次实现时的对照实验）
    check("AES-ECB 16 字节向量",
          L.aes_ecb_encrypt("0123456789abcdef", "QrCbNY@MuK1X8HGw")
          == "yqvf0PZUdZdQJTjr10L9XMo+3W2FKfGichnoLiFd2iU=")
    check("AES-ECB PKCS7 补齐",
          L.aes_ecb_encrypt("hello", "QrCbNY@MuK1X8HGw")
          == "nj6lp1Ff4po70osSTP2/jA==")
    check("设备指纹长度 = 64", len(L.device_code()) == 64)
    check("unique_id = 32 位十六进制",
          len(L.unique_id()) == 32 and all(c in "0123456789abcdef" for c in L.unique_id()))
    check("dart_quote 保留 !*'()",
          L._dart_quote("a b&c=d!*'()~") == "a%20b%26c%3Dd!*'()~")
    check("dart_quote 中文转 UTF-8",
          L._dart_quote("中文") == "%E4%B8%AD%E6%96%87")

    print("[2] inf_enc / signToken")
    enc = L.enc_params({"courseId": "123"})
    check("enc_params 四键",
          set(enc) == {"_c_0_", "token", "_time", "inf_enc"})
    check("inf_enc 是 32 位 hex",
          len(enc["inf_enc"]) == 32 and all(c in "0123456789abcdef" for c in enc["inf_enc"]))
    check("token 常量", enc["token"] == L.INF_ENC_TOKEN)
    # inf_enc = md5(按插入序拼「业务参数 + enc 参数」的 query + &DESKey=盐)
    merged = {"courseId": "123", "_c_0_": enc["_c_0_"], "token": enc["token"],
              "_time": enc["_time"]}
    qs = "&".join(f"{L._dart_quote(k)}={L._dart_quote(v)}" for k, v in merged.items())
    check("inf_enc 可复算",
          enc["inf_enc"] == L.md5_hex(f"{qs}&DESKey={L.INF_ENC_KEY}"))
    check("sign_token 键序无关",
          L.sign_token({"a": "1", "b": "2"}) == L.sign_token({"b": "2", "a": "1"}))
    check("sign_token 空盐已知值",
          L.sign_token({"a": "1", "b": "2"}, "SALT")
          == "8abb6829f52c7ad9ab562e8b21de71da")
    check("sign_token 空值当空串",
          L.sign_token({"cxtime": "1", "cxcid": None}) == L.sign_token({"cxtime": "1", "cxcid": ""}))

    print("[3] App UA（移动端接口会校验）")
    ua = L.app_user_agent()
    check("含 ChaoXingStudy 版本", "ChaoXingStudy_3_6.7.4" in ua)
    check("含 schild 签名", "schild:" in ua)
    check("含 Dalvik 系统段", "Dalvik/2.1.0" in ua)

    # =============================================== [4] 活动模型
    print("[4] 活动模型")
    act = L.Active.from_json({"id": 999, "activeType": "42", "nameOne": "随堂练习一",
                              "nameTwo": "剩余:5分钟", "startTime": 1791794100000, "status": 1})
    check("id 转字符串", act.id == "999")
    check("activeType 转 int", act.type == 42)
    check("name 取 nameOne", act.name == "随堂练习一")
    check("status 1 -> True", act.status is True)
    check("类型中文名", act.type_name == "随堂练习")
    check("非签到活动 is_sign=False", act.is_sign is False)
    sign_act = L.Active.from_json({"id": 1, "activeType": 2, "nameOne": "签到"})
    sign_act.sign_type_index = 4
    check("签到活动 is_sign=True", sign_act.is_sign is True)
    check("签到子类型名", sign_act.label == "位置签到")
    check("签到子类型键", sign_act.sign_type == "location")
    check("签到类型映射表",
          L.SIGN_TYPE_INDEX == {0: "normal", 2: "qrcode", 3: "pattern", 4: "location", 5: "code"})
    check("未知类型回落", L.active_type_name(12345) == "课堂活动")

    # =============================================== [5] 签到请求构造
    print("[5] 签到请求构造")
    course = {"courseId": "777", "clazzId": "888", "cpi": "9"}
    live = make_live()
    live.sign_normal(course, "555")
    kw = last_kwargs(live)
    check("普通签到走 GET", live.calls[-1]["method"] == "GET")
    check("普通签到 URL", live.calls[-1]["url"] == L.SIGN_URL)
    check("普通签到带 activeId", kw["params"]["activeId"] == "555")
    check("普通签到带 courseId", kw["params"]["courseId"] == "777")
    check("普通签到带 deviceCode", bool(kw["params"]["deviceCode"]))
    check("普通签到不带 objectId（无图时）", "objectId" not in kw["params"])

    live = make_live()
    live.sign_normal(course, "555", object_id="obj-1")
    check("拍照签到带 objectId", last_kwargs(live)["params"]["objectId"] == "obj-1")

    live = make_live()
    live.sign_code(course, "555", "1478")
    kw = last_kwargs(live)
    check("签到码带 signCode", kw["params"]["signCode"] == "1478")
    check("无定位时不带 location", "location" not in kw["params"])

    live = make_live()
    live.sign_code(course, "555", "1478", address="学校", latitude=38.0338, longitude=114.51)
    kw = last_kwargs(live)["params"]
    check("带定位 -> latitude 6 位小数", kw["latitude"] == "38.033800")
    check("带定位 -> longitude 6 位小数", kw["longitude"] == "114.510000")
    check("带定位 -> location JSON", json.loads(kw["location"])["address"] == "学校")
    check("带定位 -> locationResult 有 signToken",
          bool(json.loads(kw["locationResult"])["signConfig"]["signToken"]))

    live = make_live()
    live.sign_location(course, "555", "教学楼", 38.0, 114.5)
    check("位置签到走 POST", live.calls[-1]["method"] == "POST")
    body = last_kwargs(live)["data"]
    check("位置签到 ifTiJiao=1", body["ifTiJiao"] == "1")
    check("位置签到带 locationResult", bool(json.loads(body["locationResult"])["signConfig"]))
    check("位置签到 currentFaceId 空", body["currentFaceId"] == "")

    live = make_live()
    live.sign_location(course, "555", "教学楼", 38.0, 114.5, face_id="F1", face_enc="E1")
    body = last_kwargs(live)["data"]
    check("人脸签到 currentFaceId", body["currentFaceId"] == "F1")
    check("人脸签到 ifCFP=1", body["ifCFP"] == "1")
    check("人脸签到 faceEnc", body["faceEnc"] == "E1")

    live = make_live()
    live.sign_qrcode(course, "555", "ENC-X")
    kw = last_kwargs(live)["params"]
    check("二维码签到带 enc", kw["enc"] == "ENC-X")
    check("二维码签到默认 location 空", kw["location"] == "")

    live = make_live(json_result={"result": 1, "data": {"status": 1}})
    check("attend_info 取 data", (live.attend_info("555") or {}).get("status") == 1)
    live = make_live(json_result={"errorMsg": None})
    check("check_sign_code 通过返回 None", live.check_sign_code("555", "1234") is None)
    live = make_live(json_result={"errorMsg": "手势不正确"})
    check("check_sign_code 失败回错误", live.check_sign_code("555", "1234") == "手势不正确")

    print("[6] 群聊签到 / 讨论 / 评分")
    live = make_live()
    live.group_sign("9001", object_id="Obj")
    check("群聊签到走独立 URL", live.calls[-1]["url"] == L.GROUP_SIGN_URL)
    check("群聊签到带 objectId", last_kwargs(live)["params"]["objectId"] == "Obj")

    live = make_live(json_result={"result": 1, "data": {"uuid": "U1", "list": []}})
    live.topic_detail("T1")
    kw = last_kwargs(live)
    check("讨论 getTopic 是 POST", live.calls[-1]["method"] == "POST")
    check("讨论 inf_enc 只含空业务签名",
          set(kw["params"]) == {"_c_0_", "token", "_time", "inf_enc"})
    check("讨论业务参数走 body", kw["data"]["topicId"] == "T1")

    live = make_live(json_result={"result": 1, "errorMsg": None})
    check("发帖成功返回 None", live.topic_add_reply("U1", "这是十个字以上的回复") is None)
    kw = last_kwargs(live)
    check("发帖 inf_enc 含业务参数", "puid" in kw["params"] and "topicUUID" in kw["params"])
    check("发帖匿名开关", kw["params"]["anonymous"] == "0")

    live = make_live(json_result={"result": 1})
    check("评分提交成功", live.evaluate_submit("A", "C", "K", 90, content="好", score_list=[40, 50]) is True)
    body = last_kwargs(live)["data"]
    check("评分 scoreList 是 JSON 串", json.loads(body["scoreList"]) == [40, 50])

    print("[7] 随堂练习 / 投票 / 问卷")
    live = make_live(json_result={"result": 1})
    live.quiz_submit("C1", "K1", "A1", '[{"id":"1"}]')
    kw = last_kwargs(live)
    check("随堂练习答案走 body", kw["data"] == b'[{"id":"1"}]')
    check("随堂练习 Content-Type=json",
          kw["headers"]["Content-Type"] == "application/json")

    live = make_live(json_result={"status": 1})
    check("投票提交", live.vote_submit("K", "C", "A", "Q1", "A") == {"status": 1})
    check("投票 option 字段", last_kwargs(live)["data"]["option"] == "A")

    live = make_live(json_result={"result": 1})
    live.questionnaire_submit("K", "C", "A", {"Q1": ["A"], "Q2": ["B", "C"]})
    body = last_kwargs(live)["data"].decode("utf-8")
    check("问卷 preventsubmit=1", body.startswith("preventsubmit=1"))
    check("问卷多选逐项 answer", "&answerQ2=B&answerQ2=C" in body)

    live = make_live(json_result={"status": 1})
    check("随堂练习状态开启", live.quiz_check_status("C", "A") is True)

    # =============================================== [8] 页面纯函数
    print("[8] 页面纯函数")
    from gui_app.page_live import (  # noqa: E402
        parse_quiz_html, _autofill_quiz, _strip_html, _sign_result_text,
    )
    html = ('var quizList = [{"id":"1","type":0,"content":"<p>1+1</p>",'
            '"answer":[{"name":"A","content":"2","isanswer":true},'
            '{"name":"B","content":"3","isanswer":false}],"personAnswer":{}}];'
            'var active = {"id":"99"}; activeStatus: undefined,')
    ql, act_data, submitted = parse_quiz_html(html)
    check("抠出 1 道题", len(ql) == 1)
    check("activeStatus undefined -> 未提交", submitted is False)
    check("抠出 active", act_data.get("id") == "99")
    _autofill_quiz(ql)
    check("单选自动抄正确答案", ql[0]["personAnswer"]["myoption"] == "A")
    check("HTML 去标签", _strip_html("<p>a<b>b</b></p>") == "a b")
    check("success 文案", _sign_result_text("success") == "✓ 签到成功")
    check("validate 提示验证码", _sign_result_text("validate_zz").startswith("需要验证码"))
    check("空响应提示", "无响应" in _sign_result_text(""))

    # =============================================== [8b] 二维码签到已撤除
    print("[8b] 二维码签到已撤除")
    import importlib.util  # noqa: E402
    check("qr_scanner 模块已删除",
          importlib.util.find_spec("gui_app.qr_scanner") is None)

    # =============================================== [9] 雨课堂课堂互动
    print("[9] 雨课堂 YKTLive")

    class _Resp:
        def __init__(self, payload, headers=None):
            self._p = payload
            self.headers = headers or {}
            self.status_code = 200

        def json(self):
            return self._p

    class _Client:
        def __init__(self, responses):
            self.responses = list(responses)
            self.calls = []

        def _url(self, path):
            return "https://demo.yuketang.cn" + path

        def _request(self, method, url, **kw):
            self.calls.append({"method": method, "url": url, "kwargs": kw})
            item = self.responses.pop(0) if self.responses else {"code": 0}
            # 允许 (payload, headers) 二元组，用来模拟签到下发的 set-auth
            if isinstance(item, tuple):
                return _Resp(item[0], item[1])
            return _Resp(item)

    client = _Client([({"code": 0, "data": {"lessonToken": "LT"}},
                       {"set-auth": "BEARER123"})])
    live = Y.YKTLive(client)
    res = live.check_in("L1")
    check("签到 code=0", res.get("code") == 0)
    check("签到保存 lessonToken", res.get("lessonToken") == "LT")
    check("签到报 set-auth", live._bearer("L1") == "BEARER123")
    check("签到 payload source=21", client.calls[0]["kwargs"]["json"]["source"] == 21)
    check("签到 payload joinIfNotIn",
          client.calls[0]["kwargs"]["json"]["joinIfNotIn"] is True)

    client = _Client([{"code": 0, "data": {"lessonToken": "LT"}}, {"code": 0}])
    live = Y.YKTLive(client)
    res = live.answer("L1", "P1", 0, options=["A"])
    check("答题 problemId", client.calls[-1]["kwargs"]["json"]["problemId"] == "P1")
    check("答题 result 选项", client.calls[-1]["kwargs"]["json"]["result"] == ["A"])

    client = _Client([{"code": 0, "data": {"lessonToken": "LT"}}])
    live = Y.YKTLive(client)
    live.answer("L1", "P2", 5, content="主观作答")
    payload = client.calls[-1]["kwargs"]["json"]
    check("主观题 result.content", payload["result"]["content"] == "主观作答")
    check("主观题 pics 占位", payload["result"]["pics"] == [{"pic": "", "thumb": ""}])

    client = _Client([{"code": 0, "data": {"value": "L9"}}, {"code": 0, "data": {}}])
    live = Y.YKTLive(client)
    res = live.scan("https://x/q")
    check("扫码两步（scan + checkin）", len(client.calls) == 2)
    check("扫码拿到 lessonId 后签到", client.calls[1]["kwargs"]["json"]["lessonId"] == "L9")

    client = _Client([{"code": 0, "data": {"onLessonClassrooms": [{"lessonId": "L1"}]}}])
    live = Y.YKTLive(client)
    check("拉正在上课", len(live.on_lesson()) == 1)

    check("vendor 可用", Y.vendor_available() is True)

    # =============================================== [10] 导航装配
    print("[10] 导航装配")
    try:
        from gui_app.main_window import PAGE_KEYS, NAV  # noqa: E402
        check("导航含 live 页", "live" in PAGE_KEYS)
        groups = {g: [i[0] for i in items] for g, items in NAV}
        check("live 归入「学习通」组", "live" in groups.get("学习通", []))
    except Exception as exc:
        check("导航装配可导入", False, f"{type(exc).__name__}: {exc}")

    try:
        from gui_app.page_yuketang import YuketangPage  # noqa: E402
        check("雨课堂页有课堂互动", hasattr(YuketangPage, "_build_live"))
    except Exception as exc:
        check("雨课堂页可导入", False, f"{type(exc).__name__}: {exc}")

    # =============================================== [11] 新活动监控（轮询替代 IM 推送）
    print("[11] 新活动监控")
    import shutil  # noqa: E402  (tempfile 顶部已导入；这里再局部导入会把全函数的 tempfile 遮成局部变量)

    import gui_app.store as S  # noqa: E402

    tmp = tempfile.mkdtemp(prefix="leke_livewatch_")
    # 真实档案的指纹：本组用例跑完必须一字未动
    _real_profile = os.path.join(ROOT, "accounts.json")
    _real_before = (open(_real_profile, encoding="utf-8").read()
                    if os.path.exists(_real_profile) else "")
    # ⚠ store.app_dir() 在源码态返回的是**项目根**（不是 cwd），chdir 没用！
    #   必须像 _test_chapters 那样把 app_dir 顶掉，否则本组用例会写穿真实
    #   accounts.json（曾把测试账号 acc_new 追加进用户档案）。
    _orig_app_dir = S.app_dir
    S.app_dir = lambda: tmp
    try:
        # 先落一份最小档案：让 ProfileBook.load() 里的 migrate_if_needed 直接返回，
        # 免得它去读真实 config.ini（会把用户账号密码搬进临时文件）。
        with open(os.path.join(tmp, "accounts.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "active": "default",
                       "accounts": [{"id": "default", "name": "测试账号",
                                     "username": "138****0000", "password": "x"}]}, fh)

        from PySide6.QtWidgets import QApplication  # noqa: E402
        app = QApplication.instance() or QApplication([])
        from gui_app.page_live import LivePage  # noqa: E402

        page = LivePage()
        check("账号档案落在临时目录（未写穿真实文件）",
              page.book is not None and S.app_dir() == tmp)
        check("有「开启监控」勾选框", hasattr(page, "chk_watch"))
        check("监控默认关闭", page.chk_watch.isChecked() is False)
        check("默认间隔 30 秒", page.sp_watch.value() == 30)
        check("QTimer 已建好且未启动", page._watch is not None and not page._watch.isActive())
        page._apply_watch_interval(45)
        check("改间隔 -> 毫秒同步", page._watch.interval() == 45000)
        page._apply_watch_interval(3)
        check("间隔下限 10 秒", page._watch.interval() == 10000)
        page._apply_watch_interval(30)

        # 基线：同一个 id 第二次出现不算新活动
        course = {"title": "算法", "courseId": "777", "clazzId": "888", "cpi": "9"}
        base = [L.Active.from_json({"id": 1, "activeType": 2, "nameOne": "签到一"})]
        nxt = base + [L.Active.from_json({"id": 2, "activeType": 42, "nameOne": "练习二"})]
        page._seen_ids["777"] = {a.id for a in base}
        fresh = [a for a in nxt if a.id not in page._seen_ids["777"]]
        check("差集只报新 id", [a.id for a in fresh] == ["2"])
        page._alert_new(course, fresh)
        check("提示写入监控文案", "练习二" in page.lb_watch.text(), page.lb_watch.text())
        check("新活动也进日志", "🔔" in page.log_box.toPlainText())
        check("_alert_new 不弹模态窗（不阻塞）", app.activeModalWidget() is None)

        # 换课时基线要清掉，否则老活动会被当新活动报一遍
        page._load_activities = lambda: None     # 不真去联网（联网会起线程，退出时炸）
        page._seen_ids["777"] = {"1", "2"}
        page.course_box.blockSignals(True)
        page.course_box.addItem("算法", course)
        page.course_box.blockSignals(False)
        page._on_course_changed(0)
        check("换课清掉该课基线", page._seen_ids.get("777") is None)
        page.course_box.blockSignals(True)
        page.course_box.clear()
        page.course_box.blockSignals(False)
        page._on_course_changed(0)
        check("换课清基线（没选课也不炸）", page._seen_ids == {})

        # 回归：档案是**全 app 共用**的 accounts.json，本页 __init__ 时持有的只是快照。
        # 旧实现直接 snapshot.save() 会把别的页面此后新增的账号整个抹掉。
        from gui_app import profiles as prof  # noqa: E402
        other = prof.load()
        other.accounts.append({"id": "acc_new", "name": "新账号",
                               "username": "138****0000", "password": "x"})
        other.save()
        first_id = str(page.book.accounts[0].get("id") or "default")
        page._switch_account(first_id, load=False)
        after = {str(a.get("id")) for a in prof.load().accounts}
        check("切账号不抹掉别处新增的账号", "acc_new" in after, str(sorted(after)))
        check("切账号后 active 已落盘", prof.load().active == first_id)
    finally:
        S.app_dir = _orig_app_dir
        _real_after = (open(_real_profile, encoding="utf-8").read()
                       if os.path.exists(_real_profile) else "")
        check("真实 accounts.json 一字未动（用例已隔离）", _real_before == _real_after)
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"通过 {PASSES} 项，失败 {len(FAILS)} 项")
    if FAILS:
        print("失败清单：")
        for name in FAILS:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
