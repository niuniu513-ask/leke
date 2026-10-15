# -*- coding: utf-8 -*-
import argparse
import configparser
import enum
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any
from tqdm import tqdm
from api.answer import Tiku
from api.base import Chaoxing, Account, StudyResult
from api.config import GlobalConst as gc
from api.exceptions import LoginError, InputFormatError
from api.logger import logger
from api.notification import Notification
from api.live import Live
from api.live_process import LiveProcessor
from api.process import increase_learning_count_for_course

try:
    from queue import PriorityQueue, ShutDown
except ImportError:
    from queue import PriorityQueue


    class ShutDown(Exception):
        pass


class ChapterResult(enum.Enum):
    SUCCESS = 0,
    ERROR = 1,
    NOT_OPEN = 2,
    PENDING = 3
    DEFERRED = 4


def log_error(func):
    def wrapper(*args, **kwargs):
        try:
            func(*args, **kwargs)
        except BaseException as e:
            logger.error(f"Error in thread {threading.current_thread().name}: {e}")
            traceback.print_exception(type(e), e, e.__traceback__)
            raise

    return wrapper


def str_to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="乐课 · 在线课程学习助手（命令行模式）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--use-cookies", action="store_true", help="使用cookies登录")

    parser.add_argument(
        "-c", "--config", type=str, default=None, help="使用配置文件运行程序"
    )
    parser.add_argument("-u", "--username", type=str, default=None, help="手机号账号")
    parser.add_argument("-p", "--password", type=str, default=None, help="登录密码")
    parser.add_argument(
        "-l", "--list", type=str, default=None, help="要学习的课程ID列表, 以 , 分隔"
    )
    parser.add_argument(
        "-s", "--speed", type=float, default=1.0, help="视频播放倍速 (默认1, 最大2)"
    )
    parser.add_argument(
        "-j", "--jobs", type=int, default=4, help="同时进行的章节数 (默认4, 如果一个章节有多个任务点，不会限制同时处理任务点的数量)"
    )

    parser.add_argument(
        "-v",
        "--verbose",
        "--debug",
        action="store_true",
        help="启用调试模式, 输出DEBUG级别日志",
    )
    parser.add_argument(
        "-a", "--notopen-action", type=str, default="retry",
        choices=["retry", "ask", "continue"],
        help="遇到关闭任务点时的行为: retry-重试, ask-询问, continue-继续"
    )

    parser.add_argument("--auto-sign", action="store_true", help="自动签到")
    parser.add_argument(
        "--retry-interval", type=float, default=1.0, help="重试等待时间, 单位秒 (默认1.0)"
    )

    parser.add_argument(
        "-lc",
        "--add-learning-count",
        action="store_true",
        help="开启章节学习次数增加模式",
    )
    parser.add_argument(
        "-tc",
        "--target-count",
        type=int,
        default=100,
        help="章节学习次数目标总次数 (默认100)",
    )

    # 在解析之前捕获 -h 的行为
    if len(sys.argv) == 2 and sys.argv[1] in {"-h", "--help"}:
        parser.print_help()
        sys.exit(0)

    return parser.parse_args()


def load_config_from_file(config_path):
    """从配置文件加载设置"""
    config = configparser.ConfigParser()
    config.read(config_path, encoding="utf8")

    common_config: dict[str, Any] = {}
    tiku_config: dict[str, Any] = {}
    notification_config: dict[str, Any] = {}

    # 检查并读取common节
    if config.has_section("common"):
        common_config = dict(config.items("common"))
        # 处理course_list，将字符串转换为列表
        if "course_list" in common_config and common_config["course_list"]:
            common_config["course_list"] = [item.strip() for item in common_config["course_list"].split(",") if
                                            item.strip()]
        # 处理speed，将字符串转换为浮点数
        if "speed" in common_config:
            common_config["speed"] = float(common_config["speed"])
        # 视频学习模式 fast(秒过) / timed(计时长)，缺省保持原有行为
        common_config["video_mode"] = str(
            common_config.get("video_mode", "fast") or "fast").strip().lower()
        if common_config["video_mode"] not in ("fast", "timed"):
            common_config["video_mode"] = "fast"
        if "jobs" in common_config:
            common_config["jobs"] = int(common_config["jobs"])
        # 处理notopen_action，设置默认值为retry
        if "notopen_action" not in common_config:
            common_config["notopen_action"] = "retry"
        if "retry_interval" in common_config:
            common_config["retry_interval"] = float(common_config["retry_interval"])
        else:
            common_config["retry_interval"] = 1.0
        if "work_max_retries" in common_config:
            try:
                common_config["work_max_retries"] = int(common_config["work_max_retries"])
            except ValueError:
                common_config["work_max_retries"] = 3
        else:
            common_config["work_max_retries"] = 3
        if "work_redo_enabled" in common_config:
            common_config["work_redo_enabled"] = str_to_bool(common_config["work_redo_enabled"])
        if "use_cookies" in common_config:
            common_config["use_cookies"] = str_to_bool(common_config["use_cookies"])
        if "add_learning_count" in common_config:
            common_config["add_learning_count"] = str_to_bool(common_config["add_learning_count"])
        if "target_count" in common_config:
            common_config["target_count"] = int(common_config["target_count"])
        if "username" in common_config and common_config["username"] is not None:
            common_config["username"] = common_config["username"].strip()
        if "password" in common_config and common_config["password"] is not None:
            common_config["password"] = common_config["password"].strip()

    # 检查并读取tiku节
    if config.has_section("tiku"):
        tiku_config = dict(config.items("tiku"))
        # 处理数值类型转换
        for key in ["delay", "cover_rate"]:
            if key in tiku_config:
                tiku_config[key] = float(tiku_config[key])

    # 检查并读取notification节
    if config.has_section("notification"):
        notification_config = dict(config.items("notification"))

    return common_config, tiku_config, notification_config


def build_config_from_args(args):
    """从命令行参数构建配置"""
    common_config = {
        "use_cookies": args.use_cookies,
        "username": args.username,
        "password": args.password,
        "course_list": [item.strip() for item in args.list.split(",") if item.strip()] if args.list else None,
        "speed": args.speed or 1.0,
        "jobs": args.jobs,
        "notopen_action": args.notopen_action or "retry",
        "retry_interval": args.retry_interval or 1.0,
        "add_learning_count": args.add_learning_count,
        "target_count": args.target_count,
    }
    return common_config, {}, {}


def init_config():
    """初始化配置"""
    args = parse_args()

    if args.config:
        common_config, tiku_config, notification_config = load_config_from_file(args.config)
    else:
        common_config, tiku_config, notification_config = build_config_from_args(args)
    return common_config, tiku_config, notification_config, args.config


def init_chaoxing(common_config, tiku_config, config_path=None,
                  session_key="", cookie_path=None):
    """初始化超星实例

    session_key / cookie_path 为多账号扩展参数（默认空 → 全局单例 + cookies.txt，
    命令行行为完全不变）。桌面版为每个账号传入各自的 key 与 cookie 文件。
    """
    username = common_config.get("username", "")
    password = common_config.get("password", "")
    use_cookies = common_config.get("use_cookies", False)

    # 如果没有提供用户名密码，从命令行获取
    if (not username or not password) and not use_cookies:
        username = input("请输入你的手机号, 按回车确认\n手机号:")
        password = input("请输入你的密码, 按回车确认\n密码:")

    account = Account(username, password)

    # 设置题库
    tiku = Tiku.get_tiku_from_config(tiku_config, config_path=config_path)  # 载入题库
    tiku.init_tiku()  # 初始化题库

    # 获取查询延迟设置

    # 检查大模型连接（如果使用的是大模型题库）
    # 根据配置文件中的 provider 判断是否为大模型题库
    provider = tiku_config.get('provider', '')
    provider_list = [name.strip() for name in provider.split(',') if name.strip()]
    if any(name in ['AI', 'SiliconFlow'] for name in provider_list):
        check_connection = tiku_config.get('check_llm_connection', 'true').lower() == 'true'
        if check_connection:
            logger.info(f'正在验证大模型配置 (provider={provider})...')
            if not tiku.check_llm_connection():
                logger.error('大模型连接检查失败')
                choice = input('大模型连接检查失败，无法准确答题，是否继续运行？(Y/n): ').strip().lower()
                # 直接回车默认继续运行
                if choice not in ('', 'y', 'yes'):
                    raise RuntimeError('用户取消运行')
                logger.info('用户选择继续运行...')

    query_delay = tiku_config.get("delay", 0)

    # 章节检测答错后允许的最大重做次数（答错时反馈给AI并重新提交，直到全部正确）
    work_max_retries = common_config.get("work_max_retries", 3)

    # 是否允许自动重做(默认false: 仅检查并记录成绩, 适用于只允许作答一次的课程)
    work_redo_enabled = common_config.get("work_redo_enabled", False)

    # 实例化超星API
    chaoxing = Chaoxing(
        account=account,
        tiku=tiku,
        session_key=session_key,
        cookie_path=cookie_path,
        query_delay=query_delay,
        work_max_retries=work_max_retries,
        work_redo_enabled=work_redo_enabled,
    )

    # 视频学习模式：直接把「秒过」换成「按真实节奏上报」，学习时长才会被平台记录
    gc.VIDEO_MODE = str(common_config.get("video_mode", "fast") or "fast").lower()
    chaoxing.video_mode = gc.VIDEO_MODE
    logger.info(f"视频学习模式: {gc.VIDEO_MODE}")

    return chaoxing


def process_job(chaoxing: Chaoxing, course: dict, job: dict, job_info: dict, speed: float,
                on_job=None, skip_work: bool = False) -> StudyResult:
    """处理单个任务点

    on_job: 可选回调 on_job(course, job)，在真正下发请求前调用，
            供桌面版实时显示「正在处理哪个任务点」（CLI 下为 None，行为不变）。
    skip_work: 从头重刷已完成章节时为 True —— 测验不做，避免重复提交
              （补学习时长只需要视频/音频，文档/阅读重复上报无害）。
    """
    if on_job is not None:
        try:
            on_job(course, job)
        except Exception:
            pass
    # 视频任务
    if job["type"] == "video":
        logger.trace(f"识别到视频任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        # 从头重刷：已通过的视频把进度归零 —— timed 模式下从当前位置续播补不到
        # 前面那段时长，必须整个从头走一遍心跳平台才重新计。
        if job.get("ispassed"):
            job["playTime"] = 0
        # 超星的接口没有返回当前任务是否为Audio音频任务
        video_result = chaoxing.study_video(
            course, job, job_info, _speed=speed, _type="Video"
        )
        if video_result.is_failure():
            logger.warning("当前任务非视频任务, 正在尝试音频任务解码")
            video_result = chaoxing.study_video(
                course, job, job_info, _speed=speed, _type="Audio")
        if video_result.is_failure():
            logger.warning(
                f"出现异常任务 -> 任务章节: {course['title']} 任务ID: {job['jobid']}, 已跳过"
            )
        return video_result
    # 文档任务
    elif job["type"] == "document":
        logger.trace(f"识别到文档任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        return chaoxing.study_document(course, job)
    # 测验任务
    elif job["type"] == "workid":
        # 从头重刷时：任务点已通过(ispassed)或整章已完成(skip_work)的测验一律跳过，
        # 避免重复提交（一次性作答的课程会出问题）。补时长只靠视频/音频。
        if skip_work or job.get("ispassed"):
            logger.info(f"从头重刷：跳过章节检测（避免重复提交）: {course['title']}")
            return StudyResult.SUCCESS
        logger.trace(f"识别到章节检测任务, 任务章节: {course['title']}")
        return chaoxing.study_work(course, job, job_info)
    # 阅读任务
    elif job["type"] == "read":
        logger.trace(f"识别到阅读任务, 任务章节: {course['title']}")
        return chaoxing.study_read(course, job, job_info)
    # 直播任务
    elif job["type"] == "live":
        logger.trace(f"识别到直播任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        try:
            # 准备直播所需参数
            defaults = {
                "userid": chaoxing.get_uid(),
                "clazzId": course.get("clazzId"),
                "knowledgeid": job_info.get("knowledgeid")
            }

            # 创建直播对象
            live = Live(
                attachment=job,
                defaults=defaults,
                course_id=course.get("courseId"),
                # 直播请求必须走「当前账号」的会话，否则多账号并行时会串号
                session=getattr(chaoxing, "_session", None),
            )

            # 启动直播处理线程
            thread = threading.Thread(
                target=LiveProcessor.run_live,
                args=(live, speed),
                daemon=True
            )
            thread.start()
            thread.join()  # 等待直播处理完成
            return StudyResult.SUCCESS
        except Exception as e:
            logger.error(f"处理直播任务时出错: {str(e)}")
            return StudyResult.ERROR

    logger.error(f"未知任务类型: {job['type']}")
    return StudyResult.ERROR


@dataclass
class ChapterTask:
    index: int
    point: dict[str, Any]
    course: dict[str, Any]
    result: ChapterResult = ChapterResult.PENDING
    tries: int = 0
    # 收集任务时就已完成 —— 续刷时用它区分「本遍真有进展」和「本来就是绿的」
    already_finished: bool = False

    def __lt__(self, other):
        """比较两个任务的索引大小，用于优先级队列排序."""
        if not isinstance(other, ChapterTask):
            return NotImplemented
        return self.index < other.index


class JobProcessor:
    def __init__(self, chaoxing: Chaoxing, tasks: list[ChapterTask], config: dict[str, Any]):
        """初始化任务处理器."""
        if "jobs" not in config or not config["jobs"]:
            config["jobs"] = 4

        self.chaoxing = chaoxing
        self.speed = config["speed"]
        self.max_tries = 5
        self.tasks = tasks
        self.failed_tasks: list[ChapterTask] = []
        self.task_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.retry_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.wait_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.threads: list[threading.Thread] = []
        self.worker_num = config["jobs"]
        self.config = config
        self.retry_interval = config.get("retry_interval", 1.0)

    def run(self):
        for task in self.tasks:
            self.task_queue.put(task)

        for i in range(self.worker_num):
            thread = threading.Thread(target=self.worker_thread, daemon=True)
            self.threads.append(thread)
            thread.start()

        retry_worker = threading.Thread(target=self.retry_thread, daemon=True)
        retry_worker.start()

        self.task_queue.join()
        time.sleep(0.5)
        if hasattr(self.task_queue, "shutdown"):
            self.task_queue.shutdown()
            self.retry_queue.shutdown()
            retry_worker.join(timeout=5)
            for thread in self.threads:
                thread.join(timeout=5)

    @log_error
    def worker_thread(self):
        while True:
            try:
                task = self.task_queue.get()
            except ShutDown:
                logger.info("Queue shut down")
                return

            task.result = process_chapter(
                self.chaoxing, task.course, task.point, self.speed,
                skip_finished=str_to_bool(self.config.get("skip_finished", True)))

            match task.result:
                case ChapterResult.DEFERRED:
                    logger.warning('章节仅保存、未通过或回执待核对，不计完成，不自动重发')
                    self.failed_tasks.append(task)
                    self.task_queue.task_done()
                case ChapterResult.SUCCESS:
                    logger.debug("Task success: {} - {}", task.course["title"], task.point["title"])
                    self.task_queue.task_done()
                    logger.debug(f"unfinished task: {self.task_queue.unfinished_tasks}")

                case ChapterResult.NOT_OPEN:
                    if self.config["notopen_action"] == "continue":
                        logger.warning("章节未开启: {} - {}, 正在跳过", task.course["title"], task.point["title"])
                        self.task_queue.task_done()
                        continue

                    task.tries += 1
                    if task.tries >= self.max_tries:
                        logger.error(
                            "章节未开启: {} - {} 可能由于上一章节的章节检测未完成, 也可能由于该章节因为时效已关闭，"
                            "请手动检查完成并提交再重试。或者在配置中配置(自动跳过关闭章节/开启题库并启用提交)"
                            , task.course["title"], task.point["title"])
                        self.failed_tasks.append(task)
                        self.task_queue.task_done()
                        continue

                    # self.wait_queue.put(task)
                    self.retry_queue.put(task)

                case ChapterResult.ERROR:
                    task.tries += 1
                    logger.warning("重试任务 {} - {} ({}/{} 次尝试)", task.course["title"], task.point["title"],
                                   task.tries,
                                   self.max_tries)
                    if task.tries >= self.max_tries:
                        logger.error("任务重试次数达到上限: {} - {}", task.course["title"], task.point["title"])
                        self.failed_tasks.append(task)
                        self.task_queue.task_done()
                        continue
                    self.retry_queue.put(task)

                case _:
                    logger.error("任务 {} 的状态无效 {}", task.result, task.point["title"])
                    self.failed_tasks.append(task)
                    self.task_queue.task_done()

    @log_error
    def retry_thread(self):
        try:
            while True:
                task = self.retry_queue.get()
                time.sleep(self.retry_interval)
                self.task_queue.put(task)
                # task_done is not called when a task failed and needs to be retried so if is reinserted into the queue,
                # the task num will increase by one and become more than the real task number
                self.task_queue.task_done()
                self.retry_queue.task_done()
        except ShutDown:
            pass


def process_chapter(chaoxing: Chaoxing, course: dict[str, Any], point: dict[str, Any], speed: float,
                    on_job=None, skip_finished: bool = True) -> ChapterResult:
    """处理单个章节

    on_job: 可选回调 on_job(course, point, job, index, total)，
            在每个任务点开始处理前调用（index 从 1 开始）。
    """
    logger.info(f'当前章节: {point["title"]}')
    if point["has_finished"]:
        if skip_finished:
            logger.info(f'章节：{point["title"]} 已完成所有任务点')
            return ChapterResult.SUCCESS
        # 从头重刷：已完成章节重新走一遍任务点，timed 模式下视频会从头重播、
        # 把这段重新计进学习时长（base.study_video 已内置 play_time 归零逻辑）。
        logger.info(f'从头重刷：{point["title"]} 已完成，重新处理以累计学习时长（测验将跳过）')

    # 随机等待，避免请求过快
    chaoxing.rate_limiter.limit_rate(random_time=True, random_min=0, random_max=0.2)

    # 获取当前章节的所有任务点
    # 从头重刷（skip_finished=False）时保留已通过(isPassed)的任务点：
    #   ① 章节已完成的 —— 解码层会把它们全丢掉，job_list 为空，重刷变成空转；
    #   ② 章节未完成但任务点已通过的（如近现代史已秒过的视频）—— 同样被丢掉，
    #      视频重播补时长的机会就没了。
    # 两种场景都靠 process_job 的 ispassed/skip_work 防测验重复提交。
    job_info = None
    jobs, job_info = chaoxing.get_job_list(
        course, point, keep_passed=not skip_finished)

    # 发现未开放章节, 根据配置处理
    if job_info.get("notOpen", False):
        return ChapterResult.NOT_OPEN

    # 已经默认处理空任务，此处不需要判断
    if not jobs:
        pass

    job_results: list[StudyResult] = []
    for _idx, job in enumerate(jobs, start=1):
        result = process_job(chaoxing, course, job, job_info, speed,
                             on_job=(lambda c, j, i=_idx, n=len(jobs): on_job(c, point, j, i, n))
                             if on_job is not None else None,
                             # 从头重刷已完成章节时跳过测验：补时长只靠视频/音频，
                             # 测验重复提交在只允许一次作答的课程里会出问题。
                             skip_work=bool(point.get("has_finished")))
        job_results.append(result)

    for result in job_results:
        if result == StudyResult.DEFERRED:
            continue
        if result.is_failure():
            return ChapterResult.ERROR

    if StudyResult.DEFERRED in job_results:
        return ChapterResult.DEFERRED

    return ChapterResult.SUCCESS


def process_course(chaoxing: Chaoxing, course: dict[str, Any], config: dict):
    """处理单个课程"""
    logger.info(f"开始学习课程: {course['title']}")

    # 获取当前课程的所有章节
    point_list = chaoxing.get_course_point(
        course["courseId"], course["clazzId"], course["cpi"]
    )

    # 为了支持课程任务回滚, 采用下标方式遍历任务点

    _old_format_sizeof = tqdm.format_sizeof
    tqdm.format_sizeof = format_time

    tasks = []

    for i, point in enumerate(point_list["points"]):
        task = ChapterTask(point=point, index=i, course=course)
        tasks.append(task)
    p = JobProcessor(chaoxing, tasks, config)
    p.run()

    tqdm.format_sizeof = _old_format_sizeof


def filter_courses(all_course, course_list):
    """过滤要学习的课程"""
    if not course_list:
        # 手动输入要学习的课程ID列表
        print("*" * 10 + "课程列表" + "*" * 10)
        for course in all_course:
            print(f"ID: {course['courseId']} 班级ID: {course['clazzId']} 课程名: {course['title']}")
        print("*" * 28)
        print("提示: 同一 courseId 下若存在多个班级, 将分别完成。")
        try:
            course_list = input(
                "请输入想要学习的课程列表,以逗号分隔,例: 2151141,189191,198198\n"
            ).split(",")
        except Exception as e:
            raise InputFormatError("输入格式错误") from e

    # 筛选需要学习的课程
    course_task = []
    seen_keys = set()
    for course in all_course:
        key = (course["courseId"], course["clazzId"])
        if course["courseId"] in course_list and key not in seen_keys:
            course_task.append(course)
            seen_keys.add(key)
    
    # 如果没有指定课程，则学习所有课程
    if not course_task:
        course_task = all_course

    return course_task


def format_time(num, suffix='', divisor=''):
    total_time = round(num)
    sec = total_time % 60
    mins = (total_time % 3600) // 60
    hrs = total_time // 3600

    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{sec:02d}"

    return f"{mins:02d}:{sec:02d}"


def main():
    """主程序入口"""
    try:
        # 初始化配置
        common_config, tiku_config, notification_config, config_path = init_config()

        # 强制播放按照配置文件调节
        common_config["speed"] = min(2.0, max(1.0, common_config.get("speed", 1.0)))
        common_config["notopen_action"] = common_config.get("notopen_action", "retry")
        
        # 初始化增加章节学习次数配置
        add_learning_count = str_to_bool(common_config.get("add_learning_count", False))
        target_count = int(common_config.get("target_count", 100))
        
        # 初始化超星实例
        chaoxing = init_chaoxing(common_config, tiku_config, config_path=config_path)

        # 设置外部通知
        notification = Notification()
        notification.config_set(notification_config)
        notification = notification.get_notification_from_config()
        notification.init_notification()

        # 检查当前登录状态
        _login_state = chaoxing.login(login_with_cookies=common_config.get("use_cookies", False))
        if not _login_state["status"]:
            raise LoginError(_login_state["msg"])

        # 获取所有的课程列表
        all_course = chaoxing.get_course_list()

        # 过滤要学习的课程
        course_task = filter_courses(all_course, common_config.get("course_list"))

        # 开始学习
        logger.info(f"课程列表过滤完毕, 当前课程任务数量: {len(course_task)}")

        _old_format_sizeof = tqdm.format_sizeof
        tqdm.format_sizeof = format_time

        # 续刷：每一遍都重新读一次章节列表。学习通是「章节测验通过才解锁下一章」
        # 的链式结构，列表只读一次的话，一遍跑完时还没开放的章节就再也没人管了。
        skip_finished = str_to_bool(common_config.get("skip_finished", True))
        max_passes = 20          # 兜底上限，防止极端情况下无限续刷
        no_progress_passes = 2   # 连续这么多遍都没有新完成的章节就收手
        total_done = 0
        pass_no = 0
        empty_streak = 0

        while True:
            pass_no += 1
            if pass_no > 1:
                logger.info(f"===== 第 {pass_no} 遍检查开始 =====")

            tasks = []
            for course in course_task:
                logger.info(f"正在读取课程章节: {course['title']}")
                point_list = chaoxing.get_course_point(
                    course["courseId"], course["clazzId"], course["cpi"]
                )
                for i, point in enumerate(point_list["points"]):
                    if skip_finished and point.get("has_finished"):
                        continue
                    tasks.append(ChapterTask(point=point, index=i, course=course,
                                             already_finished=bool(point.get("has_finished"))))

            if not tasks:
                logger.info("已经没有需要处理的章节")
                break

            # 全局并发执行所有课程的任务点
            p = JobProcessor(chaoxing, tasks, common_config)
            p.run()

            done = sum(1 for t in tasks
                       if t.result == ChapterResult.SUCCESS and not t.already_finished)
            total_done += done

            # 从头重刷：收集时就是全量章节（含已完成），不存在「新解锁」要捞，
            # 一遍把全部内容重新处理完就结束 —— 第二遍只会把同样的视频再重播一次。
            if not skip_finished:
                logger.info("从头重刷模式：全部章节已重新处理一遍，结束")
                break

            empty_streak = 0 if done else empty_streak + 1
            if empty_streak >= no_progress_passes:
                logger.info(f"连续 {no_progress_passes} 遍没有新完成的章节，续刷结束")
                break
            if pass_no >= max_passes:
                logger.warning(f"续刷已达兜底上限 {max_passes} 遍，停止")
                break
            logger.info(f"第 {pass_no} 遍结束（新完成 {done} 个章节），3 秒后重新检查章节列表…")
            time.sleep(3)

        logger.info(f"刷课结束：共检查 {pass_no} 遍，完成 {total_done} 个章节")

        tqdm.format_sizeof = _old_format_sizeof

        logger.info("所有课程学习任务已完成")
        notification.send("chaoxing : 所有课程学习任务已完成")

        # 刷课完成后，如果开启了增加章节学习次数，则执行
        if add_learning_count:
            logger.info("刷课完成，开始增加章节学习次数...")
            common_config["target_count"] = target_count
            for course in course_task:
                increase_learning_count_for_course(chaoxing, course, common_config)
            logger.info("所有课程章节学习次数增加完成")
            notification.send("chaoxing : 所有课程章节学习次数增加完成")
        
    except SystemExit as e:
        if e.code != 0:
            logger.error(f"错误: 程序异常退出, 返回码: {e.code}")
        sys.exit(e.code)
    except KeyboardInterrupt as e:
        logger.error(f"错误: 程序被用户手动中断, {e}")
    except BaseException as e:
        logger.error(f"错误: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        try:
            notification.send(f"chaoxing : 出现错误 {type(e).__name__}: {e}\n{traceback.format_exc()}")
        except Exception:
            pass  # 如果通知发送失败，忽略异常
        raise e


if __name__ == "__main__":
    main()
