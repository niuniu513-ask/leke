# -*- coding: utf-8 -*-
import functools
import random
import secrets
import re
import threading
import time
from difflib import SequenceMatcher
from enum import Enum, IntEnum
from hashlib import md5
from typing import Optional, Literal
from typing_extensions import Self

import requests
from loguru import logger
from requests import RequestException
from requests.adapters import HTTPAdapter
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception
from tqdm import tqdm

from api.answer import Tiku, TikuManual, harvest_correct_answers
from api.answer_check import cut
from api.cipher import AESCipher
from api.config import GlobalConst as gc
from api.cookies import save_cookies, use_cookies
from api.decode import (
    decode_course_list,
    decode_course_point,
    decode_course_card,
    decode_course_folder,
    decode_questions_info,
)


def get_timestamp():
    return str(int(time.time() * 1000))


def describe_network_error(exc: BaseException) -> str:
    """把 requests / urllib3 的网络异常翻译成用户能看懂的中文提示。

    桌面版直接把这些异常抛到界面上时，用户看到的是
    「ConnectionError: HTTPSConnectionPool(host='passport2.chaoxing.com', ... NameResolutionError)」
    这类长串英文，根本不知道要干什么。这里按常见故障分类给可操作的建议。
    """
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if (
        "nameresolutionerror" in low
        or "getaddrinfo failed" in low
        or "name or service not known" in low
        or "nodename nor servname" in low
        or "temporary failure in name resolution" in low
    ):
        return "无法解析超星域名（DNS 失败）。请检查电脑网络是否正常，或关闭/切换代理后重试。"
    if "connection refused" in low:
        return "连接被拒绝。代理端口可能没有在监听，请检查代理软件是否正常运行。"
    if "connecttimeout" in low or "readtimeout" in low or "timed out" in low or "timeout" in low:
        return "连接超星超时。请检查网络或代理是否可用，然后重试。"
    if "sslerror" in low or "certificate" in low or "ssl" in low:
        return "HTTPS 证书校验失败，通常是代理拦截了流量。请检查代理设置。"
    if "proxyerror" in low or "proxy" in low:
        return "代理连接失败。请检查代理设置，或暂时关闭代理后重试。"
    if "max retries exceeded" in low or "connectionerror" in low or "connection aborted" in low:
        return "网络连接失败。请检查网络或代理后重试。"
    return f"网络异常：{text[:140]}"


def friendly_error(exc: BaseException) -> str:
    """网络类异常给中文提示，其他异常保留「类型: 信息」便于排查。"""
    if isinstance(exc, RequestException):
        return describe_network_error(exc)
    name = type(exc).__name__
    if "Connection" in name or "Timeout" in name or "SSL" in name or name == "OSError":
        return describe_network_error(exc)
    return f"{name}: {exc}"


class SessionManager:
    """会话管理。

    单账号 / 命令行：沿用原来的全局单例，cookie 文件 = cookies.txt，行为完全不变。
    多账号（桌面版）：用 ``create_session(key, cookie_path)`` 为每个账号拿一个
    **互相隔离** 的 requests.Session —— 各自持有自己的 cookie、互不覆盖，
    因此可以并行跑多个账号而不会串号。
    """

    _instance = None
    _login_lock = threading.Lock()
    #: 账号 key -> requests.Session（多账号各自独立）
    _pool: dict = {}
    _pool_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # 只在**首次**创建时建会话。否则每次 cls() 都会重跑 __init__，
            # 把已有 cookie 的全局会话悄悄换掉（表现为「刚登录又要重新登」）。
            cls._instance._session = cls.build_session(use_cookies())
        return cls._instance

    def __init__(self):
        # 会话已在 __new__ 里建好；这里保持空实现，避免重复构造。
        pass

    # ------------------------------------------------------------ 会话工厂
    @staticmethod
    def build_session(cookies: dict | None = None) -> requests.Session:
        """按统一约定造一个 session（超时、重试、默认请求头）。"""
        s = requests.Session()
        # 不吃环境代理（HTTP_PROXY/HTTPS_PROXY/Windows 注册表代理）：超星是
        # 国内站，走代理只会添乱 —— 用户机器上这些变量常残留已关掉的临时代理，
        # 一旦失效登录/刷课全挂且报错不指向根因。与智慧树 proxies={} 同待遇。
        s.trust_env = False
        s.mount("https://", HTTPAdapter(max_retries=10))
        s.mount("http://", HTTPAdapter(max_retries=10))
        s.request = functools.partial(s.request, timeout=5)
        # For debug purposes
        # s.verify=False
        s.headers.clear()
        s.headers.update(gc.HEADERS)
        if cookies:
            s.cookies.update(cookies)
        return s

    @classmethod
    def create_session(cls, key: str = "", cookie_path: str | None = None) -> requests.Session:
        """取（或新建）一个会话。

        key 为空 -> 默认单例，行为与改造前一致（命令行 / 单账号）。
        key 非空 -> 按 key 缓存一个独立会话，cookie 来自 cookie_path。
        """
        if not key:
            # 走 get_session() 而不是直接碰 _session：测试会替换 get_session
            # 注入假 session，这条路径必须保持可被替换。
            session = cls.get_session()
            session.cookies.update(use_cookies(cookie_path))
            return session

        with cls._pool_lock:
            session = cls._pool.get(key)
            if session is None:
                session = cls.build_session()
                session.cookies.update(use_cookies(cookie_path))
                cls._pool[key] = session
            return session

    @classmethod
    def drop_session(cls, key: str) -> None:
        """丢弃某个账号的会话（删除账号 / 重新登录时用）。"""
        if not key:
            return
        with cls._pool_lock:
            cls._pool.pop(key, None)

    # ------------------------------------------------------------ 原接口
    @classmethod
    def get_instance(cls) -> Self:
        return cls()

    @classmethod
    def get_session(cls) -> requests.Session:
        instance = cls.get_instance()
        return instance._session

    @classmethod
    def update_cookies(cls, cookie_path: str | None = None):
        cls.get_instance()._session.cookies.update(use_cookies(cookie_path))

    @classmethod
    def relogin_if_needed(cls, chaoxing_instance) -> bool:
        with cls._login_lock:
            # 检查 cookie 会话是否仍然无效
            if chaoxing_instance._validate_cookie_session():
                return True

            # 网络抖动导致的校验失败不要触发账号密码重登：
            # 一来必然失败，二来频繁用密码登录容易触发超星风控。
            if getattr(chaoxing_instance, "_cookie_net_error", None) is not None:
                logger.warning(
                    "Cookie 校验时网络异常，跳过自动重登: {}",
                    chaoxing_instance._cookie_net_error,
                )
                return False

            logger.info("Cookie session invalid, attempting thread-safe relogin...")
            if chaoxing_instance.account and chaoxing_instance.account.username and chaoxing_instance.account.password:
                login_result = chaoxing_instance.login(login_with_cookies=False)
                if login_result.get("status"):
                    # 只刷新「这个实例自己的」会话。
                    # 这里千万不能调 cls.update_cookies() —— 那会写全局单例，
                    # 多账号并行刷课时会互相串号。
                    try:
                        chaoxing_instance.sync_cookies_from_file()
                    except Exception:
                        pass
                    logger.info("Thread-safe relogin succeeded")
                    return True
                else:
                    logger.warning(f"Thread-safe relogin failed: {login_result.get('msg')}")
            return False


class Account:
    username = None
    password = None
    last_login = None
    isSuccess = None

    def __init__(self, _username, _password):
        self.username = _username
        self.password = _password


class RateLimiter:
    def __init__(self, call_interval):
        self.last_call = time.time()
        self.lock = threading.Lock()
        self.call_interval = call_interval

    def limit_rate(self, random_time=False, random_min=0.0, random_max=1.0):
        with self.lock:
            now = time.time()
            base_wait = max(self.last_call + self.call_interval - now, 0)
            extra_wait = random.uniform(random_min, random_max) if random_time else 0
            call_wait = base_wait + extra_wait
            self.last_call = now + call_wait

        time.sleep(call_wait)


class StudyResult(Enum):
    SUCCESS = 0
    FORBIDDEN = 1  # 403
    ERROR = 2
    TIMEOUT = 3
    DEFERRED = 4  # Draft saved or submission receipt not yet confirmed; do not resubmit blindly.

    def is_success(self):
        return self == StudyResult.SUCCESS

    def is_failure(self):
        return self != StudyResult.SUCCESS


class SignType(IntEnum):
    NORMAL = 0
    GESTURE = 3
    LOCATION = 4


class ActivityStatus(IntEnum):
    ACTIVE = 1
    INACTIVE = 2


class ActivityType(IntEnum):
    SIGNIN = 2


def multi_cut(answer: str, origin_html_content="", logger=logger):
    """
    将多选题答案字符串按特定字符进行切割, 并返回切割后的答案列表
    """
    res = cut(answer)
    if res is None:
        logger.warning(
            f"未能从网页中提取题目信息, 以下为相关信息：\n\t{answer}\n\n{origin_html_content}\n"
        )
        logger.warning("未能正确提取题目选项信息! 请反馈并提供以上信息")
        return None
    else:
        return res


def clean_res(res):
    cleaned_res = []
    if isinstance(res, str):
        res = [res]
    for c in res:
        # 仅在字符串长度大于1时才尝试去除开头的字母编号，防止误删单个字母答案
        cleaned = re.sub(r'^[A-Za-z]\s*[.、:：)?）]?\s*|[.,!?;:，。！？；：]', '', c) if len(c) > 1 else c
        cleaned_res.append(cleaned.strip())
    return cleaned_res


def normalize_text(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    # 统一常见异体字符，降低“风/⻛”类差异导致的匹配失败。
    char_map = str.maketrans({
        '⻛': '风',
        '⻔': '门',
        '⻋': '车',
        '⻢': '马',
    })
    normalized = text.translate(char_map)
    normalized = re.sub(r'^[A-Za-z]\s*[.、:：)?）]?\s*', '', normalized)
    normalized = re.sub(r'\s+', '', normalized)
    normalized = re.sub(r'[，。！？；：,.!?;:()（）\[\]【】"“”‘’\-_/\\|]', '', normalized)
    return normalized.lower()


def get_option_text(option: str) -> str:
    return re.sub(r'^[A-Za-z]\s*[.、:：)?）]?\s*', '', option).strip()


def best_option_by_similarity(target: str, options: list, threshold: float = 0.8) -> str:
    if not target or not options:
        return ""
    target_norm = normalize_text(target)
    if not target_norm:
        return ""

    best_letter = ""
    best_score = 0.0
    for option in options:
        option_text = get_option_text(option)
        option_norm = normalize_text(option_text)
        if not option_norm:
            continue
        score = SequenceMatcher(None, target_norm, option_norm).ratio()
        if score > best_score:
            best_score = score
            best_letter = option[:1]

    if best_score >= threshold:
        logger.info(f"相似度兜底匹配成功: {best_letter} (score={best_score:.2f}, threshold={threshold:.2f})")
        return best_letter
    return ""


def is_subsequence(a, o):
    iter_o = iter(o.lower())
    return all(c in iter_o for c in a.lower())



def _parse_work_record_list(html_text: str) -> list[tuple[int, float]]:
    """
    解析章节检测作答记录列表页面（/work/record-list）。

    Args:
        html_text: record-list 页面 HTML

    Returns:
        作答记录列表，元素为 (作答序号times, 成绩score)，例如 [(0, 80.0), (1, 100.0)]
    """
    records = []
    times_list = re.findall(r'viewNum">第(\d+)次', html_text)
    scores = re.findall(r'viewScore">([\d.]+)分', html_text)
    if len(times_list) != len(scores):
        # 次数/成绩数量不齐时 zip 会错位, 成绩按 0 处理(放弃分数捷径, 仅做逐题比对)
        logger.warning(f"作答记录次数/成绩数量不匹配({len(times_list)}/{len(scores)}), 成绩按0处理")
        scores = ['0'] * len(times_list)
    for t, s in zip(times_list, scores):
        try:
            records.append((int(t), float(s)))
        except ValueError:
            continue
    return records


def _parse_work_record_detail(html_text: str) -> list[dict]:
    """
    解析章节检测单次作答详情页面（/work/record-detail）。

    Args:
        html_text: record-detail 页面 HTML

    Returns:
        每题信息列表：{id, title, type_label, my_answer, correct_answer,
                       parse_ok, has_correct, is_correct, q_score, options}

    关于「正确答案」：不少课程由教师关闭了「显示答案解析」，此时页面上
    **根本没有正确答案字段**（只剩「我的答案」+ 平台判的对错标记
    ``marking_dui`` / ``marking_cuo`` + 每题得分）。因此额外解析：
    - ``is_correct``：平台判的对错（True/False/None），是这类页面上
      唯一可信的对错信源；
    - ``options``：选项字母 → 选项文本，用于把「我的答案」的字母还原成
      文本答案（答对的题才敢沉淀进知识库）；
    - ``has_correct``：页面是否真的提供了正确答案字段。
    """
    questions = []
    for qm in re.finditer(r'<div class="TiMu[^"]*singleQuesId" data="(\d+)"[^>]*>(.*?)(?=<div class="TiMu|$)', html_text, re.S):
        qid = qm.group(1)
        qb = qm.group(2)

        # 题型 + 题目
        tm = re.search(r'newZy_TItle">(.*?)</span>(.*?)</div>', qb, re.S)
        if tm:
            type_label = re.sub(r'<[^>]+>', '', tm.group(1)).strip()
            title = re.sub(r'<[^>]+>', '', tm.group(2))
        else:
            type_label = ""
            title = ""
        title = re.sub(r'\s+', ' ', title).strip()

        # 我的答案/正确答案: 多模式匹配, 兼容属性顺序变化与额外 class 的页面变体
        ans_patterns = [
            r'{label}[:：]</span>\s*<div class="fl answerCon">\s*(.*?)\s*</div>',
            r'{label}[:：]\s*</span>\s*<div[^>]*class="[^"]*answerCon[^"]*"[^>]*>\s*(.*?)\s*</div>',
            r'{label}[:：]\s*</span>\s*<span[^>]*>\s*(.*?)\s*</span>',
        ]

        def _extract_answer(label):
            for pat in ans_patterns:
                m = re.search(pat.format(label=label), qb, re.S)
                if m:
                    return re.sub(r'<[^>]+>', '', m.group(1)).strip()
            return None

        my_raw = _extract_answer('我的答案')
        correct_raw = _extract_answer('正确答案')

        # 平台对错标记（正确答案缺失时的唯一信源）
        if 'marking_dui' in qb:
            is_correct = True
        elif 'marking_cuo' in qb:
            is_correct = False
        else:
            is_correct = None
        sm = re.search(r'scoreNum">([\d.]+)<', qb)
        try:
            q_score = float(sm.group(1)) if sm else None
        except (TypeError, ValueError):
            q_score = None

        # 选项：字母 -> 文本
        options = []
        for om in re.finditer(
                r'<i\s+class="fl">([A-Za-z])[、.．]?\s*</i>\s*<a[^>]*>(.*?)</a>', qb, re.S):
            text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', om.group(2))).strip()
            if text:
                options.append((om.group(1).upper(), text))

        questions.append({
            "id": qid,
            "title": title,
            "type_label": type_label,
            "my_answer": my_raw or '',
            "correct_answer": correct_raw or '',
            "parse_ok": my_raw is not None and correct_raw is not None,
            "has_correct": correct_raw is not None,
            "is_correct": is_correct,
            "q_score": q_score,
            "options": options,
        })
    return questions


def judge_work_detail(detail: list) -> dict:
    """逐题判定作答详情对错。

    判定优先级：
    1. 平台给了正确答案 → 逐题文本比对（可产出给 AI 重做的错误反馈）；
    2. 没给正确答案（教师关闭了答案展示）→ 退回用平台自己的对错标记
       ``is_correct`` —— 单选/多选/判断都能判，只是拿不到正确答案文本，
       因此 ``feedback`` 为空（重做时无法给 AI 有效反馈）；
    3. 标记也没有 → 计入 ``unknown``（无法判定），不当作「答错」。

    Returns:
        {"all_correct": bool, "feedback": list[str], "wrong": list[str], "unknown": int}
        wrong 为明确判错的题目文本列表。
    """
    feedback: list[str] = []
    wrong: list[str] = []
    unknown = 0
    all_correct = True
    for q in detail or []:
        correct_ans = (q.get("correct_answer") or "").strip()
        if correct_ans:
            my_ans = (q.get("my_answer") or "").strip()
            if normalize_answer_text(my_ans) != normalize_answer_text(correct_ans):
                all_correct = False
                if q.get("title"):
                    wrong.append(q["title"])
                feedback.append(
                    f"- 题目：{q.get('title', '')}\n"
                    f"  题型：{q.get('type_label', '')}\n"
                    f"  你的上次答案：{my_ans or '(空)'}\n"
                    f"  正确答案：{correct_ans}"
                )
            continue
        ic = q.get("is_correct")
        if ic is True:
            continue
        if ic is False:
            all_correct = False
            if q.get("title"):
                wrong.append(q["title"])
            continue
        unknown += 1
        all_correct = False
    return {"all_correct": all_correct, "feedback": feedback,
            "wrong": wrong, "unknown": unknown}


_JUDGE_TRUE_SET = {'TRUE', 'T', '1', '对', '正确', '√', '是', 'YES', 'Y'}
_JUDGE_FALSE_SET = {'FALSE', 'F', '0', '错', '错误', '×', 'X', '否', 'NO', 'N', '不对', '不正确'}


def normalize_answer_text(ans) -> str:
    """作答详情答案归一化: 消除 对/错与true/false、全半角标点等格式差异.

    归一化是确定性的, 两侧同格式必然相等; 跨格式差异(如字母 vs 选项全文)
    交由调用方的满分分数兜底处理.
    """
    s = re.sub(r'\s+', '', str(ans or '')).upper()
    if s in _JUDGE_TRUE_SET:
        return 'TRUE'
    if s in _JUDGE_FALSE_SET:
        return 'FALSE'
    if re.fullmatch(r'[A-Z0-9]+', s):
        return s
    s = re.sub(r'^[A-Z][.、:：)?）]', '', s)
    s = re.sub(r'[，。、！？；：,.!?;:()（）\[\]【】"“”‘’\-_/\\|]', '', s)
    return s


class Chaoxing:
    def __init__(self, account: Account = None, tiku: Tiku = None,
                 session_key: str = "", cookie_path: str | None = None, **kwargs):
        self.account = account
        self.cipher = AESCipher()
        self.tiku = tiku
        self.kwargs = kwargs
        self.rollback_times = 0
        self.rate_limiter = RateLimiter(0.5)  # 其他接口速率限制比较松
        self.video_log_limiter = RateLimiter(2)  # 上报进度极其容易卡验证码，限制2s一次
        # 上一次 _validate_cookie_session() 若因「网络异常」失败，记下原始异常。
        # 用来区分「cookie 真失效」和「网络断了」——两者都返回 False，但处理方式完全不同。
        self._cookie_net_error = None

        # ---- 多账号支持（向后兼容扩展）----
        # session_key 为空：沿用原来的全局会话，命令行 / 单账号行为完全不变；
        # session_key 非空：本实例独占一个会话 + 独立的 cookie 文件，
        #                   多个账号之间 cookie 互不覆盖，因此可以并行跑。
        self.session_key = str(session_key or "")
        self.cookie_path = cookie_path or gc.COOKIES_PATH
        self._own_session = SessionManager.create_session(
            self.session_key, self.cookie_path)

    # ------------------------------------------------------------ 会话
    @property
    def _session(self) -> requests.Session:
        """本实例使用的会话。

        正常构造的实例在 __init__ 里就固定了自己的会话；
        ``object.__new__(Chaoxing)`` 造出来的桩没有走 __init__，
        这时回落到全局单例 —— 方便测试替换 SessionManager.get_session 注入假会话。
        """
        own = getattr(self, "_own_session", None)
        if own is not None:
            return own
        return SessionManager.get_session()

    def sync_cookies_from_file(self):
        """把本账号 cookie 文件里的内容并入自己的会话。"""
        try:
            self._session.cookies.update(use_cookies(self.cookie_path))
        except Exception:
            pass

    def login(self, login_with_cookies=False):
        if login_with_cookies:
            logger.info("Logging in with cookies")
            self.sync_cookies_from_file()
            logger.debug(f"Logged in with cookies: {self._session.cookies}")
            if not self._validate_cookie_session():
                if self._cookie_net_error is not None:
                    # 网络/代理不通时不要把责任推给 cookie，否则用户会跑去重新抓 cookie，
                    # 而真正的问题只是网断了。直接返回网络错误，也不回退账号密码登录
                    # （网络都断了，账号登录同样会失败，只会多刷一次无谓请求）。
                    msg = describe_network_error(self._cookie_net_error)
                    logger.error(
                        "Cookie 校验时网络异常，按网络故障处理（不判定为 cookie 失效）: {}",
                        self._cookie_net_error,
                    )
                    return {"status": False, "msg": msg, "net_error": True}
                logger.warning("Cookie 登录校验失败，尝试使用账号密码重新登录")
                if self.account and self.account.username and self.account.password:
                    return self.login(login_with_cookies=False)
                return {"status": False, "msg": "cookies 已失效，请更新 cookies 或提供账号密码"}
            logger.info("登录成功...")
            try:
                realname = self.get_name()
                if realname:
                    logger.info(f"当前登录用户: {realname}")
            except Exception as e:
                logger.debug(f"获取当前登录用户名失败: {e}")
            return {"status": True, "msg": "登录成功"}

        _session = requests.Session()
        # 登录请求绝不吃环境代理（见 build_session 里的注释）
        _session.trust_env = False
        _url = "https://passport2.chaoxing.com/fanyalogin"
        _data = {
            "fid": "-1",
            "uname": self.cipher.encrypt(self.account.username),
            "password": self.cipher.encrypt(self.account.password),
            "refer": "https%3A%2F%2Fi.chaoxing.com",
            "t": True,
            "forbidotherlogin": 0,
            "validate": "",
            "doubleFactorLogin": 0,
            "independentId": 0,
        }
        logger.trace("正在尝试登录...")
        try:
            # 必须带超时：没超时的话，网络半死时会一直挂着，界面卡在「登录中…」不动
            resp = _session.post(
                _url, headers=gc.HEADERS, data=_data, timeout=gc.HTTP_TIMEOUT
            )
        except RequestException as exc:
            logger.error(f"账号登录请求失败: {type(exc).__name__}: {exc}")
            return {"status": False, "msg": describe_network_error(exc), "net_error": True}

        # 超星在要求验证码 / 触发风控时会返回 HTML 而不是 JSON，
        # 原代码直接 resp.json()["status"] 会抛 JSONDecodeError / KeyError 糊到界面上。
        try:
            payload = resp.json()
        except ValueError:
            low = (resp.text or "").lower()
            logger.error(
                "登录响应不是 JSON (HTTP {}): {}", resp.status_code, (resp.text or "")[:200]
            )
            if "captcha" in low or "verify" in low or "验证码" in (resp.text or ""):
                return {
                    "status": False,
                    "msg": "超星要求输入验证码。请改用 cookies.txt 登录，或稍后在手机上完成验证后重试。",
                }
            return {
                "status": False,
                "msg": f"超星返回了非预期内容（HTTP {resp.status_code}），请稍后重试或改用 cookies 登录。",
            }

        if not isinstance(payload, dict):
            logger.error("登录响应格式异常: {}", str(payload)[:200])
            return {"status": False, "msg": "超星返回格式异常，请稍后重试。"}

        if payload.get("status") is True:
            save_cookies(_session, self.cookie_path)
            # 把登录拿到的 cookie 灌进本实例自己的会话。
            # 不能用 SessionManager.update_cookies()：那写的是全局单例，
            # 多账号并行时会互相覆盖，导致「登了 B 把 A 踢掉」。
            self._session.cookies.update(_session.cookies.get_dict())
            logger.info("登录成功...")
            try:
                realname = self.get_name()
                if realname:
                    logger.info(f"当前登录用户: {realname}")
            except Exception as e:
                logger.debug(f"获取当前登录用户名失败: {e}")
            return {"status": True, "msg": "登录成功"}

        msg = payload.get("msg2") or payload.get("msg") or "账号或密码不正确"
        return {"status": False, "msg": str(msg)}

    def get_name(self=None) -> str:
        """获取当前登录用户名。

        既支持实例调用 ``cx.get_name()``（用本账号的会话），
        也支持类调用 ``Chaoxing.get_name()``（用全局会话，兼容旧写法）。
        """
        _session = self._session if self is not None else SessionManager.get_session()
        try:
            resp = _session.get("https://passport2.chaoxing.com/mooc/accountManage", timeout=10)
            if resp.status_code == 200:
                match = re.search(r'id="messageName"\s+value="([^"]*)"', resp.text)
                if match:
                    return match.group(1).strip()
        except Exception as e:
            logger.debug(f"获取用户名失败: {e}")
        return ""

    def _validate_cookie_session(self) -> bool:
        session = self._session
        self._cookie_net_error = None
        if not session.cookies.get("_uid"):
            return False

        test_session = requests.Session()
        test_session.trust_env = False
        test_session.headers.update(gc.HEADERS)
        test_session.cookies.update(session.cookies.get_dict())

        try:
            resp = test_session.post(
                "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata",
                data={"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0},
                timeout=8,
            )
        except RequestException as exc:
            logger.debug("Cookie validation request failed: {}", exc)
            # 网络异常 ≠ cookie 失效。先记下来，交给 login() 判断并给「网络故障」提示。
            self._cookie_net_error = exc
            return False

        if resp.status_code != 200:
            return False

        if "passport2.chaoxing.com" in resp.text or "login" in resp.text.lower():
            return False

        return True

    def get_fid(self):
        _session = self._session
        return _session.cookies.get("fid", 1024)

    def get_uid(self):
        s = self._session
        if "_uid" in s.cookies:
            return s.cookies["_uid"]
        if "UID" in s.cookies:
            return s.cookies["UID"]
        raise ValueError("Cannot get uid !")

    def get_course_list(self):
        _session = self._session
        _url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata"
        _data = {"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0}
        logger.trace("正在读取所有的课程列表...")

        # 接口突然抽风, 增加headers
        # 有可能只是referer的问题
        _headers = {
            "Referer": "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction?moocDomain=https://mooc1-1.chaoxing.com/mooc-ans",
        }
        _resp = _session.post(_url, headers=_headers, data=_data)
        # logger.trace(f"原始课程列表内容:\n{_resp.text}")
        logger.info("课程列表读取完毕...")
        course_list = decode_course_list(_resp.text)

        _interaction_url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction"
        _interaction_resp = _session.get(_interaction_url)
        course_folder = decode_course_folder(_interaction_resp.text)
        for folder in course_folder:
            _data = {
                "courseType": 1,
                "courseFolderId": folder["id"],
                "query": "",
                "superstarClass": 0,
            }
            _resp = _session.post(_url, data=_data)
            course_list += decode_course_list(_resp.text)
        return course_list

    def get_activity_list(self, course: dict) -> list[dict]:
        s = self._session
        url = "https://mobilelearn.chaoxing.com/v2/apis/active/student/activelist"
        params = {
            "fid": self.get_fid(),
            "courseId": course["courseId"],
            "classId": course["clazzId"],
            "showNotStartedActive": 0,
            "_": get_timestamp()
        }
        resp = s.get(url, params=params, allow_redirects=False)
        if resp.status_code != 200:
            logger.error("Failed to get activity list, return code: " + str(resp.status_code))
            logger.debug("Request url: " + resp.url)
            return []

        data = resp.json()
        if data["result"] != 1:
            logger.error("Unknown status: {} {}", data["result"], data["errorMsg"])
            logger.debug("Request url: " + resp.url)
            return []

        return data["data"]["activeList"]

    def pre_sign(self, course: dict, activity_id):
        s = self._session
        params = {
            "general": 1,
            "sys": 1,
            "ls": 1,
            "appType": 15,
            "tid": '',
            "ut": 's',
            "uid": self.get_uid(),
            "activePrimaryId": activity_id,
            "courseId": course["courseId"],
            "classId": course["clazzId"],
        }
        resp = s.get('https://mobilelearn.chaoxing.com/newsign/preSign', params=params)
        resp_txt = resp.text
        logger.debug("Request url" + resp.url)
        if resp.status_code != 200:
            logger.error("Failed to get sign in, return code: " + str(resp.status_code) + "message: " + resp_txt)

        return resp_txt

    def sign_in_normal(self, course: dict, activity_id, name="", obj_id="aaa", lat=-1, lon=-1, type_=SignType.NORMAL):
        s = self._session
        params = {
            "activeId": activity_id,
            "uid": self.get_uid(),
            "fid": self.get_fid(),
            "courseId": course["courseId"],
            "classId": course["clazzId"],
            "clientip": "",
            "objectId": obj_id,
            "name": name,
            "useragent": "",
            "latitude": lat,
            "longitude": lon,
            "appType": "15",
        }

        resp = s.get("https://mobilelearn.chaoxing.com/pptSign/stuSignajax", params=params)

        resp_txt = resp.text
        if resp.status_code != 200:
            logger.error("Failed to get sign in, return code: " + str(resp.status_code) + "message: " + resp_txt)

        if type_ != SignType.LOCATION:
            return resp_txt

        pattern = r"[^0-9\.]*(.+)米[^0-9\.]*"
        msg = re.match(pattern, resp_txt)
        logger.warning(f"距离签到位置 {msg}m")
        # TOD0: Implement triangulation for location signs
        return resp_txt

    def get_course_point(self, _courseid, _clazzid, _cpi):
        _session = self._session
        _url = f"https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse?courseid={_courseid}&clazzid={_clazzid}&cpi={_cpi}&ut=s"
        logger.trace("URL: " + _url)
        logger.trace("开始读取课程所有章节...")
        _resp = _session.get(_url)
        _resp.raise_for_status()
        logger.trace(f"原始章节列表内容:\n{_resp.text}")
        logger.info("课程章节读取成功...")
        return decode_course_point(_resp.text)

    def get_job_list(self, course: dict, point: dict, keep_passed: bool = False) -> tuple[list[dict], dict]:
        """读取章节任务点列表。

        keep_passed: 保留已通过(isPassed)的任务点 —— 从头重刷已完成章节补学习时长时传
        True，否则解码层会把已完成任务点全部丢掉、job_list 为空（重刷空转）。
        """
        _session = self._session
        self.rate_limiter.limit_rate()
        job_list = []
        job_info = {}
        cards_params = {
            "clazzid": course["clazzId"],
            "courseid": course["courseId"],
            "knowledgeid": point["id"],
            "ut": "s",
            "cpi": course["cpi"],
            "v": "2025-0424-1038-3",
            "mooc2": 1
        }

        # 学习界面任务卡片数, 很少有3个的, 但是对于章节解锁任务点少一个都不行, 可以从API /mooc-ans/mycourse/studentstudyAjax获取值, 或者干脆直接加, 但二者都会造成额外的请求
        for _possible_num in "0123456":

            logger.trace("开始读取章节所有任务点...")

            cards_params.update({"num": _possible_num})
            _resp = _session.get("https://mooc1.chaoxing.com/mooc-ans/knowledge/cards", params=cards_params)
            if _resp.status_code != 200:
                logger.error(f"未知错误: {_resp.status_code} 正在跳过")
                logger.error(_resp.text)
                return [], {}

            _job_list, _job_info = decode_course_card(_resp.text, keep_passed=keep_passed)
            if _job_info.get("notOpen", False):
                # 直接返回, 节省一次请求
                logger.info("该章节未开放")
                return [], _job_info

            job_list += _job_list
            job_info.update(_job_info)

        if not job_list:
            self.study_emptypage(course, point)

        logger.trace(f"原始任务点列表内容:\n{_resp.text}")
        logger.info("章节任务点读取成功...")

        return job_list, job_info

    def get_enc(self, clazzId, jobid, objectId, playingTime, duration, userid):
        return md5(
            f"[{clazzId}][{userid}][{jobid}][{objectId}][{playingTime * 1000}][d_yHJ!$pdA~5][{duration * 1000}][0_{duration}]"
            .encode()).hexdigest()

    def video_progress_log(
            self,
            _session,
            _course,
            _job,
            _job_info,
            _dtoken,
            _duration,
            _playingTime,
            _type: str = "Video",
            _isdrag: int = 3,
            headers: Optional[dict] = None,
    ) -> tuple[bool, int]:

        if headers is None:
            logger.warning("null headers")
            headers = gc.VIDEO_HEADERS

        self.video_log_limiter.limit_rate(random_time=True, random_max=2)

        if "courseId" in _job["otherinfo"]:
            logger.error(_job["otherinfo"])
            raise RuntimeError("this is not possible")

        enc = self.get_enc(_course["clazzId"], _job["jobid"], _job["objectid"], _playingTime, _duration, self.get_uid())
        params = {
            "clazzId": _course["clazzId"],
            "playingTime": _playingTime,
            "duration": _duration,
            "clipTime": f"0_{_duration}",
            "objectId": _job["objectid"],
            "otherInfo": _job["otherinfo"],
            "courseId": _course["courseId"],
            "jobid": _job["jobid"],
            "userid": self.get_uid(),
            "isdrag": _isdrag,
            "view": "pc",
            "enc": enc,
            "dtype": _type
        }

        _url = (
            f"https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/"
            f"{_course['cpi']}/"
            f"{_dtoken}"
        )

        face_capture_enc = _job["videoFaceCaptureEnc"]
        att_duration = _job["attDuration"]
        att_duration_enc = _job["attDurationEnc"]

        if face_capture_enc:
            params["videoFaceCaptureEnc"] = face_capture_enc
        if att_duration:
            params["attDuration"] = att_duration
        if att_duration_enc:
            params["attDurationEnc"] = att_duration_enc

        def perform_request(rt_val):
            params.update({"rt": rt_val, "_t": get_timestamp()})
            res = _session.get(_url, params=params, headers=headers)
            if res.status_code == 403 or '验证码' in res.text or 'validate' in res.text:
                logger.warning("检测到验证码拦截，正在尝试自动通过验证码...")
                try:
                    from api.captcha import CxCaptcha
                    cookies_str = "; ".join([f"{k}={v}" for k, v in _session.cookies.items()])
                    ua = headers.get("User-Agent", gc.HEADERS.get("User-Agent"))
                    ocr_inst = getattr(self, '_ocr', None)
                    if ocr_inst is None:
                        from api.captcha import ocr_init
                        ocr_inst = ocr_init()
                        if ocr_inst:
                            self._ocr = ocr_inst
                    captcha_solver = CxCaptcha(user_agent=ua, cookies=cookies_str, ocr=ocr_inst)
                    solved = False
                    for attempt in range(3):
                        logger.info(f"第 {attempt + 1} 次尝试通关验证码...")
                        if captcha_solver.try_pass():
                            logger.success("验证码通关成功！")
                            solved = True
                            break
                        else:
                            logger.warning("验证码验证失败，正在重试...")
                            time.sleep(2)
                    if solved:
                        _session.cookies.update(captcha_solver.s.cookies)
                        res = _session.get(_url, params=params, headers=headers)
                    else:
                        logger.error("多次验证码通关失败，可能需要手动干预。")
                except Exception as e:
                    logger.error(f"验证码通关逻辑异常: {e}")
            return res

        rt = _job['rt']
        if not rt:
            rt_search = re.search(r"-rt_([1d])", _job['otherinfo'])
            if rt_search:
                rt_char = rt_search.group(1)
                rt = "0.9" if rt_char == "d" else "1"
                logger.trace(f"Got rt from otherinfo: {rt}")

        if rt:
            logger.trace(f"Got rt: {rt}")
            _job['rt'] = rt
            resp = perform_request(rt)
        else:
            logger.warning("Failed to get rt")
            for rt in [0.9, 1]:
                resp = perform_request(rt)
                if resp.status_code == 200:
                    logger.trace(resp.text)
                    return resp.json()["isPassed"], 200
                elif resp.status_code == 403:
                    logger.warning("出现403报错, 正常尝试切换rt")
                else:
                    logger.warning("未知错误 jobid={}, status_code={}, 摘要:\n{}",
                                   _job.get("jobid"),
                                   resp.status_code,
                                   resp.text[:200])
                    break

        if resp.status_code == 200:
            logger.trace(resp.text)
            return resp.json()["isPassed"], 200

        elif resp.status_code == 403:
            logger.debug(
                "视频进度上报返回403, jobid={}, 摘要={}",
                _job.get("jobid"),
                resp.text[:200],
            )

            # 若出现两个rt参数都返回403的情况, 则跳过当前任务
            logger.error("出现403报错, 尝试修复无效, 正在跳过当前任务点...")
            logger.error("请求url: {}", resp.url)
            logger.error("请求头: {}", dict(_session.headers) | headers)
            return False, 403

        logger.error(f"未知错误: {resp.status_code}")
        logger.error("请求url:", resp.url)
        logger.error("请求头：", dict(_session.headers) | headers)
        return False, resp.status_code

    def _refresh_video_status(self, session: requests.Session, job: dict, _type: Literal["Video", "Audio"]) \
            -> Optional[dict]:
        self.rate_limiter.limit_rate(random_time=True, random_max=0.2)
        headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS
        info_url = (
            f"https://mooc1.chaoxing.com/ananas/status/{job['objectid']}?"
            f"k={self.get_fid()}&flag=normal"
        )
        try:
            resp = session.get(info_url, timeout=8, headers=headers)
        except RequestException as exc:
            logger.debug("刷新视频状态失败: {}", exc)
            return None

        if resp.status_code != 200:
            logger.debug("刷新视频状态返回码异常: {}" % resp.status_code)
            logger.debug(resp.text)
            return None

        try:
            data = resp.json()
        except ValueError as exc:
            logger.debug("解析视频状态响应失败: {}", exc)
            return None

        if data.get("status") == "success":
            return data

        return None

    def _recover_after_forbidden(self, session: requests.Session, job: dict, _type: Literal["Video", "Audio"]):
        SessionManager.update_cookies()
        refreshed = self._refresh_video_status(session, job, _type)
        if refreshed:
            return refreshed

        if SessionManager.relogin_if_needed(self):
            return self._refresh_video_status(session, job, _type)

        return None

    @staticmethod
    def _close_pbar_safe(pbar_ref):
        if pbar_ref is not None:
            try:
                pbar_ref.leave = False
                pbar_ref.close()
            except Exception as e:
                logger.trace(f"关闭进度条失败: {e}")
        return None

    def study_video(self, _course, _job, _job_info, _speed: float = 1.0,
                    _type: Literal["Video", "Audio"] = "Video") -> StudyResult:
        _session = self._session

        headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS
        _info_url = f"https://mooc1.chaoxing.com/ananas/status/{_job['objectid']}?k={self.get_fid()}&flag=normal"
        _video_info = _session.get(_info_url, headers=headers).json()

        if _video_info["status"] != "success":
            logger.error(f"Unknown status: {_video_info['status']}")
            return StudyResult.ERROR

        _dtoken = _video_info["dtoken"]

        _crc = _video_info["crc"]
        _key = _video_info["key"]

        # Time in the real world: last_iter, gc.THRESHOLD
        # Time in the video (can be scaled with the speed factor): duration, play_time, last_log_time, wait_time

        duration = int(_video_info["duration"])
        play_time = int(_job["playTime"]) // 1000
        last_log_time = 0
        last_iter = time.time()
        wait_time = int(random.uniform(30, 90))

        # 视频学习模式（config.ini [common] video_mode）：
        #   fast  - 「秒过」：一次把 playingTime 报到 duration。任务点立刻变绿，
        #           但平台是按「两次上报的真实间隔 vs playingTime 增量」判定有效学习时长的，
        #           0 秒跳完 N 秒的视频只会记到 ~0 秒时长（isdrag=4 还会被标成拖拽）。
        #   timed - 按真实节奏逐段上报（playingTime 从当前位置递增到 duration，
        #           isdrag=3 正常播放），任务点和学习时长一起拿；
        #           代价是耗时 ≈ 剩余时长 ÷ 倍速，倍速越高平台记到的时长占比越低。
        video_mode = str(getattr(self, "video_mode", "") or gc.VIDEO_MODE or "fast").lower()

        logger.info(f"开始任务: {_job['name']}, 总时长: {duration}s, 已进行: {play_time}s")

        forbidden_retry = 0
        max_forbidden_retry = 2

        passed, state = False, 0

        # 重播感知（v1.5.1d）：从头重刷时 process_job 把已通过视频的 playTime 归零，
        # 但服务端对「任务点已通过」的每一次心跳都返回 isPassed=True —— 若照单全收，
        # 第一次心跳（30~90 秒）循环就退出，日志显示「任务完成」，实际视频只"看"了
        # 一分钟不到，学习时长也补不到。重播场景必须忽略服务端的 isPassed，
        # 播满 duration（完整走一遍 playingTime 递增上报）才算完成。
        force_replay = (bool(_job.get("ispassed"))
                        and video_mode == "timed"
                        and play_time == 0)
        last_notify = 0

        # ---- GUI 播放进度帧（v1.5.1e 引入，v1.5.4 改为「锚点」式）----------
        # 每次 study_video 调用 = 一个「播放会话」。重播 / 换视频会开新会话，
        # 界面据此才把进度条归零；同一个会话内进度只增不减，见
        # gui_app/page_run.py 的 AccountProgressCard.set_video_progress。
        session = time.monotonic_ns()
        _frame_seq = 0

        def _emit_video_frame(_pt, _state="play"):
            """把当前播放位置推给界面。CLI 下 video_progress_cb 为 None，零开销。"""
            nonlocal _frame_seq
            _cb = getattr(self, "video_progress_cb", None)
            if _cb is None:
                return
            _frame_seq += 1
            try:
                _cb({"job": str(_job.get("name", "") or ""),
                     "course": str(_course.get("title", "") or ""),
                     # 浮点秒：整秒取整会让界面上的进度条按「格」跳
                     "play_time": float(_pt),
                     "duration": float(duration),
                     "speed": float(_speed),
                     "seq": _frame_seq,
                     "session": session,
                     "state": _state})
            except Exception:
                pass
        if force_replay:
            logger.info("重播模式: {}（任务点已通过，从头播放补时长，以播放结束为准）",
                        _job['name'])

        if video_mode == "timed":
            if duration and play_time >= duration:
                # 任务点已经完成，再按原进度上报 100% 不会再增加任何时长，
                # 必须从头走一遍心跳，平台才会把这段重新计进学习时长。
                logger.info(
                    "任务点已完成，计时长模式从头重播以累计学习时长: {}", _job['name'])
                play_time = 0
            logger.info(
                "计时长模式：按真实节奏上报（倍速 {}），预计还需 {:.0f} 分钟",
                _speed, max(0.0, (duration - play_time) / max(_speed, 0.01)) / 60,
            )
        else:
            passed, state = self.video_progress_log(_session, _course, _job, _job_info, _dtoken, duration, duration,
                                                    _type, headers=headers, _isdrag=4)
            if passed:
                logger.info("任务瞬间完成: {}", _job['name'])
                return StudyResult.SUCCESS

        # 首帧：一开播就把「从第几秒开始播」推给界面，不用等第一轮循环
        # （否则进度条会空等一秒才出现，看上去像卡了一下）
        _emit_video_frame(play_time)

        pbar = None
        try:
            while not passed:
                # 1) 先把本地播放时间推进到「现在」，再决定要不要上报。
                #    上报请求会阻塞几百毫秒到几秒，而这段时间视频其实还在播
                #    （上游注释同义）。把推进挪到请求之前有两个好处：
                #      · 请求进行中界面不会冻住 —— 这一帧在阻塞之前已经发出；
                #      · 阻塞耗时照样计入本次 dt，累计秒数与真实节奏保持一致。
                dt = (time.time() - last_iter) * _speed
                last_iter = time.time()
                play_time = min(duration, play_time + dt)
                _emit_video_frame(play_time)

                # 2) 到点就把进度报到服务端
                # Sometimes the last request needs to be sent several times to complete the task
                if play_time - last_log_time >= wait_time or play_time == duration:

                    passed, state = self.video_progress_log(_session, _course, _job, _job_info, _dtoken, duration,
                                                            int(play_time), _type, headers=headers)

                    if state == 403:
                        if forbidden_retry >= max_forbidden_retry:
                            logger.warning("403重试失败, 跳过当前任务")
                            return StudyResult.FORBIDDEN
                        forbidden_retry += 1
                        logger.warning(
                            "出现403报错, 正在尝试刷新会话状态 (第{}次)",
                            forbidden_retry,
                        )
                        time.sleep(random.uniform(2, 4))
                        refreshed_meta = self._recover_after_forbidden(_session, _job, _type)
                        if refreshed_meta and refreshed_meta.get("dtoken") and refreshed_meta.get(
                                "duration") is not None:
                            _dtoken = refreshed_meta["dtoken"]
                            duration = int(refreshed_meta["duration"])
                            refreshed_play_time = refreshed_meta.get("playTime")
                            if refreshed_play_time is not None:
                                play_time = int(refreshed_play_time)

                            logger.debug("刷新后的令牌: {}, 持续时间: {}, 播放时间: {}", _dtoken, duration, play_time)
                            pbar = self._close_pbar_safe(pbar)
                            continue
                        else:
                            logger.error("会话恢复失败，刷新后的元数据缺少必要字段 (dtoken, duration)")
                            return StudyResult.ERROR

                    elif not passed and state != 200:
                        return StudyResult.ERROR

                    if force_replay and play_time < duration:
                        # 服务端对已通过任务点恒返回 isPassed=True，忽略之，
                        # 播满 duration 才退出（见上方 force_replay 注释）
                        passed = False

                    # 播放期间的低频进度日志：此前心跳不打日志，一段 30+ 分钟的
                    # 视频播放期间日志窗口一行输出都没有，看起来像卡死。
                    if play_time - last_notify >= 300:
                        last_notify = play_time
                        logger.info("播放中: {} - {}s / {}s{}",
                                    _job['name'], int(play_time), duration,
                                    "（重播补时长）" if force_replay else "")

                    wait_time = int(random.uniform(30, 90))
                    last_log_time = play_time

                    logger.trace("Progress logged")

                # 检查手动模式锁是否被锁定
                manual_locked = False
                try:
                    manual_locked = TikuManual._manual_lock.locked()
                except Exception as e:
                    logger.trace(f"无法检查手动锁状态: {e}")

                if manual_locked or getattr(self, "gui_mode", False):
                    # GUI 下 stderr 是 StreamProxy，tqdm 的 \r 帧高频写入会不断
                    # 重置日志/进度通道判定（engine 已设 gui_mode=True），进度看
                    # 日志窗口的「播放中」即可；CLI 保持原进度条。
                    pbar = self._close_pbar_safe(pbar)
                else:
                    if pbar is None:
                        pbar = tqdm(total=duration, initial=int(play_time), desc=_job["name"],
                                    unit_scale=True, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}', leave=False)
                    pbar.n = int(play_time)
                    pbar.refresh()

                time.sleep(gc.THRESHOLD)
        finally:
            pbar = self._close_pbar_safe(pbar)

        logger.info("任务完成: {}", _job['name'])
        return StudyResult.SUCCESS

    def study_document(self, _course, _job) -> StudyResult:
        """
        Study a document in Chaoxing platform.

        This method makes a GET request to fetch document information for a given course and job.

        Args:
            _course (dict): Dictionary containing course information with keys:
                - courseId: ID of the course
                - clazzId: ID of the class
            _job (dict): Dictionary containing job information with keys:
                - jobid: ID of the job
                - otherinfo: String containing node information
                - jtoken: Authentication token for the job

        Returns:
            requests.Response: Response object from the GET request

        Note:
            This method requires the following helper functions:
            - init_session(): To initialize a new session
            - get_timestamp(): To get current timestamp
            - re module for regular expression matching
        """
        _session = self._session
        _url = f"https://mooc1.chaoxing.com/ananas/job/document?jobid={_job['jobid']}&knowledgeid={re.findall(r'nodeId_(.*?)-', _job['otherinfo'])[0]}&courseid={_course['courseId']}&clazzid={_course['clazzId']}&jtoken={_job['jtoken']}&_dc={get_timestamp()}"
        _resp = _session.get(_url)
        if _resp.status_code != 200:
            return StudyResult.ERROR
        else:
            return StudyResult.SUCCESS

    def study_work(self, _course, _job, _job_info) -> StudyResult:
        if not self.tiku or self.tiku.DISABLE:
            logger.warning('章节检测未作答：题库不可用，不能标记完成')
            return StudyResult.DEFERRED

        _session = self._session
        _url = "https://mooc1.chaoxing.com/mooc-ans/api/work"

        def is_not_permission_error(exception):
            return not isinstance(exception, PermissionError)

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_fixed(1),
            retry=retry_if_exception(is_not_permission_error),
            reraise=True
        )
        def fetch_response_with_retry():
            _resp = _session.get(
                _url,
                params={
                    "api": "1",
                    "workId": _job["jobid"].replace("work-", ""),
                    "jobid": _job["jobid"],
                    "originJobId": _job["jobid"],
                    "needRedirect": "true",
                    "skipHeader": "true",
                    "knowledgeid": str(_job_info["knowledgeid"]),
                    "ktoken": _job_info["ktoken"],
                    "cpi": _job_info["cpi"],
                    "ut": "s",
                    "clazzId": _course["clazzId"],
                    "type": "",
                    "enc": _job["enc"],
                    "mooc2": "1",
                    "courseid": _course["courseId"],
                }
            )

            # 未创建完成该测验则不进行答题，目前遇到的情况是未创建完成等同于没题目
            if '教师未创建完成该测验' in _resp.text:
                raise PermissionError("教师未创建完成该测验")

            questions = decode_questions_info(_resp.text)

            if _resp.status_code == 200 and questions.get("questions"):
                return _resp, questions

            logger.warning(
                f"无效响应 (Code: {getattr(_resp, 'status_code', 'Unknown')}), 重试中...")
            raise RuntimeError(f"请求返回无效数据 (Code: {_resp.status_code})")

        # 章节检测最大重做次数（答错后收集错误反馈并重新提交，直到全对）
        # work_redo_enabled=false 时为单轮模式: 提交+检查+报告成绩, 不自动重做(适用于只允许作答一次的课程)
        if bool(self.kwargs.get("work_redo_enabled", False)):
            try:
                max_retries = max(1, int(self.kwargs.get("work_max_retries", 3)))
            except (TypeError, ValueError):
                max_retries = 3
        else:
            max_retries = 0
        query_delay = self.kwargs.get("query_delay", 0)
        feedback_history = None
        last_submitted_score = None

        for attempt in range(max_retries + 1):
            if attempt > 0:
                logger.warning(
                    f"章节检测重做第 {attempt}/{max_retries} 轮，携带上一轮错误反馈重新作答")
                time.sleep(2)

            # 1. 获取题目
            final_resp = {}
            questions = {}
            try:
                final_resp, questions = fetch_response_with_retry()
            except PermissionError as e:
                logger.warning(f"跳过章节检测: {e}")
                return StudyResult.SUCCESS
            except Exception as e:
                if attempt > 0:
                    logger.error(f"重做轮无法获取题目(上一轮提交成绩 {last_submitted_score} 分), "
                                 f"该课程可能仅允许作答一次: {e}")
                else:
                    logger.error(f"获取章节检测题目失败, 达到最大重试次数: {e}")
                return StudyResult.ERROR

            _ORIGIN_HTML_CONTENT = final_resp.text  # 用于配合输出网页源码, 帮助修复#391错误

            # 2. 设置上一轮错误反馈（供AI重新作答时参考）
            if hasattr(self.tiku, 'set_work_feedback'):
                try:
                    self.tiku.set_work_feedback(feedback_history or None)
                    if feedback_history:
                        logger.debug("已将上一轮错误反馈设置到题库")
                except Exception as e:
                    logger.warning(f"设置题库错误反馈失败: {e}")

            # 3. 搜题
            total_questions = len(questions["questions"])
            found_answers = 0
            answers = self.tiku.query_all(questions["questions"], query_delay=query_delay)

            if not isinstance(answers, list):
                logger.error("题库 query_all 返回的数据格式异常，缺失答案不作答")
                answers = [None] * total_questions
            elif len(answers) != total_questions:
                logger.error(
                    f"题库返回的答案数量（{len(answers)}）与题目数量（{total_questions}）不匹配，整批结果已丢弃，避免错位作答！")
                answers = [None] * total_questions

            from api.work_answer_policy import map_answer
            model_provider = next((p for p in getattr(self.tiku, 'providers', [self.tiku])
                                   if p.__class__.__name__ == 'AI' and not p.DISABLE), None)
            for q, res in zip(questions["questions"], answers):
                answer = map_answer(q, res, self.tiku)
                # A bank answer that does not match the actual options is a miss.
                # Bypass its cached value when asking the model for a replacement.
                if not answer and model_provider is not None and q.get('type') != 'unknown':
                    try:
                        replacement = model_provider._query(q)
                        answer = map_answer(q, replacement, self.tiku)
                    except Exception as exc:
                        logger.warning(f"模型补答失败，该题不作答：{type(exc).__name__}")
                if answer:
                    found_answers += 1
                    q[f'answerSource{q["id"]}'] = "cover"
                else:
                    q[f'answerSource{q["id"]}'] = "missing"
                    logger.warning(f"该题无有效答案，不猜题：{q['title']}")
                q["answerField"][f'answer{q["id"]}'] = answer
            cover_rate = (found_answers / total_questions) * 100
            logger.info(f"章节检测题库覆盖率： {cover_rate:.0f}%")
            # 提交模式  现在与题库绑定,留空直接提交, 1保存但不提交
            is_manual_mode = (
                    getattr(self.tiku, 'is_manual', False) or
                    self.tiku.__class__.__name__ == 'TikuManual' or
                    (self.tiku.__class__.__name__ == 'TikuFallback' and any(
                        getattr(p, 'is_manual', False) or p.__class__.__name__ == 'TikuManual' for p in
                        getattr(self.tiku, 'providers', [])))
            )
            if self.tiku.get_submit_params() == "1":
                questions["pyFlag"] = "1"
                logger.warning('自动提交已关闭：本次仅保存草稿，不代表已提交')
            elif found_answers == total_questions and total_questions > 0:
                questions["pyFlag"] = ""
            else:
                questions["pyFlag"] = "1"
                if self.rollback_times >= 1:
                    logger.info(
                        f"重做轮题库/AI 仍无一题可答（覆盖率 {cover_rate:.0f}%），"
                        f"仅保存不提交 —— 缺失答案不会猜测或提交")
                else:
                    logger.info(f"章节检测仍有未作答题目，不予提交")
            # 组建提交表单
            if questions["pyFlag"] == "1":
                for q in questions["questions"]:
                    questions.update(
                        {
                            f'answer{q["id"]}':
                                q["answerField"][f'answer{q["id"]}'] if q[f'answerSource{q["id"]}'] == "cover" else '',
                            f'answertype{q["id"]}': q["answerField"][f'answertype{q["id"]}'],
                        }
                    )
            else:
                for q in questions["questions"]:
                    questions.update(
                        {
                            f'answer{q["id"]}': q["answerField"][f'answer{q["id"]}'],
                            f'answertype{q["id"]}': q["answerField"][f'answertype{q["id"]}'],
                        }
                    )

            del questions["questions"]

            # 4. 提交
            try:
                res = _session.post(
                    "https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNew",
                    data=questions,
                    headers={
                        "Host": "mooc1.chaoxing.com",
                        "sec-ch-ua-platform": '"Windows"',
                        "X-Requested-With": "XMLHttpRequest",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "sec-ch-ua": '"Microsoft Edge";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "sec-ch-ua-mobile": "?0",
                        "Origin": "https://mooc1.chaoxing.com",
                        "Sec-Fetch-Site": "same-origin",
                        "Sec-Fetch-Mode": "cors",
                        "Sec-Fetch-Dest": "empty",
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5",
                    },
                )
            except requests.RequestException:
                logger.warning('提交/保存请求中断，平台是否接收未确认；请核对记录，不自动重发')
                return StudyResult.DEFERRED
            from api.work_answer_policy import classify_work_receipt
            receipt = classify_work_receipt(res)
            action = '提交' if questions['pyFlag'] == '' else '保存'
            if receipt == 'rejected':
                logger.error(f'{action}被平台拒绝，不能标记完成')
                return StudyResult.ERROR
            if receipt != 'accepted':
                logger.warning(f'{action}回执未确认，请先核对平台记录，避免重复提交')
                return StudyResult.DEFERRED
            logger.info(f'{action}回执已确认')

            # 5. 若只是保存未提交，无法判断成绩，保持原有行为
            if questions["pyFlag"] == "1":
                return StudyResult.DEFERRED

            # 6. 提交后检查成绩：若未全部正确，则收集错误反馈并重新作答提交
            try:
                result_info = self._check_work_result(_session, _course, _job, _job_info, questions)
            except Exception:
                logger.warning('已收到提交回执，但作答记录读取异常，请先核对，不重复提交')
                return StudyResult.DEFERRED
            if result_info is None:
                # 提交回执不能替代批改结果，保留待核对状态。
                logger.warning('已收到提交回执，但尚未确认作答记录或成绩')
                return StudyResult.DEFERRED

            score = float(result_info.get("score", 0.0) or 0.0)
            unknown = int(result_info.get("unknown", 0) or 0)
            wrong = list(result_info.get("wrong") or [])
            if result_info.get("all_correct", False):
                logger.info(f"章节检测全部正确（成绩 {score} 分），通过！")
                return StudyResult.SUCCESS
            if score >= 99.95:
                # 分数已满分：即使逐题比对有格式差异 / 平台没给正确答案，也以分数为准
                if unknown or wrong:
                    logger.info(f"章节检测成绩 {score} 分（满分），以分数为准通过"
                                f"（逐题比对不完整：{unknown} 题平台未给正确答案，"
                                f"{len(wrong)} 题标记为错）")
                else:
                    logger.info(f"逐题比对存在格式差异，但成绩 {score} 分达到满分，以分数为准，通过！")
                return StudyResult.SUCCESS
            if wrong and not result_info.get("feedback"):
                # 明确判错，但平台未提供正确答案 → 拿不到可用于重做的反馈。
                # 此时重做只会把同一批答案再抄一遍，必然还是错，直接保留成绩结束。
                shown = "；".join(wrong[:5]) + ("…" if len(wrong) > 5 else "")
                logger.warning(f"章节检测有 {len(wrong)} 题答错（成绩 {score} 分）：{shown}")
                logger.warning("该课程未开放正确答案展示，无法自动纠正；"
                               "已保留本次成绩，建议人工核对这几道题")
                return StudyResult.DEFERRED
            if unknown and not result_info.get("feedback"):
                # 既无正确答案也无对错标记：不可信，保留已提交成绩，不触发重做
                logger.info(f"章节检测成绩 {score} 分：{unknown} 题无法判定对错"
                            f"（平台未返回批改信息），已保留本次成绩")
                return StudyResult.DEFERRED

            # 7. 未全对：收集错误反馈，进入下一轮重做
            feedback_history = result_info.get("feedback", [])
            wrong_count = len(feedback_history)
            if attempt >= max_retries:
                # 已是最后一轮(含单轮模式), 保留已提交成绩, 不再触发无意义的重做
                logger.warning(f"章节检测有 {wrong_count}/{total_questions} 题回答错误（成绩 {score} 分），"
                               f"已保留本次成绩, 请人工检查处理")
                return StudyResult.DEFERRED
            logger.warning(
                f"章节检测有 {wrong_count}/{total_questions} 题回答错误（成绩 {score} 分），"
                f"已将错误反馈给AI，准备重新作答提交 (第 {attempt + 1}/{max_retries + 1} 轮)"
            )
            self.rollback_times += 1
            last_submitted_score = score

        # 达到最大重试次数仍未全对
        logger.error(f"章节检测重试 {max_retries + 1} 次仍未全部正确，请人工检查处理")
        return StudyResult.ERROR

    def _check_work_result(self, _session, _course, _job, _job_info, questions) -> Optional[dict]:
        """
        章节检测提交后，查询最新一次作答的成绩与对错详情，供判断是否需要重做。

        Args:
            _session: 当前会话
            _course: 课程信息
            _job: 任务点信息
            questions: 提交时使用的表单数据（含 workId / workAnswerId 等）

        Returns:
            {"all_correct": bool, "feedback": list[str], "score": float, "times": int}
            或 None（无法获取成绩详情时返回 None）
        """
        work_id = str(
            questions.get("workId", "")
            or questions.get("workRelationId", "")
            or _job["jobid"].replace("work-", "")
        )
        work_answer_id = str(questions.get("workAnswerId", "") or "")
        course_id = str(_course.get("courseId", ""))
        class_id = str(_course.get("clazzId", ""))
        cpi = str(_course.get("cpi", "") or questions.get("cpi", ""))

        # 1. 获取作答记录列表（提交后服务端异步生成记录，需稍作等待并多次重试）
        records = None
        for attempt in range(5):
            try:
                resp = _session.get(
                    "https://mooc1.chaoxing.com/mooc-ans/work/record-list",
                    params={
                        "courseId": course_id,
                        "classId": class_id,
                        "workId": work_id,
                        "workAnswerId": work_answer_id,
                        "cpi": cpi,
                        "api": "1",
                        "mooc2": "1",
                        "ut": "s",
                    },
                    timeout=20,
                )
                records = _parse_work_record_list(resp.text)
                if records:
                    break
            except Exception as e:
                logger.warning(f"获取章节检测作答记录失败 (第{attempt + 1}次): {e}")
            if attempt < 4:
                time.sleep(1.5)

        if not records:
            # 兜底：重新访问题目页判断是否已通过（详情页=已提交有成绩；可编辑页=未通过可重做）
            logger.warning("无法获取章节检测作答记录，尝试通过题目页状态判断")
            try:
                resp = _session.get(
                    "https://mooc1.chaoxing.com/mooc-ans/api/work",
                    params={
                        "api": "1",
                        "workId": _job["jobid"].replace("work-", ""),
                        "jobid": _job["jobid"],
                        "originJobId": _job["jobid"],
                        "needRedirect": "true",
                        "skipHeader": "true",
                        "knowledgeid": str(_job_info.get("knowledgeid", "") or _job.get("knowledgeid", "")),
                        "ktoken": str(_job_info.get("ktoken", "") or _job.get("ktoken", "")),
                        "cpi": str(_job_info.get("cpi", "") or _job.get("cpi", "") or cpi),
                        "ut": "s",
                        "clazzId": class_id,
                        "type": "",
                        "enc": str(_job.get("enc", "")),
                        "mooc2": "1",
                        "courseid": course_id,
                    },
                    timeout=20,
                )
                html = resp.text
                if 'answerwqbid' in html:
                    # 可编辑页面：说明未全部正确，可重新作答（该判定为页面状态推断, 未核对实际成绩）
                    logger.warning("题目页仍可编辑, 推断章节检测未全部正确(未核对成绩)")
                    return None
                elif '我的答案' in html:
                    # 已提交详情页：解析成绩与对错。
                    # 注意这里**不能**用 '正确答案' in html 当门槛 —— 关闭了答案
                    # 展示的课程会在内联 JS 里出现「正确答案」字样（构建文本用），
                    # 但页面上并没有真的正确答案字段，用它当门槛会把这类页面
                    # 误当成「可逐题比对」，结果永远解析不出答案。
                    detail = _parse_work_record_detail(html)
                    if detail:
                        harvest_correct_answers(detail)
                        verdict = judge_work_detail(detail)
                        m = re.search(r'本次成绩<i>([\d.]+)</i>分', html)
                        score = float(m.group(1)) if m else 0.0
                        return {
                            "all_correct": verdict["all_correct"],
                            "feedback": verdict["feedback"],
                            "score": score,
                            "times": 0,
                            "unknown": verdict["unknown"],
                            "wrong": verdict["wrong"],
                        }
                return None
            except Exception as e:
                logger.warning(f"兜底判断章节检测状态失败: {e}")
                return None

        latest_times = max(r[0] for r in records)
        latest_score = dict(records).get(latest_times, 0.0)

        # 2. 获取最新一次作答详情（含每道题对错与正确答案）
        try:
            resp = _session.get(
                "https://mooc1.chaoxing.com/mooc-ans/work/record-detail",
                params={
                    "courseId": course_id,
                    "classId": class_id,
                    "workId": work_id,
                    "workAnswerId": work_answer_id,
                    "times": str(latest_times),
                    "cpi": cpi,
                    "ut": "s",
                    "isdisplaytable": "0",
                    "firstHeader": "2",
                    "isWork": "false",
                    "workSystem": "0",
                    "api": "1",
                    "archive": "false",
                    "mooc2": "1",
                },
                timeout=20,
            )
            detail = _parse_work_record_detail(resp.text)
            if detail:
                harvest_correct_answers(detail)
        except Exception as e:
            logger.warning(f"获取章节检测作答详情失败: {e}")
            return None

        if not detail:
            logger.warning("章节检测作答详情解析为空，跳过成绩检查")
            return None

        # 3. 逐题判断对错：正确答案优先，页面没给就用平台自己的对错标记
        verdict = judge_work_detail(detail)
        logger.debug(f"章节检测成绩: {latest_score} 分, 全部正确: {verdict['all_correct']}, "
                     f"错题数: {len(verdict['wrong'])}, 未解析题数: {verdict['unknown']}")
        return {
            "all_correct": verdict["all_correct"],
            "feedback": verdict["feedback"],
            "score": latest_score,
            "times": latest_times,
            "unknown": verdict["unknown"],
            "wrong": verdict["wrong"],
        }

    def study_read(self, _course, _job, _job_info) -> StudyResult:
        """
        阅读任务学习, 仅完成任务点, 并不增长时长
        """
        _session = self._session
        _resp = _session.get(
            url="https://mooc1.chaoxing.com/ananas/job/readv2",
            params={
                "jobid": _job["jobid"],
                "knowledgeid": _job_info["knowledgeid"],
                "jtoken": _job["jtoken"],
                "courseid": _course["courseId"],
                "clazzid": _course["clazzId"],
            },
        )
        if _resp.status_code != 200:
            logger.error(f"阅读任务学习失败 -> [{_resp.status_code}]{_resp.text}")
            return StudyResult.ERROR
        else:
            _resp_json = _resp.json()
            logger.info(f"阅读任务学习 -> {_resp_json['msg']}")
            return StudyResult.SUCCESS

    def _send_monitor_heartbeat(self, course, point):
        """
        发送章节监控心跳包到 detect.chaoxing.com。

        模拟真实浏览器的 JSONP 打点请求，佐证访问行为的真人属性。

        Args:
            course: 课程信息字典
            point: 当前章节信息字典
        """
        version = get_timestamp()
        callback = f"jsonp{secrets.randbelow(10**21 - 10**20) + 10**20}"
        params = {
            "version": version,
            "refer": "http://i.mooc.chaoxing.com",
            "from": "",
            "fid": self.get_fid(),
            "jsoncallback": callback,
            "t": get_timestamp(),
        }
        referer_url = (
            f"https://mooc1.chaoxing.com/mycourse/studentstudy?"
            f"chapterId={point['id']}&courseId={course['courseId']}"
            f"&clazzid={course['clazzId']}&cpi={course['cpi']}&mooc2=1"
        )
        try:
            session = self._session
            resp = session.get(
                "https://detect.chaoxing.com/api/monitor",
                params=params,
                headers={"Referer": referer_url},
                timeout=5,
            )
            logger.trace(f"Monitor heartbeat sent -> {resp.status_code}")
        except Exception as e:
            logger.trace(f"Monitor heartbeat failed (non-critical): {e}")

    def study_emptypage(self, _course, point):
        _session = self._session
        # &cpi=0&verificationcode=&mooc2=1&microTopicId=0&editorPreview=0
        _resp = _session.get(
            url="https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax",
            params={
                "courseId": _course["courseId"],
                "clazzid": _course["clazzId"],
                "chapterId": point["id"],
                "cpi": _course["cpi"],
                "verificationcode": "",
                "mooc2": 1,
                "microTopicId": 0,
                "editorPreview": 0,
            },
            timeout=8,
        )
        if _resp.status_code != 200:
            logger.error(f"空页面任务失败 -> [{_resp.status_code}]{point['title']}")
            return StudyResult.ERROR
        else:
            logger.info(f"空页面任务完成 -> {point['title']}")
            return StudyResult.SUCCESS

    def _access_chapter_for_count(self, _course, point):
        _session = self._session
        # &cpi=0&verificationcode=&mooc2=1&microTopicId=0&editorPreview=0
        _resp = _session.get(
            url="https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax",
            params={
                "courseId": _course["courseId"],
                "clazzid": _course["clazzId"],
                "chapterId": point["id"],
                "cpi": _course["cpi"],
                "verificationcode": "",
                "mooc2": 1,
                "microTopicId": 0,
                "editorPreview": 0,
            },
            timeout=8,
        )
        if _resp.status_code != 200:
            logger.error(f"章节访问失败 -> [{_resp.status_code}]{point['title']}")
            return None
        else:
            logger.info(f"章节访问成功 -> {point['title']}")
            return _resp.text

    def _extract_and_send_setlog(self, html_text):
        """
        从 studentstudyAjax 返回的 HTML 中提取 setlog URL 并执行。

        该 URL 包含服务端生成的 encode 参数，是记录章节学习次数的关键 API。

        Args:
            html_text: studentstudyAjax 返回的 HTML 内容
        """
        match = re.search(
            r'<script[^>]+src="(https://fystat-ans\.chaoxing\.com/log/setlog[^"]+)"',
            html_text
        )
        if not match:
            logger.trace("未在响应中找到 setlog URL")
            return

        setlog_url = match.group(1)
        try:
            session = self._session
            resp = session.get(setlog_url, timeout=5)
            logger.trace(f"Setlog sent -> {resp.status_code}")
        except Exception as e:
            logger.trace(f"Setlog failed (non-critical): {e}")

    def increase_chapter_learning_count(self, course, points, target_count):
        """
        增加课程章节学习次数。

        循环遍历课程的所有章节，每访问一个章节页面：
        1. 调 studentstudyAjax 获取页面 HTML（含服务端生成的 setlog URL）
        2. 提取并执行 setlog URL（记录学习次数）
        3. 立即发送 monitor 心跳包（模拟 fn() 首次心跳）
        4. 停留 30 秒（模拟前端 setInterval(fn, 30000) 的间隔）
        5. 再次发送 monitor 心跳包（模拟 30s 后的第二次心跳）
        6. 计数器 +1，继续下一个章节

        Args:
            course: 课程信息字典
            points: 课程所有章节列表
            target_count: 目标总次数

        Returns:
            StudyResult: 操作结果
        """
        total = 0
        consecutive_failures = 0
        max_consecutive_failures = 10
        logger.info(f"开始增加章节学习次数, 目标总次数: {target_count}, 章节数: {len(points)}")
        if not points:
            logger.warning("章节列表为空, 跳过章节学习次数增加")
            return StudyResult.SUCCESS
        while total < target_count:
            for point in points:
                if total >= target_count:
                    break
                self.rate_limiter.limit_rate(random_time=True, random_min=0, random_max=0.2)
                html_text = self._access_chapter_for_count(course, point)
                if not html_text:
                    logger.error(f"章节学习次数增加失败, 当前章节: {point['title']}")
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_failures:
                        logger.error(
                            f"章节学习次数增加连续失败 {consecutive_failures} 次, 终止任务"
                        )
                        return StudyResult.ERROR
                    continue

                consecutive_failures = 0

                # 第 1 步：从 HTML 中提取 setlog URL 并执行（真正的计次 API）
                self._extract_and_send_setlog(html_text)

                # 第 2 步：立即发送 monitor 心跳包（模拟 fn()）
                self._send_monitor_heartbeat(course, point)

                # 第 3 步：停留 30 秒（模拟前端 setInterval 间隔）
                time.sleep(30)

                # 第 4 步：再次发送 monitor 心跳包（模拟 setInterval 触发的第二次心跳）
                self._send_monitor_heartbeat(course, point)

                total += 1
                logger.info(f"章节学习次数进度: {total}/{target_count}")
        logger.info(f"章节学习次数增加完成, 共完成: {total} 次")
        return StudyResult.SUCCESS
