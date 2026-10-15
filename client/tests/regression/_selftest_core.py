# -*- coding: utf-8 -*-
"""核心逻辑自检（不依赖 PySide6）：config.ini 回写保注释 + 日志流解析。"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

ok_count = 0
fail_count = 0


def check(name, cond, extra=""):
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f"  [PASS] {name}")
    else:
        fail_count += 1
        print(f"  [FAIL] {name} {extra}")


def test_store():
    print("\n== store.update_values：保留注释与排版 ==")
    from gui_app import store

    tmp = os.path.join(tempfile.gettempdir(), "_selftest_config.ini")
    shutil.copyfile(os.path.join(ROOT, "config.ini"), tmp)
    # 模板可能没有通知小节；先准备旧值，随后只验证替换已有键不增行。
    store.update_values({("notification", "url"): ""}, path=tmp)
    before = open(tmp, encoding="utf8").read()
    n_before = before.count(";")
    lines_before = len(before.splitlines())

    changed = store.update_values({
        ("common", "speed"): "2.0",
        ("common", "jobs"): "6",
        ("common", "username"): "13900000000",
        ("tiku", "provider"): "TikuGo,TikuYanxi",
        ("tiku", "cover_rate"): "0.80",
        ("notification", "url"): "https://example.com/x",
        ("common", "target_count"): "250",
    }, path=tmp)

    after = open(tmp, encoding="utf8").read()
    n_after = after.count(";")
    lines_after = len(after.splitlines())

    check("注释数量不变", n_before == n_after, f"{n_before} -> {n_after}")
    check("行数不变（无意外增删）", lines_before == lines_after,
          f"{lines_before} -> {lines_after}")
    check("speed 已更新为 2.0", "speed = 2.0" in after or "speed=2.0" in after)
    check("jobs 已更新为 6", "\njobs = 6" in after or "\njobs=6" in after)
    check("username 已更新", "13900000000" in after)
    check("provider 已更新", "TikuGo,TikuYanxi" in after)
    check("cover_rate 已更新", "cover_rate = 0.80" in after)
    check("notification url 已更新", "https://example.com/x" in after)
    check("注释行 '; 手机号账号(必填)' 仍在", "; 手机号账号(必填)" in after)
    before_comments = [ln for ln in before.splitlines() if ln.lstrip().startswith((";", "#"))]
    after_comments = [ln for ln in after.splitlines() if ln.lstrip().startswith((";", "#"))]
    check("模板原有注释全部原样保留", before_comments == after_comments)
    check("变更条目有记录", len(changed) >= 6, f"{len(changed)} 条")

    # 回读校验
    cp = store.read(tmp)
    check("回读 speed == 2.0", cp.get("common", "speed").strip() == "2.0")
    check("回读 jobs == 6", cp.get("common", "jobs").strip() == "6")

    # 幂等性：再写一次相同内容，不应有任何变化
    changed2 = store.update_values({("common", "speed"): "2.0",
                                    ("common", "jobs"): "6"}, path=tmp)
    check("重复写入相同值 → 无变更", len(changed2) == 0, f"{changed2}")

    os.remove(tmp)


def test_stream():
    print("\n== stream：ANSI 剥离与级别识别 ==")
    from gui_app import stream

    raw = "\x1b[32m\x1b[1m2026-10-07 21:00:00.000 | INFO     | api.base:login:430 - 登录成功...\x1b[0m"
    check("剥离 ANSI", "\x1b" not in stream.strip_ansi(raw))
    check("识别 INFO", stream.parse_level(raw) == "INFO")

    raw2 = "\x1b[31m\x1b[1m2026-10-07 21:00:00.000 | ERROR    | api.base:x:1 - boom\x1b[0m"
    check("识别 ERROR", stream.parse_level(raw2) == "ERROR")
    check("无级别标记时默认 INFO", stream.parse_level("普通一行") == "INFO")

    tail = stream.strip_ansi(raw)
    check("剥离后仍保留正文", "登录成功..." in tail)


def test_stream_proxy():
    print("\n== StreamProxy：\\r 进度条 与 \\n 日志行分流 ==")
    from gui_app.stream import StreamProxy

    lines, progs = [], []
    sp = StreamProxy(lines.append, progs.append)
    sp.write("第一行\n")
    sp.write("\r进度 50%")
    sp.write("\r进度 100%")
    sp.write("\n第二行\r\n")
    sp.flush()

    check("日志行收集正确", lines == ["第一行", "第二行"], str(lines))
    check("进度条被识别为进度", progs[-1].endswith("100%"), str(progs))
    check("进度未混入日志", all("进度" not in x for x in lines), str(lines))


def test_import_chain():
    print("\n== 后端模块可导入（GUI 之外的部分） ==")
    try:
        import api.logger  # noqa: F401
        import api.base  # noqa: F401
        import api.answer  # noqa: F401
        check("api.base / api.answer 可导入", True)
    except BaseException as e:
        check("api.base / api.answer 可导入", False, f"{type(e).__name__}: {e}")

    try:
        import gui_app.study_engine as se  # noqa: F401
        check("gui_app.study_engine 可导入", True)
    except BaseException as e:
        check("gui_app.study_engine 可导入", False, f"{type(e).__name__}: {e}")

    try:
        from gui_app import store  # noqa: F401
        from gui_app import widgets  # noqa: F401
        check("gui_app.widgets 可导入（需要 PySide6）", True)
    except BaseException as e:
        check("gui_app.widgets 可导入（需要 PySide6）", False, f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    print("=" * 60)
    print("学习通桌面版 · 核心逻辑自检")
    print("=" * 60)
    test_store()
    test_stream()
    test_stream_proxy()
    test_import_chain()
    print("\n" + "=" * 60)
    print(f"结果：{ok_count} 通过 / {fail_count} 失败")
    print("=" * 60)
    sys.exit(1 if fail_count else 0)
