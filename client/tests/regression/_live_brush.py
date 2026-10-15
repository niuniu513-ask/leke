# -*- coding: utf-8 -*-
"""真机刷课复现脚本（headless）。

复用生产路径：`gui_app.study_engine.StudyScheduler` + `gui_app.worker` 的续刷判定逻辑，
用本地已登录的测试账号对真实课程跑一遍，全量采集日志用于排查问题。

环境变量
--------
LEKE_APP_DIR   程序目录（默认 D:/学习通）——决定 config.ini / accounts.json / 知识库 / cookie
LEKE_ACC       账号 id（默认取 accounts.json 的 active）
LEKE_COURSE    课程 id（默认取账号 courses 第一门）
LEKE_POINTS    逗号分隔的 pointId；"ALL"=全课程未完成章节；缺省=账号里保存的章节选择
LEKE_VIDEO_MODE  fast/timed（缺省读 config.ini）
LEKE_DRY       1 = 只收集任务点并打印构成，不实际处理
LEKE_MAX_PASSES 续刷兜底上限（默认 20）
LEKE_SKIP_FINISHED  0 = 不跳过已完成章节（用于重做验证；默认沿用 gui_settings.json）

运行（工作目录必须是程序目录，才能命中真实的 知识库 / leke.log）：
  cd /d/学习通 && .venv/Scripts/python.exe .../tests/regression/_live_brush.py
"""
import configparser
import json
import os
import sys
import threading
import time
import traceback

APP_DIR = os.path.abspath(os.environ.get("LEKE_APP_DIR", "D:/学习通"))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

_REAL_OUT = sys.stdout
LOG_PATH = os.path.join(APP_DIR, "_live_brush_run.log")


def _emit(line: str):
    _REAL_OUT.write(line + "\n")
    _REAL_OUT.flush()


try:
    _logf = open(LOG_PATH, "w", encoding="utf-8")
except Exception:
    _logf = None


def _log(line: str):
    if _logf:
        _logf.write(line + "\n")
        _logf.flush()


# ---- 接管日志（必须先于 api.logger 导入）----
from gui_app import stream  # noqa: E402
stream.install()
stream.set_handlers(lambda t: (_emit(t.rstrip()), _log(t.rstrip())))

from api.logger import logger  # noqa: E402
from api.base import Account, Chaoxing  # noqa: E402
from gui_app import study_engine  # noqa: E402


def _preflight_font_map():
    """字体映射表自检 —— 缺了它题面全是乱码，答案必然全错（假故障）。

    `api.cxsecret_font.resource_path()` 非冻结态取 cwd，所以从程序目录跑就必须
    程序目录下有 resource/。缺了不会报错，只会静默答错，白白浪费一次真机刷课。
    """
    from api import cxsecret_font
    ok = bool(getattr(cxsecret_font.fonthash_dao, "hash_map", None))
    if not ok:
        logger.error(
            "字体映射表未加载（题面会是乱码，答题结果不可信）—— "
            f"当前 cwd={os.getcwd()}，请先 `cp -r <项目根>/resource ./`，"
            "或把本脚本的工作目录设到含 resource/ 的目录后重跑")
    return ok

CONFIG = os.path.join(APP_DIR, "config.ini")
ACCOUNTS = os.path.join(APP_DIR, "accounts.json")
GUI_SETTINGS = os.path.join(APP_DIR, "gui_settings.json")


def _bool(v, d=False):
    if v is None:
        return d
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def load_runtime():
    cp = configparser.RawConfigParser(interpolation=None)
    cp.read(CONFIG, encoding="utf8")
    ui = {}
    try:
        ui = json.load(open(GUI_SETTINGS, encoding="utf-8"))
    except Exception:
        pass
    common = {
        "speed": float(cp.get("common", "speed", fallback="1") or 1),
        "jobs": int(cp.get("common", "jobs", fallback="4") or 4),
        "notopen_action": cp.get("common", "notopen_action", fallback="retry"),
        "retry_interval": float(cp.get("common", "retry_interval", fallback="1") or 1),
        "work_redo_enabled": _bool(cp.get("common", "work_redo_enabled", fallback="false")),
        "work_max_retries": int(cp.get("common", "work_max_retries", fallback="3") or 3),
        "add_learning_count": _bool(cp.get("common", "add_learning_count", fallback="false")),
        "target_count": int(cp.get("common", "target_count", fallback="100") or 100),
        "skip_finished": _bool(os.environ.get("LEKE_SKIP_FINISHED"),
                               bool(ui.get("skip_finished", True))),
        "video_mode": (os.environ.get("LEKE_VIDEO_MODE")
                       or cp.get("common", "video_mode", fallback="fast")).strip().lower(),
    }
    tiku = dict(cp.items("tiku")) if cp.has_section("tiku") else {}
    return common, tiku


def load_account(acc_id):
    data = json.load(open(ACCOUNTS, encoding="utf-8"))
    if not acc_id:
        acc_id = data.get("active") or ""
    for a in data.get("accounts", []):
        if str(a.get("id")) == str(acc_id):
            return a
    return {}


class Task:
    __slots__ = ("index", "point", "course", "result", "tries", "already_finished")

    def __init__(self, index, point, course):
        self.index = index
        self.point = point
        self.course = course
        self.result = None
        self.tries = 0
        self.already_finished = bool(point.get("has_finished"))

    def __lt__(self, other):
        return self.index < other.index


def collect_tasks(cx, courses, only_points, skip_finished, give_up, max_give_up=3):
    tasks = []
    for course in courses:
        logger.info(f"正在读取章节: {course.get('title')}")
        pl = cx.get_course_point(course["courseId"], course["clazzId"], course.get("cpi"))
        for i, point in enumerate((pl or {}).get("points") or []):
            if only_points and str(point.get("id")) not in only_points:
                continue
            if skip_finished and point.get("has_finished"):
                continue
            key = f"{course.get('courseId')}:{point.get('id')}"
            if key in give_up:
                logger.warning(f"跳过熔断章节: {point.get('title')}")
                continue
            tasks.append(Task(i, point, course))
    return tasks


def main():
    acc_id = os.environ.get("LEKE_ACC") or ""
    acc = load_account(acc_id)
    acc_id = str(acc.get("id") or "accd2ce1f69")
    username = str(acc.get("username") or os.environ.get("LEKE_USER") or "")
    password = str(acc.get("password") or os.environ.get("LEKE_PASS") or "")
    course_id = str(os.environ.get("LEKE_COURSE") or (acc.get("courses") or [""])[0])
    cookie = os.path.join(APP_DIR, f"cookies_{acc_id}.txt")
    dry = _bool(os.environ.get("LEKE_DRY"), False)

    common, tiku_conf = load_runtime()
    if os.environ.get("LEKE_VIDEO_MODE"):
        common["video_mode"] = os.environ["LEKE_VIDEO_MODE"].strip().lower()
    if os.environ.get("LEKE_SUBMIT") is not None:
        tiku_conf["submit"] = "true" if _bool(os.environ["LEKE_SUBMIT"]) else "false"

    # 章节选择
    pts_env = os.environ.get("LEKE_POINTS")
    if pts_env is not None and pts_env.strip():
        if pts_env.strip().upper() == "ALL":
            only_points = None
        else:
            only_points = {p.strip() for p in pts_env.split(",") if p.strip()}
    else:
        sel = (acc.get("chapter_sel") or {}).get(course_id) or []
        only_points = set(str(x) for x in sel) or None

    _emit("=" * 70)
    _emit(f"app_dir    = {APP_DIR}")
    _emit(f"account    = {acc_id} / {username} / {acc.get('realname')}")
    _emit(f"course     = {course_id}")
    _emit(f"cookie     = {cookie}")
    _emit(f"video_mode = {common['video_mode']}  speed={common['speed']}  jobs={common['jobs']}  "
          f"skip_finished={common['skip_finished']}  submit={tiku_conf.get('submit')}")
    _emit(f"provider   = {tiku_conf.get('provider')}")
    _emit(f"只刷章节   = {'ALL' if only_points is None else sorted(only_points)}")
    _emit(f"DRY        = {dry}")
    _emit("=" * 70)
    _preflight_font_map()

    # 题库（与 GUI worker 同路径）
    tiku = None
    try:
        from api.answer import Tiku
        tiku = Tiku.get_tiku_from_config(tiku_conf, config_path=CONFIG)
        tiku.init_tiku()
        logger.info(f"题库已就绪: {getattr(tiku, 'name', type(tiku).__name__)}")
    except BaseException as e:
        logger.error(f"题库初始化失败，将以无题库模式运行: {type(e).__name__}: {e}")

    cx = Chaoxing(account=Account(username, password), tiku=tiku,
                  session_key=acc_id, cookie_path=cookie)
    cx.kwargs.update({
        "query_delay": float(tiku_conf.get("delay", 0) or 0),
        "work_max_retries": int(common.get("work_max_retries", 3) or 3),
        "work_redo_enabled": bool(common.get("work_redo_enabled", False)),
    })

    res = cx.login(login_with_cookies=False)
    logger.info(f"login => {res}")
    if not res.get("status"):
        return 1
    logger.info(f"当前登录用户: {cx.get_name()}")

    courses = [c for c in (cx.get_course_list() or []) if str(c.get("courseId")) == course_id]
    if not courses:
        logger.error(f"未找到课程 {course_id}")
        return 2
    for c in courses:
        if only_points:
            c["selected_points"] = sorted(only_points)

    if dry:
        _emit("\n===== DRY：任务点构成 =====")
        for course in courses:
            pl = cx.get_course_point(course["courseId"], course["clazzId"], course.get("cpi"))
            for i, point in enumerate((pl or {}).get("points") or []):
                if only_points and str(point.get("id")) not in only_points:
                    continue
                if common["skip_finished"] and point.get("has_finished"):
                    _emit(f"  [跳过-已完成] {point.get('title')}")
                    continue
                jobs, info = cx.get_job_list(course, point, keep_passed=False)
                _emit(f"  ○ {point.get('title')}  (jobs={len(jobs)})")
                for j in jobs:
                    _emit(f"      - {j.get('type')} | {j.get('name') or j.get('title') or ''}"
                          f" | dur={j.get('duration')} | passed={j.get('ispassed')}")
        return 0

    # ---- 续刷循环（复刻 worker.start_study）----
    stop_event = threading.Event()
    skip_finished = bool(common.get("skip_finished", True))
    max_passes = int(os.environ.get("LEKE_MAX_PASSES", "20") or 20)
    NO_PROGRESS = 2
    GIVE_UP_AFTER = 3
    PASS_GAP = 3.0

    total_done = 0
    failed_all = 0
    pass_no = 0
    empty_streak = 0
    prev_keys = None
    give_up = set()
    fail_streak = {}
    t0 = time.time()

    while True:
        pass_no += 1
        logger.info(f"===== 第 {pass_no} 遍检查开始 =====")
        tasks = collect_tasks(cx, courses, only_points, skip_finished, give_up)
        if not tasks:
            logger.info(f"第 {pass_no} 遍：已无待处理章节，结束")
            break

        logger.info(f"第 {pass_no} 遍：共 {len(tasks)} 个章节")
        sched = study_engine.StudyScheduler(
            chaoxing=cx, tasks=tasks, config=common, stop_event=stop_event,
            on_start=lambda t: logger.info(f"  → 处理: {t.point.get('title')}"),
            on_done=lambda d, f, title: logger.info(f"    [{d}/{len(tasks)}] 完成 {title}（失败 {f}）"),
        )
        sched.run()

        failed_titles = {}
        for t in sched.failed:
            key = f"{t.course.get('courseId')}:{t.point.get('id')}"
            failed_titles[key] = t.point.get("title", "")
        gave_up_now = 0
        for key, title in failed_titles.items():
            fail_streak[key] = fail_streak.get(key, 0) + 1
            if fail_streak[key] >= GIVE_UP_AFTER and key not in give_up:
                give_up.add(key)
                gave_up_now += 1
                logger.warning(f"章节「{title}」连续 {GIVE_UP_AFTER} 遍失败，熔断放弃")
        for key in list(fail_streak):
            if key not in failed_titles:
                fail_streak[key] = 0

        total_done += sched.success_count
        failed_all += len(sched.failed)
        logger.info(f"第 {pass_no} 遍小结：成功 {sched.success_count}，失败 {len(sched.failed)}，"
                    f"累计成功 {total_done}")

        if not skip_finished:
            logger.info("从头重刷模式：一遍即结束")
            break

        cur_keys = {f"{t.course.get('courseId')}:{t.point.get('id')}" for t in tasks}
        if prev_keys is not None and cur_keys == prev_keys and not gave_up_now:
            empty_streak += 1
        else:
            empty_streak = 0
        prev_keys = cur_keys
        logger.info(f"章节集合大小 {len(cur_keys)}，empty_streak={empty_streak}")
        if empty_streak >= NO_PROGRESS:
            logger.info(f"连续 {NO_PROGRESS} 遍章节集合无变化，续刷结束")
            break
        if pass_no >= max_passes:
            logger.warning(f"达到兜底上限 {max_passes} 遍，停止")
            break
        time.sleep(PASS_GAP)

    _emit("")
    _emit("=" * 70)
    _emit(f"刷课结束：共 {pass_no} 遍，成功 {total_done} 个章节，失败 {failed_all} 个，"
          f"熔断 {len(give_up)} 个，用时 {time.time() - t0:.0f}s")
    _emit(f"日志：{LOG_PATH}")
    _emit("=" * 70)
    return 0


if __name__ == "__main__":
    code = 0
    try:
        code = main()
    except BaseException:
        traceback.print_exc(file=_REAL_OUT)
        if _logf:
            traceback.print_exc(file=_logf)
        code = 1
    finally:
        try:
            _logf and _logf.close()
        except Exception:
            pass
    _REAL_OUT.flush()
    os._exit(code)
