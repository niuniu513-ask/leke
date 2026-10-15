# -*- coding: utf-8 -*-
"""运行页（多账号版）进度 / 当前章节与视频 显示 + 设置页「刷课方式」说明 验证。

会临时改写 config.ini 与 gui_settings.json，跑完自动还原（sha256 校验）。

跑法：.venv\\Scripts\\python.exe tests/regression/_test_runprogress.py
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time

os.environ.pop("QT_QPA_PLATFORM", None)

from PySide6.QtWidgets import QApplication  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

from gui_app import store                       # noqa: E402
from gui_app import theme as T                  # noqa: E402
from gui_app.main_window import MainWindow      # noqa: E402

CFG = store.config_path()
CFG_BAK = os.path.join(ROOT, "_bak_config_runprg.ini")
UIF = store.ui_path()
UIF_BAK = os.path.join(ROOT, "_bak_ui_runprg.json")
SHOT_DIR = os.path.join(ROOT, "_shots", "runprogress")

A1, A2 = "default", "acc_two"

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


def sha(p: str) -> str:
    return hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else "-"


def main() -> int:
    app = QApplication.instance() or QApplication([])
    # 跟 app.py 一样挂上全局样式，否则 grab() 出来的图没有卡片/进度条底色
    app.setStyleSheet(T.build_qss(os.path.join(ROOT, "assets")))
    shutil.copy(CFG, CFG_BAK)
    if os.path.exists(UIF):
        shutil.copy(UIF, UIF_BAK)
    cfg_before, ui_before = sha(CFG), sha(UIF)

    try:
        win = MainWindow()
        win.resize(1200, 900)
        rp = win.run_page
        sp = win.settings_page

        # 造两个账号，验「每账号一张卡、各自独立」
        rp.set_accounts([(A1, "默认账号"), (A2, "小号")])
        c1 = rp.cards[A1]
        c2 = rp.cards[A2]

        # ---------------------------------------------------------- 1
        print("\n[1] 每账号一张进度卡，控件齐备")
        check("两个账号各有一张卡", len(rp.cards) == 2, f"得到 {sorted(rp.cards)}")
        for attr in ("bar", "lb_pass", "lb_stats", "lb_name", "lb_time", "badge",
                     "now_box", "slots_box", "lb_now_empty", "lb_last"):
            check(f"卡上有 {attr}", hasattr(c1, attr))
        check("旧单槽控件已移除", not any(hasattr(c1, a) for a in (
            "video_bar", "job_bar", "lb_chapter", "lb_course", "chip_type",
            "lb_job", "lb_job_meta", "_now_fields")))
        check("默认全选参与刷课", set(rp.selected_accounts()) == {A1, A2},
              f"得到 {rp.selected_accounts()}")

        # ---------------------------------------------------------- 2
        print("\n[2] 续刷遍次徽标（每账号独立）")
        rp.set_pass(A1, 1)
        check("第 1 遍文案", c1.lb_pass.text() == "自动续刷", f"得到 {c1.lb_pass.text()}")
        rp.set_pass(A1, 2)
        check("第 2 遍文案", c1.lb_pass.text() == "自动续刷 · 第 2 遍",
              f"得到 {c1.lb_pass.text()}")
        rp.set_pass(A1, 13)
        check("第 13 遍文案", c1.lb_pass.text() == "自动续刷 · 第 13 遍",
              f"得到 {c1.lb_pass.text()}")
        check("徽标不再出现「X / Y 轮」", "/" not in c1.lb_pass.text(),
              f"得到 {c1.lb_pass.text()}")
        check("A2 不受 A1 影响", c2.lb_pass.text() == "自动续刷",
              f"得到 {c2.lb_pass.text()}")

        # ---------------------------------------------------------- 3
        print("\n[3] 总进度条与文案")
        rp.reset(A1)
        check("重置后文案为尚未开始", c1.lb_stats.text() == "尚未开始")
        rp.set_pass(A1, 1)
        rp.set_total(A1, 8)
        check("进度条上限 = 本遍待刷章节", c1.bar.maximum() == 8, f"得到 {c1.bar.maximum()}")
        rp.update_progress(A1, 3, 0, "第 1 章")
        check("进度条值 = 已处理", c1.bar.value() == 3, f"得到 {c1.bar.value()}")
        check("文案含 已处理 3 与百分比", "本遍已处理 3" in c1.lb_stats.text()
              and "38%" in c1.lb_stats.text(),
              f"得到 {c1.lb_stats.text()}")
        rp.set_cumulative(A1, 7, 1)
        check("累计写进文案", "累计完成 7" in c1.lb_stats.text(), f"得到 {c1.lb_stats.text()}")
        check("失败数写进文案", "失败 1" in c1.lb_stats.text(), f"得到 {c1.lb_stats.text()}")

        # 全部完成
        rp.update_progress(A1, 8, 1, "第 8 章")
        check("满进度时百分比 100%", "100%" in c1.lb_stats.text(), f"得到 {c1.lb_stats.text()}")
        check("进度条满格不越界", c1.bar.value() == c1.bar.maximum())

        # ---------------------------------------------------------- 4
        print("\n[4] 并行任务点槽：单任务点显示")
        rp.set_current(A1, {
            "pass": 2, "plan": 8,
            "course": "高等数学（上）", "chapter": "第 3 章 导数应用",
            "job": "3.2 导数应用举例.mp4", "job_type": "视频",
            "job_index": 2, "job_total": 3,
        })
        check("槽位已建", len(c1._slots) == 1, f"得到 {len(c1._slots)}")
        key1 = next(iter(c1._slots))
        s1 = c1._slots[key1]
        check("行标题含章节与任务点", "第 3 章 导数应用" in s1["title"].text()
              and "3.2 导数应用举例.mp4" in s1["title"].text(),
              f"得到 {s1['title'].text()}")
        check("类型标签", s1["chip"].text() == "视频")
        check("当前块进入运行态", c1.now_box.property("state") == "run",
              f"得到 {c1.now_box.property('state')}")
        check("行文案含 2/3 与已用时长", "第 2/3 个" in s1["meta"].text()
              and "已用" in s1["meta"].text(), f"得到 {s1['meta'].text()}")
        check("另一张卡无槽", len(c2._slots) == 0)

        # 缺字段 / 空值
        rp.set_current(A1, {"course": "X"})
        check("缺字段仍建独立槽（不覆盖旧槽）", len(c1._slots) == 2,
              f"得到 {len(c1._slots)}")
        rp.set_current(A1, {})
        check("空 dict 清空全部槽", len(c1._slots) == 0)
        check("空 dict 回到待机块", c1.now_box.property("state") == "idle")
        rp.set_current(A1, None)
        check("None 也回到待机块", c1.now_box.property("state") == "idle")

        # ---------------------------------------------------------- 4b
        print("\n[4b] 并行任务点槽：视频迷你条 + 多章节并行（v1.5.1f）")
        rp.reset(A1)
        rp.set_current(A1, {
            "pass": 1, "plan": 8,
            "course": "高等数学（上）", "chapter": "第 3 章 导数应用",
            "job": "3.2 导数应用举例.mp4", "job_type": "视频",
            "job_index": 2, "job_total": 3,
        })
        s1 = c1._slots[c1._slot_key({"course": "高等数学（上）", "job": "3.2 导数应用举例.mp4"})]
        check("set_current 后迷你条隐藏（等心跳）", not s1["bar"].isVisibleTo(s1["row"]))
        rp.set_video_progress(A1, {"course": "高等数学（上）", "job": "3.2 导数应用举例.mp4",
                                   "play_time": 750, "duration": 1940, "speed": 1.0})
        check("迷你条出现", s1["bar"].isVisibleTo(s1["row"]))
        check("迷你条上限 = 视频总秒数", s1["bar"].maximum() == 1940,
              f"得到 {s1['bar'].maximum()}")
        check("迷你条值 = 当前播放秒数", s1["bar"].value() == 750,
              f"得到 {s1['bar'].value()}")
        check("行文案带播放进度", "播放中 12:30 / 32:20" in s1["meta"].text(),
              f"得到 {s1['meta'].text()}")
        c1.tick()
        check("tick 持续刷新播放文案", "播放中 12:30 / 32:20" in s1["meta"].text(),
              f"得到 {s1['meta'].text()}")
        # 心跳推进 + 倍速
        rp.set_video_progress(A1, {"course": "高等数学（上）", "job": "3.2 导数应用举例.mp4",
                                   "play_time": 1800, "duration": 1940, "speed": 1.5})
        check("心跳推进迷你条", s1["bar"].value() == 1800)
        check("倍速 >1 显示在文案", "1.5x" in s1["meta"].text(),
              f"得到 {s1['meta'].text()}")

        # 并行：第二个章节同时开始处理，不得覆盖第一行
        rp.set_current(A1, {
            "course": "高等数学（上）", "chapter": "第 4 章 中值定理",
            "job": "4.1 拉格朗日定理.mp4", "job_type": "视频",
            "job_index": 1, "job_total": 2,
        })
        check("两个并行槽并存", len(c1._slots) == 2, f"得到 {len(c1._slots)}")
        check("旧行文案未被覆盖", "播放中 30:00" in s1["meta"].text(),
              f"得到 {s1['meta'].text()}")
        rp.set_video_progress(A1, {"course": "高等数学（上）", "job": "4.1 拉格朗日定理.mp4",
                                   "play_time": 10, "duration": 100, "speed": 1.0})
        s2 = c1._slots[c1._slot_key({"course": "高等数学（上）", "job": "4.1 拉格朗日定理.mp4"})]
        check("两行各自推进互不干扰", s1["bar"].value() == 1800 and s2["bar"].value() == 10,
              f"s1={s1['bar'].value()} s2={s2['bar'].value()}")

        # 测验任务点建第三个槽，视频槽保留
        rp.set_current(A1, {
            "course": "高等数学（上）", "chapter": "第 3 章 导数应用",
            "job": "章节测验", "job_type": "章节测验",
            "job_index": 3, "job_total": 3,
        })
        check("测验任务点建新槽", len(c1._slots) == 3, f"得到 {len(c1._slots)}")
        check("视频槽不被新任务点清除", s1["bar"].isVisibleTo(s1["row"]))

        # 过期灰显 / 移除
        s2["ts"] = time.time() - 200
        c1.tick()
        check("超时行灰显（stale）", s2["row"].property("state") == "stale",
              f"得到 {s2['row'].property('state')}")
        s2["ts"] = time.time() - 700
        c1.tick()
        check("更久无更新的行被移除", len(c1._slots) == 2, f"得到 {len(c1._slots)}")

        # 新一遍开始清空全部槽
        rp.set_pass(A1, 2)
        check("新一遍开始清空槽", len(c1._slots) == 0)

        # 结束清理
        rp.set_current(A1, {"course": "C", "job": "V.mp4", "job_type": "视频",
                            "job_index": 1, "job_total": 1})
        rp.set_video_progress(A1, {"course": "C", "job": "V.mp4",
                                   "play_time": 10, "duration": 100, "speed": 1.0})
        rp.set_finished(A1, "ok", "done")
        check("结束后槽清空", len(c1._slots) == 0)
        check("结束后状态回待机块", c1.now_box.property("state") == "idle")
        # 不串号
        rp.set_current(A1, {"course": "C", "job": "V.mp4", "job_type": "视频",
                            "job_index": 1, "job_total": 1})
        rp.set_video_progress(A1, {"course": "C", "job": "V.mp4",
                                   "play_time": 30, "duration": 100, "speed": 1.0})
        check("视频心跳不串号（A2 无槽）", len(c2._slots) == 0)

        # ---------------------------------------------------------- 5
        print("\n[5] 主窗口接线：信号带 account_id 直达对应卡")
        rp.reset(A1)
        win.runner.sig_study_pass.emit(A1, 3)
        check("遍次信号 -> 该账号徽标", c1.lb_pass.text() == "自动续刷 · 第 3 遍",
              f"得到 {c1.lb_pass.text()}")
        check("遍次信号不串号", c2.lb_pass.text() == "自动续刷", f"得到 {c2.lb_pass.text()}")
        win.runner.sig_study_current.emit(A1, {
            "pass": 3, "plan": 5,
            "course": "数据结构", "chapter": "第 5 章 树与二叉树",
            "job": "5.2 二叉树的遍历.mp4", "job_type": "视频",
            "job_index": 1, "job_total": 4,
        })
        check("当前任务信号 -> 槽标题", "5.2 二叉树的遍历.mp4"
              in c1._slots[c1._slot_key({"course": "数据结构", "job": "5.2 二叉树的遍历.mp4"})]["title"].text())
        win.runner.sig_video_progress.emit(A1, {"course": "数据结构", "job": "5.2 二叉树的遍历.mp4",
                                                "play_time": 60, "duration": 300,
                                                "speed": 1.0})
        s5 = c1._slots.get(c1._slot_key({"course": "数据结构", "job": "5.2 二叉树的遍历.mp4"}))
        check("播放心跳信号 -> 对应槽迷你条（无槽时忽略）",
              s5 is not None and s5["bar"].maximum() == 300 and s5["bar"].value() == 60,
              f"槽存在={s5 is not None}")
        win.runner.sig_study_total.emit(A1, 5)
        check("总数信号 -> 该账号进度条", c1.bar.maximum() == 5)
        win.runner.sig_study_update.emit(A1, 2, 0, "第 4 章 串")
        check("进度信号 -> 进度条", c1.bar.value() == 2, f"得到 {c1.bar.value()}")
        win.runner.sig_study_totals.emit(A1, {"total_done": 9, "failed": 1, "pass": 2})
        check("累计信号 -> 文案", "累计完成 9" in c1.lb_stats.text(),
              f"得到 {c1.lb_stats.text()}")
        check("状态栏带上遍次信息", "第 2 遍结束" in win.lb_status.text(),
              f"得到 {win.lb_status.text()}")

        # ---------------------------------------------------------- 6
        print("\n[6] 结束态")
        rp.set_finished(A1, "ok", "共检查 3 遍，完成 12 个章节")
        check("结束徽标", c1.badge.text() == "已完成", f"得到 {c1.badge.text()}")
        check("当前块回到待机", c1.now_box.property("state") == "idle")
        check("开始按钮恢复", rp.btn_start.isEnabled() and rp.btn_start.text() == "开始刷课")

        # ---------------------------------------------------------- 7
        print("\n[7] 设置页：不再有「刷几轮」选项，只有常显说明")
        check("已移除轮次下拉控件", not hasattr(sp, "cb_rounds"))
        check("已移除轮次切换回调", not hasattr(sp, "_on_rounds_changed"))
        check("说明标签存在", hasattr(sp, "lb_pass_hint"))
        check("说明写明是自动续刷", "自动续刷" in sp.lb_pass_hint.text(),
              f"得到 {sp.lb_pass_hint.text()[:44]}")
        check("说明解释了为什么需要续刷（解锁）", "解锁" in sp.lb_pass_hint.text())
        check("说明写明不必手动跑第二遍", "不需要再手动跑第二遍" in sp.lb_pass_hint.text())
        check("说明是常显的（有实际尺寸）",
              sp.lb_pass_hint.sizeHint().width() > 0,
              f"hint={sp.lb_pass_hint.sizeHint().width()}")

        check("current_common 不再带 rounds", "rounds" not in sp.current_common(),
              f"得到 {sorted(sp.current_common().keys())}")

        # 保存也不该再往 config.ini 写 rounds
        sp.save()
        raw = open(CFG, encoding="utf-8").read()
        check("保存后 config.ini 不含 rounds 键", "rounds" not in raw)
        check("保存后仍保留原有注释排版", "; 遇到关闭任务点时的行为" in raw)

        # 重建页面：说明仍在，且不崩
        sp2 = type(sp)()
        check("重建设置页不崩且说明仍在", "自动续刷" in sp2.lb_pass_hint.text())

        # 1.3.0 曾经往配置里写过 rounds 键，残留时界面也必须照常
        store.update_values({("common", "rounds"): "3"})
        sp3 = type(sp)()
        check("配置里残留 rounds 旧键时界面照常", "自动续刷" in sp3.lb_pass_hint.text())
        check("残留旧键不会被界面读回", not hasattr(sp3, "cb_rounds"))

        # ---------------------------------------------------------- 8
        print("\n[8] 截图（两个账号并行运行中的样子）")
        os.makedirs(SHOT_DIR, exist_ok=True)
        win.goto("run")
        rp.reset(A1)
        rp.set_pass(A1, 2)
        rp.set_total(A1, 12)
        rp.set_cumulative(A1, 7, 1)
        rp.update_progress(A1, 4, 1, "第 3 章 中值定理")
        rp.set_current(A1, {
            "pass": 2, "plan": 12,
            "course": "高等数学（上）", "chapter": "第 3 章 微分中值定理与导数的应用",
            "job": "3.2 拉格朗日中值定理的应用.mp4", "job_type": "视频",
            "job_index": 2, "job_total": 3,
        })
        # 同一账号再并行第二个章节 + 视频心跳 —— 展示多章节并行各占一行
        rp.set_current(A1, {
            "pass": 2, "plan": 12,
            "course": "高等数学（上）", "chapter": "第 4 章 不定积分",
            "job": "4.1 原函数与不定积分的概念.mp4", "job_type": "视频",
            "job_index": 1, "job_total": 2,
        })
        rp.set_video_progress(A1, {"course": "高等数学（上）", "job": "3.2 拉格朗日中值定理的应用.mp4",
                                   "play_time": 752, "duration": 1940, "speed": 1.0})
        rp.set_video_progress(A1, {"course": "高等数学（上）", "job": "4.1 原函数与不定积分的概念.mp4",
                                   "play_time": 96, "duration": 1420, "speed": 1.0})
        rp.reset(A2)
        rp.set_pass(A2, 1)
        rp.set_total(A2, 6)
        rp.update_progress(A2, 2, 0, "第 1 章 绪论")
        rp.set_current(A2, {
            "pass": 1, "plan": 6,
            "course": "大学英语", "chapter": "Unit 2 Reading",
            "job": "2.1 Text A.mp4", "job_type": "视频",
            "job_index": 1, "job_total": 2,
        })
        rp.cards[A1].set_state("running")
        rp.cards[A2].set_state("running")
        rp.set_mode_hint("真实节奏 · 自动续刷")
        rp.sync_run_state(True)
        app.processEvents()
        # 抓整页（含 QScrollArea 内容），不能只 grab 视口
        p = os.path.join(SHOT_DIR, "01_运行中.png")
        rp.grab().save(p)
        check("运行中截图已保存", os.path.exists(p))
        # v1.5.3 闭环修复：运行中配置类控件一律禁用（当前任务用开始时的快照）
        check("运行中「从头重刷」已禁用", not rp.cb_restudy.isEnabled())
        check("运行中账号勾选框已禁用",
              all(not b.isEnabled() for b in rp._boxes.values()))
        check("运行中「从头重刷」带提示", "不可修改" in (rp.cb_restudy.toolTip() or ""))
        check("运行中「停止全部」可用且「开始」禁用",
              rp.btn_stop.isEnabled() and not rp.btn_start.isEnabled())
        rp.sync_run_state(False)
        app.processEvents()
        check("停止后「从头重刷」恢复可用",
              rp.cb_restudy.isEnabled() and rp.cb_restudy.toolTip() == "")

        rp.set_finished(A1, "ok", "共检查 3 遍，完成 12 个章节")
        app.processEvents()
        p2 = os.path.join(SHOT_DIR, "02_已完成.png")
        rp.grab().save(p2)
        check("已完成截图已保存", os.path.exists(p2))

        # ---------------------------------------------------------- 9
        # 用户反馈："视频进度条总是一跳一跳的"。
        # 根因：引擎心跳是「一秒一帧 + 整秒取整」，QProgressBar 的 chunk 又只按
        # 整数像素画，32 分钟的视频每秒只前进 0.4px → 卡住 → 跳一格。
        # 修法：引擎帧改浮点 + 带 session，界面按墙钟插值连续推进（SmoothBar）。
        print("\n[9] 视频进度条：连续滑动、不倒退、播完即停（一跳一跳 bug）")
        rp.reset(A1)
        JOB = {"course": "数据结构", "job": "6.1 图的基本概念.mp4",
               "job_type": "视频", "job_index": 1, "job_total": 1}
        rp.set_current(A1, JOB)
        key = c1._slot_key(JOB)
        slot = c1._slots[key]
        vbar = slot["bar"]

        rp.set_video_progress(A1, {**JOB, "play_time": 100.4, "duration": 1940.0,
                                   "speed": 1.0, "session": 7, "seq": 1})
        check("心跳带浮点秒被原样锚定", abs(vbar.value() - 100.4) < 0.01,
              f"得到 {vbar.value()}")
        check("进度条上限 = 浮点 duration", vbar.maximum() == 1940.0,
              f"得到 {vbar.maximum()}")

        # 同一会话内引擎的整秒取整抖动不能让进度条倒退
        rp.set_video_progress(A1, {**JOB, "play_time": 100.0, "duration": 1940.0,
                                   "speed": 1.0, "session": 7, "seq": 2})
        check("会话内小幅回退不倒退", vbar.value() >= 100.4, f"得到 {vbar.value()}")

        # 单帧心跳 + 墙钟插值 = 10 次小步推进（老实现要等下一次心跳才「跳一格」）
        base_v = vbar.shown()
        steps, deltas = [], []
        prev = base_v
        for _ in range(10):
            time.sleep(0.05)
            c1.animate()
            cur = vbar.shown()
            steps.append(round(cur - base_v, 4))     # 相对起点的累计位移
            deltas.append(round(cur - prev, 4))      # 单帧位移
            prev = cur
        check("墙钟插值：10 次小步连续推进（不是等心跳才跳一格）",
              len(set(steps)) >= 8 and all(steps[i] < steps[i + 1] for i in range(9)),
              str(steps))
        check("动画驱动报告仍在推进", c1.animate() is True)

        # 位移被摊成很多个小步，而不是攒够 1 秒才硬跳一格。
        # 用「绝大多数帧都是小步」判定：机器负载偶尔让某次 sleep 超时不会误报
        check("位移被摊成多次小步（不是攒够一秒才硬跳一格）",
              all(d > 0 for d in deltas) and sum(1 for d in deltas if d < 0.5) >= 8,
              f"total={steps[-1]:.3f} deltas={deltas}")

        # 真·重播：引擎开新会话 → 才允许归零
        rp.set_video_progress(A1, {**JOB, "play_time": 0.0, "duration": 1940.0,
                                   "speed": 1.0, "session": 8, "seq": 1})
        check("新 session = 重播，进度条归零重计", vbar.value() < 1.0,
              f"得到 {vbar.value()}")
        # 没带新 session 的大幅倒退也要按重播处理，不能停在旧值
        rp.set_video_progress(A1, {**JOB, "play_time": 900.0, "duration": 1940.0,
                                   "speed": 1.0, "session": 8, "seq": 2})
        rp.set_video_progress(A1, {**JOB, "play_time": 5.0, "duration": 1940.0,
                                   "speed": 1.0, "session": 8, "seq": 3})
        check("无新 session 的大幅倒退也按重播处理", vbar.value() < 10.0,
              f"得到 {vbar.value()}")

        # 播到片尾后不再空转
        rp.set_video_progress(A1, {**JOB, "play_time": 1940.0, "duration": 1940.0,
                                   "speed": 1.0, "session": 9, "seq": 1})
        check("播到 duration 后无槽可推进", c1.animate() is False)
        check("播到 duration 后显示值不越界", vbar.shown() <= 1940.0,
              f"得到 {vbar.shown()}")
        check("文案给出百分比（进度可读）", "100%" in slot["meta"].text(),
              f"得到 {slot['meta'].text()}")

        # 账号进度条：章节粒度跳变 → 缓动过渡，但 value() 仍是目标值
        rp.reset(A1)
        rp.set_total(A1, 8)
        # 显式把缓动值也归零：前面的步骤可能已经让 _shown 停在别处，
        # 断言只关心「从 0 出发、一次 advance 后落在 0 与目标之间」
        c1.bar.setValue(0)
        c1.bar._shown = 0.0
        c1.bar.setValue(3)
        check("账号进度条目标值即时可见（缓动只作用于绘制）",
              c1.bar.value() == 3, f"得到 {c1.bar.value()}")
        c1.bar._advance()
        check("账号进度条缓动逐帧逼近（不是瞬跳）", 0 < c1.bar.shown() < 3,
              f"得到 {c1.bar.shown()}")

    finally:
        shutil.copy(CFG_BAK, CFG)
        os.remove(CFG_BAK)
        if os.path.exists(UIF_BAK):
            shutil.copy(UIF_BAK, UIF)
            os.remove(UIF_BAK)
        elif ui_before == "-" and os.path.exists(UIF):
            os.remove(UIF)

    print("\n[10] 配置文件已还原")
    check("config.ini sha256 与跑之前一致", sha(CFG) == cfg_before,
          f"{sha(CFG)[:12]} vs {cfg_before[:12]}")
    check("gui_settings.json sha256 与跑之前一致", sha(UIF) == ui_before,
          f"{sha(UIF)[:12]} vs {ui_before[:12]}")

    print("\n" + "=" * 56)
    print(f"通过 {PASSES} 项，失败 {len(FAILS)} 项")
    for f in FAILS:
        print("  失败:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
