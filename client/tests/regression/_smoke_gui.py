# -*- coding: utf-8 -*-
"""界面冒烟测试：离屏创建主窗口，切页、灌数据、截图，暴露 QSS 解析错误与布局异常。"""
import os
import sys

# 默认用真实平台渲染（offscreen 在 Windows 上字体引擎不工作，中文会变方框，
# 无法用于视觉验收）。需要无人值守时传 --offscreen。
if "--offscreen" in sys.argv:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

SHOTS = os.path.join(ROOT, "_shots")
os.makedirs(SHOTS, exist_ok=True)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui_app import theme as T  # noqa: E402
from gui_app.main_window import NAV, PAGE_KEYS, MainWindow  # noqa: E402

FAKE = [
    {"title": "算法与数据结构", "courseId": "236354657", "clazzId": "156246766", "cpi": "1"},
    {"title": "嵌入式操作系统B-2026秋-24级", "courseId": "267009884", "clazzId": "154470148", "cpi": "1"},
    {"title": "习近平新时代中国特色社会主义思想概论", "courseId": "251749076", "clazzId": "154216668", "cpi": "1"},
    {"title": "数学建模", "courseId": "203461342", "clazzId": "151233173", "cpi": "1"},
    {"title": "大学英语视听说", "courseId": "255584934", "clazzId": "128384942", "cpi": "1"},
    {"title": "2026Summer-IE22&AI22-系统设计创新与电子系统设计", "courseId": "265451559", "clazzId": "150599541", "cpi": "1"},
]
PROG = [(0, 204), (0, 16), (0, 62), (1, 33), (7, 43), (13, 13)]


extra_bad = []


def shot(widget, name, extra=""):
    path = os.path.join(SHOTS, f"{name}.png")
    widget.grab().save(path)
    print(f"  截图 -> {path} {extra}")


def main():
    print("=" * 60)
    print("界面冒烟测试")
    print("=" * 60)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(T.build_qss(os.path.join(ROOT, "assets")))

    print("\n[1] 创建主窗口")
    win = MainWindow()
    win.resize(1300, 860)
    win.show()
    app.processEvents()
    print("  MainWindow OK, size =", win.size().width(), "x", win.size().height())

    print("\n[2] 逐页截图")
    # 页面清单从 NAV 单一数据源派生 —— 以前写死 goto(0..3)，插一页就指错地方
    shot_pages = [("run", "01_开始刷课"), ("account", "02_账号登录"),
                  ("courses", "03_课程列表"), ("settings", "04_参数设置"),
                  ("ai", "05_AI大模型"), ("unipus", "06_U校园"),
                  ("yuketang", "07_雨课堂"), ("live", "09_课堂互动")]
    for key, name in shot_pages:
        if key not in PAGE_KEYS:
            continue
        win.goto(key)
        app.processEvents()
        shot(win, name)

    print("\n[3] 课程页灌入数据")
    win.goto("courses")
    _acc = win.courses_page.current_account()
    win._on_courses(_acc, FAKE)
    for r, (d, t) in enumerate(PROG):
        win.runner.sig_course_progress.emit(_acc, r, {"done": d, "total": t, "error": None})
    app.processEvents()
    # 勾选前两门
    from PySide6.QtCore import Qt as _Qt
    for r in (0, 1, 3):
        it = win.courses_page.table.item(r, 0)
        it.setCheckState(_Qt.Checked)
    app.processEvents()
    shot(win, "05_课程列表_有数据", f"已选={len(win.courses_page.selected_courses())} 门")

    print("\n[4] 课程选择的持久化（按账号写入账号档案）")
    from gui_app import profiles as _prof

    book = win.book
    orig = list(book.courses(_acc))
    try:
        book.set_courses(_acc, ["203461342", "255584934"])
        back = list(book.courses(_acc))
        print(f"    accounts.json 中 {_acc} 的 courses = {back}  (原值 {orig})")
        # 重新填表，检查是否自动恢复勾选
        win.courses_page.set_saved(back)
        win._on_courses(_acc, FAKE)
        for r, (d, t) in enumerate(PROG):
            win.runner.sig_course_progress.emit(_acc, r, {"done": d, "total": t, "error": None})
        app.processEvents()
        sel = [c["courseId"] for c in win.courses_page.selected_courses()]
        print(f"    自动恢复勾选: {sel}")
        restored = set(sel) == {"203461342", "255584934"}
        check_more = [("选择写入账号档案", back == ["203461342", "255584934"]),
                      ("重新载入后自动恢复勾选", restored)]
        for name, cond in check_more:
            print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
            if not cond:
                extra_bad.append(name)
    finally:
        # 清理测试痕迹：恢复原值
        book.set_courses(_acc, orig)
        shot(win, "05b_课程列表_恢复勾选")

    print("\n[5] 设置页四个页签")
    win.goto("settings")
    for i in range(4):
        win.settings_page.seg.set_index(i)
        app.processEvents()
        shot(win, f"06_设置_{i}")

    print("\n[6] 题库页签切换 provider 后的动态显隐")
    win.settings_page.seg.set_index(1)
    win.settings_page.ed_provider.setText("TikuGo,TikuYanxi,TikuLike,AI,SiliconFlow")
    app.processEvents()
    vis = {
        "token(言溪/LIKE)": win.settings_page.card_token.isVisible(),
        "LIKE专属": win.settings_page.card_like.isVisible(),
        "硅基流动": win.settings_page.card_sf.isVisible(),
        "GO题": win.settings_page.card_go.isVisible(),
        "TikuAdapter": win.settings_page.card_adapter.isVisible(),
    }
    for k, v in vis.items():
        print(f"  {k}: {'显示' if v else '隐藏'}")
    shot(win, "07_题库_多provider")

    print("\n[7] 运行页灌入日志与进度")
    win.goto("run")
    win.run_page.set_accounts([(_acc, "默认账号")])
    win.run_page.set_total(_acc, 446)
    win.run_page.sync_run_state(True)
    for lvl, txt in [
        ("INFO", "2026-10-07 21:20:00.000 | INFO     | api.base:login:430 - 登录成功..."),
        ("INFO", "2026-10-07 21:20:01.000 | INFO     | api.base:get_course_list:506 - 课程列表读取完毕..."),
        ("DEBUG", "2026-10-07 21:20:02.000 | DEBUG    | main:worker_thread:394 - 完成: 算法与数据结构 - 1.1 绪论"),
        ("WARNING", "2026-10-07 21:20:05.000 | WARNING  | main:worker_thread:418 - 重试任务 数学建模 - 第二章 (1/5 次尝试)"),
        ("ERROR", "2026-10-07 21:20:09.000 | ERROR    | api.base:get_job_list:636 - 未知错误: 403 正在跳过"),
        ("SUCCESS", "2026-10-07 21:20:12.000 | SUCCESS  | main:main:607 - 所有课程学习任务已完成"),
    ]:
        win.run_page.append_log(lvl, txt)
    win.run_page.update_progress(_acc, 37, 2, "1.3 线性表的链式存储")
    app.processEvents()
    shot(win, "08_运行页_有日志")

    print("\n[8] 关键控件存在性检查")
    checks = [
        ("导航项数 = 页面数 + 分组标题数",
         win.nav.count() == len(PAGE_KEYS) + len(NAV)),
        ("页面栈页数 = 页面数", win.stack.count() == len(PAGE_KEYS)),
        ("每组至少一个页面", all(items for _g, items in NAV)),
        ("导航 key 与页面栈一一对应",
         all(k in win._key_row for k in PAGE_KEYS)),
        ("课程表 7 列", win.courses_page.table.columnCount() == 7),
        ("课程表行数", win.courses_page.table.rowCount() == len(FAKE)),
        ("日志区只读", win.run_page.log.isReadOnly()),
        ("日志最大行数限制", win.run_page.log.maximumBlockCount() == 4000),
        ("QSS 已应用", len(app.styleSheet()) > 1000),
        ("设置页 provider 回读", win.settings_page.ed_provider.text().startswith("TikuGo")),
    ]
    bad = len(extra_bad)
    for name, cond in checks:
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            bad += 1

    # ---- 设置页「未保存」徽标 + 题库空配置提示（v1.5.7 真实行为，不是只查源码）----
    print("\n[9] 设置页 dirty 与题库隐患提示")
    sp = win.settings_page
    sp.sp_speed.setValue(sp.sp_speed.value() + 0.1)
    app.processEvents()
    dirty = "未保存" in sp.badge.text()
    print(f"  [{'PASS' if dirty else 'FAIL'}] 改动后徽标提示「未保存」（得到 {sp.badge.text()!r}）")
    bad += 0 if dirty else 1

    sp.ed_provider.setText("TikuYanxi")
    sp.ed_tokens.setText("")
    warn = sp._config_warnings()
    ok_warn = "TikuYanxi" in warn or "言溪" in warn
    print(f"  [{'PASS' if ok_warn else 'FAIL'}] 言溪缺 token → 保存提示会被跳过")
    bad += 0 if ok_warn else 1

    print("\n" + "=" * 60)
    print("冒烟测试结束，失败项：", bad)
    print("=" * 60)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
