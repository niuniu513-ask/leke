# -*- coding: utf-8 -*-
"""智慧树接入自测（纯离线：不起网络、不构造页面）。

覆盖：vendor 包与模块卫生、pyaes 加解密往返（含中文）、签名 / 事件编解码、
Fucker 构造与 cookie 解析、fuckCourse 的两套 API 路由、适配层账号档案
（隔离 base_dir • 旧文件迁移）、账号信息打码、登录异常翻译、cookie 文本解析、
runner phase/信号/停止中断、注册表 get/drop/shutdown。

跑法：cd chaoxing-main; .venv/Scripts/python.exe tests/regression/_test_zhihuishu.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

# 用空 logger 顶替 api.logger：测试不落 leke.log，也不把日志混进输出。
# （注意：测试里不能调用 stream.install()，否则输出会被 StreamProxy 吞掉。）
_null_logger = types.ModuleType("api.logger")


class _NullLogger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


_null_logger.logger = _NullLogger()
sys.modules["api.logger"] = _null_logger

import api  # noqa: E402
api.logger = _null_logger

import api.zhihuishu as zhs  # noqa: E402
from api.zhihuishu import client as zc  # noqa: E402
from api.zhihuishu import zd_utils as zu  # noqa: E402

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


def main():
    # ============================================================ [1] 模块卫生
    section("vendor 包与模块卫生")
    check("1.1 顶层导出 Fucker / TimeLimitExceeded",
          zhs.Fucker is zc.Fucker and zhs.TimeLimitExceeded is zc.TimeLimitExceeded)
    check("1.2 TimeLimitExceeded 是 Exception 子类",
          issubclass(zhs.TimeLimitExceeded, Exception))

    zd_src = open(zu.__file__, encoding="utf-8").read()
    cl_src = open(zc.__file__, encoding="utf-8").read()
    check("1.3 zd_utils 不再依赖 Crypto（改用 pyaes）",
          "from Crypto" not in zd_src and "import Crypto" not in zd_src
          and "import pyaes" in zd_src)
    check("1.4 client 顶层无 Crypto 导入", "from Crypto" not in cl_src and "import Crypto" not in cl_src)
    check("1.5 client 走包内相对导入",
          "from .zd_utils import" in cl_src and "from ._logging import" in cl_src
          and "from .sign import" in cl_src)
    check("1.6 vendor 已去 CLI 入口（无 if __name__ 块）",
          all("if __name__" not in open(m.__file__, encoding="utf-8").read()
              for m in (zc, zu, __import__("api.zhihuishu.objdict", fromlist=["x"]),
                        __import__("api.zhihuishu.utils", fromlist=["x"]))))

    width = zc._term_width()
    check("1.7 _term_width 无终端也安全返回", isinstance(width, int) and width > 0, str(width))

    from api.zhihuishu import _logging as zl
    check("1.8 _logging 桥接 logger 接口齐全",
          all(hasattr(zl.logger, m) for m in
              ("debug", "info", "warning", "error", "critical", "exception",
               "setLevel", "getLevel")))
    check("1.9 桥接层不创建 ./logs 目录",
          not os.path.isdir(os.path.join(ROOT, "api", "zhihuishu", "logs"))
          and "_emit" in open(zl.__file__, encoding="utf-8").read())

    # ============================================================ [2] 加密
    section("AES 加解密与签名工具")
    cip = zu.Cipher()
    plain = "hello-zhihuishu"
    round_trip = cip.decrypt(cip.encrypt(plain))
    check("2.1 ASCII 加解密往返", round_trip == plain, round_trip)
    zh_plain = '{"courseName":"高等数学（上）"}'
    check("2.2 中文加解密往返（按字节填充修好了）",
          cip.decrypt(cip.encrypt(zh_plain)) == zh_plain)
    check("2.3 同一明文两次加密结果一致（固定 IV）",
          cip.encrypt(plain) == cip.encrypt(plain))
    check("2.4 密文是 base64（可解码）",
          len(__import__("base64").b64decode(cip.encrypt(plain))) % 16 == 0)
    check("2.5 HOME/VIDEO/QA 三把密钥各不相同",
          len({zu.HOME_KEY, zu.VIDEO_KEY, zu.QA_KEY}) == 3)

    check("2.6 getEv/revEv 往返（数值域）",
          zu.revEv(zu.getEv([1, 2, 3])) == "1;2;3")
    wp = zu.WatchPoint()
    wp.add(10)
    marks = wp.get().split(",")
    check("2.7 WatchPoint 生成观看点且以 0,1 起", marks[0] == "0" and marks[1] == "1"
          and len(marks) > 2, wp.get())

    from api.zhihuishu.sign import sign
    sig = sign({"uuid": "u", "courseId": "111", "fileId": "222",
                "studyTotalTime": "10", "startDate": "1", "endDate": "2",
                "endWatchTime": "3", "startWatchTime": "4"})
    check("2.8 sign 返回 32 位 md5", isinstance(sig, str) and len(sig) == 32
          and all(c in "0123456789abcdef" for c in sig), sig)

    # ============================================================ [3] Fucker
    section("Fucker 构造与 cookie")
    f = zc.Fucker()
    check("3.1 默认不使用系统代理", f.proxies == {}, str(f.proxies))
    check("3.2 显式代理原样保留",
          zc.Fucker(proxies={"http": "http://127.0.0.1:7890"}).proxies
          == {"http": "http://127.0.0.1:7890"})
    check("3.3 on_event 初始为 None（GUI 适配层再挂）", f.on_event is None)
    check("3.4 speed=None 保持默认", zc.Fucker(speed=None).speed is None)
    check("3.5 speed 归一", zc.Fucker(speed=2.0).speed == 2.0
          and zc.Fucker(speed=-1).speed == 0.1)
    check("3.6 end_thre 上限 1.0", zc.Fucker().end_thre == 0.91
          and zc.Fucker(end_thre=2.0).end_thre == 1.0)

    got = []
    f.on_event = lambda kind, data: got.append((kind, data))
    f._emit("video", name="第一节")
    check("3.7 _emit 回调载荷", got == [("video", {"name": "第一节"})], str(got))
    f.on_event = None
    check("3.8 on_event 为空时不抛", (f._emit("chapter", name="x") or True) is True)

    caslogc = urllib.parse.quote(json.dumps({"uuid": "u-abc-1"}))
    fc = zc.Fucker(cookies={"CASLOGC": caslogc})
    check("3.9 从 CASLOGC 解析 uuid", fc.uuid == "u-abc-1", str(fc.uuid))
    check("3.10 注入 exitRecod_<uuid>", fc.cookies.get("exitRecod_u-abc-1") == "2")
    bad = None
    try:
        zc.Fucker(cookies={"SOMETHING": "1"})
    except ValueError as exc:
        bad = exc
    check("3.11 非法 cookie 抛 ValueError('Cookies invalid')",
          bad is not None and "Cookies invalid" in str(bad), str(bad))

    # ============================================================ [4] 路由
    section("fuckCourse 两套 API 路由")
    r = zc.Fucker()
    routed = []
    r.fuckZhidaoCourse = lambda cid, tree_view=True: routed.append(("zhidao", cid))
    r.fuckHikeCourse = lambda cid, tree_view=True: routed.append(("hike", cid))
    r.fuckCourse("12a3b4c5")           # 含字母 = recruitAndCourseId → 共享课
    r.fuckCourse("12345678")           # 纯数字 = courseId → 校内课
    check("4.1 含字母走共享课（zhidao）", routed[0] == ("zhidao", "12a3b4c5"), str(routed))
    check("4.2 纯数字走校内课（hike）", routed[1] == ("hike", "12345678"), str(routed))

    # ============================================================ [5] 适配层档案
    section("适配层：账号档案（隔离 base_dir）")
    import gui_app.zhihuishu_engine as ze

    tmp = tempfile.mkdtemp(prefix="ze_test_")
    # 手动装一个显式 base_dir 的档案，避免碰开发机上的真实账号文件
    ze._STORE = ze.AccountStore(ze.ACCOUNTS_FILE, ze.ZHIHUISHU_FILE,
                                ze.ACCOUNT_DEFAULTS, base_dir=tmp).load()
    check("5.1 档案文件名隔离", os.path.basename(ze.accounts_path()) == "zhihuishu_accounts.json"
          and tmp in ze.accounts_path(), ze.accounts_path())
    first = ze.account_store().add("测试账号")
    check("5.2 新增账号带默认字段",
          first["username"] == "" and first["speed"] == ze.DEFAULT_SPEED
          and first["cookie"] == "", str(sorted(first)))
    ze.account_store().update(first["id"], username="13800000000", password="pw-secret")
    ze.account_store().set_active(first["id"])

    runner = ze.ZhihuishuRunner(first["id"])
    info = runner.account_info()
    check("5.3 account_info 字段齐全",
          set(info) == {"acc_id", "username", "realname", "password", "cookie",
                        "speed", "logged_in"}, str(sorted(info)))
    check("5.4 密码打码", info["password"] == "******", info["password"])
    check("5.5 未登录状态", info["logged_in"] is False and runner.phase == "idle")

    # 旧单账号文件迁移（显式 base_dir，仍不碰真实目录）
    tmp2 = tempfile.mkdtemp(prefix="ze_mig_")
    with open(os.path.join(tmp2, ze.ZHIHUISHU_FILE), "w", encoding="utf8") as fh:
        json.dump({"username": "old-user", "password": "old-pw"}, fh)
    migrated = ze.AccountStore(ze.ACCOUNTS_FILE, ze.ZHIHUISHU_FILE,
                               ze.ACCOUNT_DEFAULTS, base_dir=tmp2).load()
    check("5.6 旧 zhihuishu.json 自动迁移成第一条记录",
          len(migrated.accounts) == 1
          and migrated.accounts[0]["username"] == "old-user", str(migrated.accounts))
    check("5.7 迁移后旧文件改名 .migrated",
          os.path.exists(os.path.join(tmp2, ze.ZHIHUISHU_FILE + ".migrated")))

    # 空档案自动建「账号 1」（走 account_store() 的懒加载分支）
    tmp3 = tempfile.mkdtemp(prefix="ze_fresh_")
    _orig_app_dir = ze.store.app_dir
    ze.store.app_dir = lambda: tmp3
    try:
        ze.reset_store()
        auto = ze.account_store()
        check("5.8 空档案自动建一条「账号 1」",
              len(auto.accounts) == 1 and auto.accounts[0]["name"] == "账号 1",
              str([a.get("name") for a in auto.accounts]))
    finally:
        ze.store.app_dir = _orig_app_dir

    # ============================================================ [6] 工具函数
    section("适配层：cookie 解析 / 错误翻译 / 打码")
    parsed_json = ze.ZhihuishuRunner._parse_cookie_text('{"a": "1", "b": "2"}')
    check("6.1 解析 JSON 串", parsed_json == {"a": "1", "b": "2"}, str(parsed_json))
    parsed_kv = ze.ZhihuishuRunner._parse_cookie_text("k=v; k2=v2 ;; bad")
    check("6.2 解析浏览器 k=v 串（忽略残片）",
          parsed_kv == {"k": "v", "k2": "v2"}, str(parsed_kv))
    check("6.3 空串 → 空 dict", ze.ZhihuishuRunner._parse_cookie_text("") == {})

    check("6.4 账号或密码错误翻译",
          ze.humanize_login_error(Exception("Username or password invalid"))
          == "登录失败：账号或密码错误")
    captcha = ze.humanize_login_error(Exception("Multiple invalid attempts, requires captcha"))
    check("6.5 风控验证码翻译", "先在浏览器登录一次智慧树" in captcha, captcha)
    unknown = ze.humanize_login_error(Exception("boom-unknown"))
    check("6.6 未知错误原文透传", unknown == "登录失败：boom-unknown", unknown)
    check("6.7 cookie 打码（长串保留首尾）",
          ze.masked_cookie("abcdefghijklmnop") == "abcde******mnop",
          ze.masked_cookie("abcdefghijklmnop"))
    check("6.8 短 cookie 整体打码", ze.masked_cookie("abc") == "******")

    # ============================================================ [7] runner
    section("runner：状态 / 信号 / 停止中断 / 注册表")
    runner2 = ze.ZhihuishuRunner(first["id"])
    steps = []
    runner2.sig_login_step.connect(lambda acc, text: steps.append(text))
    runner2._step("1/3 连接")
    check("7.1 sig_login_step 分步骤上报", steps == ["1/3 连接"], str(steps))

    evs = []
    runner2.sig_progress.connect(lambda d: evs.append(d))
    runner2._on_engine_event("chapter", {"name": "第一章"})
    runner2._on_engine_event("video", {"name": "视频A", "video_id": "77"})
    runner2._on_engine_event("progress", {"video_id": "77", "played": 30, "total": 60})
    check("7.2 chapter 事件", evs[0]["phase"] == "chapter" and evs[0]["chapter"] == "第一章")
    check("7.3 video 事件", evs[1]["phase"] == "video" and evs[1]["video_id"] == "77"
          and evs[1]["rate"] == 0.0)
    check("7.4 progress 事件换算 rate",
          evs[2]["phase"] == "playing" and abs(evs[2]["rate"] - 0.5) < 1e-9, str(evs[2]))

    from api.zhihuishu import TimeLimitExceeded as _TLE
    runner2._tle = _TLE
    runner2._stop.set()
    stopped_err = None
    try:
        runner2._on_engine_event("chapter", {"name": "不该处理"})
    except _TLE as exc:
        stopped_err = exc
    check("7.5 已停止时抛 TimeLimitExceeded 中断课程",
          stopped_err is not None and "已停止" in str(stopped_err), str(stopped_err))
    runner2._stop.clear()

    before = len(evs)
    runner2._on_engine_event("progress", {"played": 0, "total": 0})
    check("7.6 total=0 时 rate 安全为 0", evs[-1]["rate"] == 0.0 and len(evs) == before + 1)

    r_a = ze.get_runner("acc-A")
    r_a2 = ze.get_runner("acc-A")
    r_b = ze.get_runner("acc-B")
    check("7.7 同 acc_id 复用同一 runner", r_a is r_a2)
    check("7.8 不同 acc_id 各自独立", r_a is not r_b and r_a.acc_id == "acc-A")
    check("7.9 all_runners 快照包含两者",
          {"acc-A", "acc-B"}.issubset(set(ze.all_runners().keys())),
          str(sorted(ze.all_runners().keys())))

    ze.drop_runner("acc-B")
    check("7.10 drop_runner 摘除实例",
          "acc-B" not in ze.all_runners() and "acc-A" in ze.all_runners())

    runner2.start([])
    check("7.11 start([]) 不发线程（没勾选课程）", runner2.busy() is False)
    runner2.shutdown()
    check("7.12 shutdown 置停止位", runner2._stop.is_set())
    ze.shutdown_all()
    check("7.13 shutdown_all 把全部 runner 停下来",
          all(r._stop.is_set() for r in ze.all_runners().values()))

    print("")
    print("===== 结果：" + str(PASSES) + " 项通过，" + str(len(FAILS)) + " 项失败 =====")
    for name in FAILS:
        print("  FAIL: " + name)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
