import configparser
import json
import os
import random
import re
import shutil
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from re import sub
from typing import Optional

import httpx
import requests
from openai import OpenAI
from api.token_usage import tracked_completion, record_response, account_context
from urllib3 import disable_warnings, exceptions

from api._net import send as _net_send
from api.answer_check import check_answer
from api.logger import logger

# 关闭警告
disable_warnings(exceptions.InsecureRequestWarning)

__all__ = ["CacheDAO", "Tiku", "TikuFallback", "TikuYanxi", "TikuGo", "TikuLike", "TikuAdapter", "AI", "SiliconFlow",
           "TikuManual"]


#: 本地知识库：统一放 exe 旁的「知识库」文件夹（v1.5.1f）
KNOWLEDGE_DIR_NAME = "知识库"


def knowledge_dir() -> Path:
    return Path(KNOWLEDGE_DIR_NAME)


_cache_dao_singleton = None
_cache_dao_singleton_lock = threading.Lock()


def get_cache_dao():
    """进程内共享云端题库入口，答案由服务器统一持久化。"""
    global _cache_dao_singleton
    if _cache_dao_singleton is None:
        with _cache_dao_singleton_lock:
            if _cache_dao_singleton is None:
                _cache_dao_singleton = CloudCacheDAO()
    return _cache_dao_singleton


def knowledge_size() -> int:
    """云端题库条目数；显式传入旧文件后端的测试仍可读取本地条数。"""
    try:
        dao = get_cache_dao()
        return dao.size() if hasattr(dao, 'size') else len(dao._read_cache())
    except Exception:
        return 0


#: record-detail 的中文题型 -> 题库校验用的类型名
_WORK_TYPE_LABEL_MAP = {
    "单选": "single",
    "多选": "multiple",
    "填空": "completion",
    "判断": "judgement",
}


class _HarvestCheckTiku:
    """check_answer 校验用的哑题库（判断题语义表；Tiku 是抽象类）。"""

    true_list = ['true', '正确']
    false_list = ['false', '错误']
    is_manual = False


#: 题目标题开头的题型标签，如「【单选题】」「【多选题】」「【其他类型题目】」
_TYPE_TAG_RE = re.compile(r'^【[^】]{1,12}】\s*')


def cache_key(title: str) -> str:
    """题目归一化 —— 全流程唯一口径（CacheDAO 读写、发给题库/AI 的题面都过这里）。

    同一条题目在不同链路里的原始标题长得不一样：
      - 题面解码出来的带题号和题型标签：『1【多选题】题干？ 』
      - 批改页解析出来的只有题干：『题干？』
      - 带上分值后缀：『题干（2.0分）』
    不归一化就会出现「回流写进去的答案永远查不到」+ 成对重复键；
    题面带着『【题型】』标签/题号发给上游题库还会显著拉低命中率
    （实测 TikuAdapter 对『【单选题】Excel中求和的函数是』直接不命中，
    去掉标签才回来 SUM）。

    只剥「明确是题号」的前缀（数字+分隔符，或数字紧贴【题型】标签），
    绝不裸剥开头的数字 —— 否则『3NF 是指什么』会变成『NF 是指什么』、
    『1500米跑的规则』会变成『米跑的规则』（曾用 `^\\d+` 犯过这个错）。
    """
    t = str(title or "").strip()
    t = sub(r'^\d+\s*(?:[.．](?!\d)|[、,，)）])\s*', '', t)
    t = sub(r'^\d+(?=【)', '', t)               # 题号紧贴题型标签：1【单选题】
    t = sub(r'^[.、．,，)）]\s*', '', t)          # 残留分隔符
    t = _TYPE_TAG_RE.sub('', t)                 # 【题型】标签
    t = sub(r'\s*[（(]\d+(?:\.\d+)?\s*分[）)]$', '', t)
    return t.strip()


def _derive_answer_from_mark(q: dict, qtype: str = "") -> str:
    """平台没给正确答案时，用「答对标记 + 我的答案 + 选项表」还原出答案文本。

    只对**平台判定为答对**的题调用 —— 答对意味着「我的答案」就是正确答案，
    这是正确性的保证。字母型答案（A / ACD）先经选项表还原成文本，避免把
    光秃秃的字母沉淀进知识库（下次换课选项顺序一变就答错）。
    qtype 仅用于日志/未来扩展，还原逻辑不依赖它。
    """
    my = (q.get("my_answer") or "").strip()
    if not my:
        return ""
    opts = dict(q.get("options") or [])
    # 纯字母答案（A / AC / ACD / A,C,D）→ 逐字母还原成选项文本。
    # 必须**全部**字母都能在选项表里找到才沉淀：缺一个说明选项没解析全，
    # 宁可放弃这条也不写半截答案进知识库。
    if re.fullmatch(r'[A-Za-z](?:[\s,，、;；/]*[A-Za-z])*', my):
        letters = re.sub(r'[^A-Za-z]', '', my).upper()
        if letters and all(ch in opts for ch in letters):
            return "\n".join(opts[ch] for ch in letters).strip()
        return ""
    # 判断题的「对/错」、填空题的文本答案等：原样沉淀
    return my


def harvest_correct_answers(detail: list) -> int:
    """把平台批改详情里的正确答案沉淀进云端共享题库。

    灵感来自 cxmooc-tools 的共享题库闭环：平台批改出的正确答案是
    唯一可信的答案来源，答错也照收 —— 知识库越刷越全。

    两个来源，优先前者：
    1. 平台明示的正确答案（correct_answer 非空）；
    2. 平台没给正确答案、但把该题判为答对时的「我的答案」
       （答对 ⇒ 我的答案就是正确答案），按选项表还原成文本。

    只收题型可映射的四类（单选/多选/填空/判断）；答案用 check_answer
    校验，防垃圾数据污染知识库；平台答案纠正已有预测。返回沉淀条数，
    任何异常都吞掉 —— 回流失败绝不影响刷课主流程。
    """
    harvested = 0
    dao = None
    dummy = _HarvestCheckTiku()
    for q in detail or []:
        try:
            title = (q.get("title") or "").strip()
            label = (q.get("type_label") or "").strip()
            if not title:
                continue
            qtype = None
            for key, val in _WORK_TYPE_LABEL_MAP.items():
                if key in label:
                    qtype = val
                    break
            if qtype is None:
                continue
            correct = (q.get("correct_answer") or "").strip()
            if correct and qtype in ('single', 'multiple'):
                # 平台明示答案也可能只有选项字母，必须还原正文才可跨选项顺序复用。
                if re.fullmatch(r'[A-Za-z](?:[\s,，、;；/]*[A-Za-z])*', correct):
                    opts = dict(q.get('options') or [])
                    letters = re.sub(r'[^A-Za-z]', '', correct).upper()
                    if all(ch in opts for ch in letters):
                        correct = _derive_answer_from_mark({**q, 'my_answer': correct}, qtype)
                    elif len(letters) == 1 or set(letters) <= set('ABCDEFGH'):
                        continue  # 选项字母缺映射，不能沉淀成可复用答案。
                else:
                    correct = re.sub(r'^[A-Za-z][.．、]\s*', '', correct)
            if not correct:
                # 平台未给正确答案：只认「判为答对」的题，否则不沉淀
                if q.get("is_correct") is not True:
                    continue
                correct = _derive_answer_from_mark(q, qtype)
            if not correct:
                continue
            # 键走统一归一化（见 cache_key）—— 与查询侧同口径，回流才查得到
            key = cache_key(title)
            if not key:
                continue
            if dao is None:
                dao = get_cache_dao()
            if isinstance(dao, CacheDAO) and dao.get_cache(key) == correct:
                continue
            if not check_answer(correct, qtype, dummy):
                continue
            if dao.add_cache(key, correct, verified=True) is not False:
                harvested += 1
        except Exception:
            continue
    if harvested:
        logger.info(f"章节检测批改回流：{harvested} 个正确答案已沉淀进题库")
    return harvested


class CacheDAO:
    """
    本地知识库（答题缓存）：题目标题 -> 答案。

    v1.5.1f 起，所有缓存统一存放在 exe 旁的「知识库」文件夹（不再散在根目录）：
    - 查题先查本地知识库，命中即用（不访问任何远端服务）；
    - 走大模型 / 题库查到的答案会自动沉淀进本地知识库；
    - 多账号共享同一份文件，先答过的题其他账号直接命中，提高正确率、节省 token。

    进程内持有单例 + 内存副本：多账号并发查询不重复读盘；写盘仍是
    「临时文件 + 原子替换」，配合 RLock 保证并发安全。
    """
    DEFAULT_CACHE_FILE = "cache.json"

    def __init__(self, file: Optional[str] = None):
        if file is None:
            d = knowledge_dir()
            try:
                d.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"知识库目录创建失败，退回当前目录: {e}")
                d = Path(".")
            self.cache_file = d / self.DEFAULT_CACHE_FILE
            self._migrate_legacy()
        else:
            self.cache_file = Path(file)
        self._lock = threading.RLock()
        self._mem: Optional[dict] = None      # 进程内副本；None = 尚未加载
        if not self.cache_file.is_file():
            self._write_cache({})

    @staticmethod
    def _migrate_legacy() -> None:
        """旧版 cache.json 在 exe 根目录 —— 首次运行整体搬进知识库文件夹。"""
        legacy = Path("cache.json")
        target = knowledge_dir() / "cache.json"
        try:
            if legacy.is_file() and not target.exists():
                shutil.move(str(legacy), str(target))
                logger.info(f"旧题库缓存已迁移到本地知识库: {target}")
        except Exception as e:
            logger.warning(f"迁移旧题库缓存失败（不影响使用）: {e}")

    def _load_file(self) -> dict:
        # 从磁盘读取（含损坏恢复）；调用方需已持有锁
        if not self.cache_file.is_file():
            return {}
        try:
            with self.cache_file.open("r", encoding="utf8") as fp:
                return json.load(fp)
        except json.JSONDecodeError as e:
            logger.error(f"缓存文件 JSON 解析失败: {e}, 尝试恢复...")
            # 尝试从原始二进制中以 utf-8 忽略错误地恢复有效 JSON 段
            try:
                raw = self.cache_file.read_bytes()
                text = raw.decode("utf-8", errors="ignore")
                start = text.find('{')
                end = text.rfind('}')
                if start != -1 and end != -1 and start < end:
                    try:
                        return json.loads(text[start:end + 1])
                    except Exception:
                        pass
            except Exception:
                pass
            # 若无法恢复，备份损坏文件并返回空缓存
            try:
                bak_name = f"{self.cache_file.name}.bak.{int(time.time())}"
                bak_path = self.cache_file.with_name(bak_name)
                shutil.copy2(self.cache_file, bak_path)
                logger.error(f"缓存文件已损坏，已备份为: {bak_path}，将使用空缓存继续运行")
            except Exception as ex:
                logger.error(f"备份损坏缓存失败: {ex}")
            return {}
        except UnicodeDecodeError as e:
            logger.error(f"缓存文件编码读取失败: {e}, 采用恢复策略...")
            try:
                raw = self.cache_file.read_bytes()
                text = raw.decode("utf-8", errors="ignore")
                start = text.find('{')
                end = text.rfind('}')
                if start != -1 and end != -1 and start < end:
                    try:
                        return json.loads(text[start:end + 1])
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                bak_name = f"{self.cache_file.name}.bak.{int(time.time())}"
                bak_path = self.cache_file.with_name(bak_name)
                shutil.copy2(self.cache_file, bak_path)
                logger.error(f"缓存文件编码错误，已备份为: {bak_path}，将使用空缓存继续运行")
            except Exception as ex:
                logger.error(f"备份损坏缓存失败: {ex}")
            return {}
        except Exception as e:
            logger.error(f"读取缓存异常: {e}")
            return {}

    def _read_cache(self) -> dict:
        # 优先走进程内副本；首次才读盘（读盘后立即把旧键归一化，见 _normalize_keys）
        with self._lock:
            if self._mem is None:
                self._mem = self._normalize_keys(self._load_file())
            return self._mem

    @staticmethod
    def _normalize_keys(data: dict) -> dict:
        """把历史条目的键统一到 cache_key 口径（顺带合并重复键）。

        早期版本写入的键有的带【题型】标签、有的带尾随空白（题面 HTML 带的），
        归一化后这些条目才能重新被查到；不同形态的同题条目自动合成一条，
        先出现的取值优先（已落盘的都是校验过的答案）。
        """
        out: dict = {}
        try:
            for k, v in (data or {}).items():
                nk = cache_key(k)
                if nk and nk not in out:
                    out[nk] = v
        except Exception as e:
            logger.warning(f"知识库键归一化失败，保持原样: {e}")
            return data or {}
        return out

    def _write_cache(self, data: dict) -> None:
        # 为缓存写入加锁，防止并发写入损坏文件
        try:
            with self._lock:
                parent = self.cache_file.parent
                if not parent.exists():
                    parent.mkdir(parents=True, exist_ok=True)
                # 写入临时文件后原子替换，减少并发写入时的损坏风险
                fd, tmp_path = tempfile.mkstemp(prefix=self.cache_file.name, dir=str(parent))
                try:
                    with os.fdopen(fd, "w", encoding="utf8") as fp:
                        json.dump(data, fp, ensure_ascii=False, indent=4)
                        fp.flush()
                        os.fsync(fp.fileno())
                    os.replace(tmp_path, str(self.cache_file))
                    self._mem = data          # 写盘成功后同步进程内副本
                except Exception as e:
                    # 清理临时文件
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
                    logger.error(f"Failed to write cache atomically: {e}")
        except IOError as e:
            logger.error(f"Failed to write cache: {e}")

    def get_cache(self, question: str) -> Optional[str]:
        # 键统一归一化：上游传进来的是题面原始标题（带题型标签/分值/空格）
        data = self._read_cache()
        return data.get(cache_key(question))

    def add_cache(self, question: str, answer: str, *, verified: bool = False) -> None:
        # 为缓存写入加锁，防止并发写入损坏文件
        key = cache_key(question)
        if not key:
            return
        with self._lock:
            data = self._read_cache()
            data[key] = answer
            self._write_cache(data)


class CloudCacheDAO:
    """服务端持久化的共享题库；本机只保留已有的迁移源文件。"""

    def __init__(self):
        self._failure_until = 0.0
        self._identity = None
        self._migration_lock = threading.Lock()

    def _request(self, callback, fallback):
        from api import kc_client
        identity = (kc_client._server(), kc_client.load_state().get('kc_token', ''))
        if identity != self._identity:
            self._identity = identity
            self._failure_until = 0.0
        if time.monotonic() < self._failure_until:
            return fallback
        try:
            return callback()
        except Exception as exc:
            self._failure_until = time.monotonic() + 15
            logger.warning(f'云端题库暂不可用：{exc}；查询将继续回退到其他来源')
            return fallback

    def get_many(self, titles: list[str]) -> list:
        from api import kc_client
        normalized = [cache_key(title) for title in titles]
        if not normalized:
            return []
        return self._request(lambda: kc_client.knowledge_query(normalized), [None] * len(titles))

    def get_cache(self, question: str) -> Optional[str]:
        return self.get_many([question])[0]

    def add_cache(self, question: str, answer: str, *, verified: bool = False):
        from api import kc_client
        key = cache_key(question)
        if not key or not isinstance(answer, str) or not answer.strip():
            return False
        return bool(self._request(lambda: kc_client.knowledge_write([
            {'question': key, 'answer': answer.strip(), 'verified': verified},
        ]), 0))

    def size(self) -> int:
        from api import kc_client
        return kc_client.knowledge_stats()['count']

    def migrate_legacy(self) -> int:
        """按文件内容标记导入进度；完整确认成功才记标记，失败可幂等重试。"""
        import hashlib
        from api import kc_client
        with self._migration_lock:
            stats = kc_client.knowledge_stats()
            scope = 'shared' if stats.get('scope') == 'shared' else str(stats['user_id'])
            state_key = hashlib.sha256((kc_client._server() + '/' + scope).encode()).hexdigest()
            markers = dict(kc_client.load_state().get('kc_knowledge_migrated', {}) or {})
            imported = 0
            for path in (knowledge_dir() / 'cache.json', Path('cache.json')):
                if not path.is_file():
                    continue
                content = path.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                marker = state_key + ':' + str(path)
                if markers.get(marker) == digest:
                    continue
                data = json.loads(content.decode('utf-8-sig'))
                if not isinstance(data, dict):
                    raise ValueError(f'旧题库不是题目到答案的字典：{path}')
                entries = [{'question': cache_key(q), 'answer': a.strip(), 'verified': False}
                           for q, a in data.items() if isinstance(q, str) and cache_key(q)
                           and len(q) <= 10000 and isinstance(a, str) and a.strip() and len(a) <= 20000]
                imported += kc_client.knowledge_write(entries, overwrite=False)
                markers[marker] = digest
                kc_client.save_state({'kc_knowledge_migrated': markers})
            return imported


class Tiku(ABC):
    CONFIG_PATH = os.path.join(os.getcwd(), "config.ini")
    DISABLE = False  # 停用标志
    SUBMIT = False  # 提交标志
    COVER_RATE = 0.8  # 覆盖率
    true_list = None
    false_list = None

    def __init__(self, config_path: Optional[str] = None) -> None:
        """
        初始化题库基类。

        Args:
            config_path: 配置文件路径，若为 None 则使用默认的 CONFIG_PATH。
        """
        self._name = None
        self._api = None
        self._conf = None
        self._config_path = config_path or self.CONFIG_PATH
        self.true_list = []
        self.false_list = []

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def api(self):
        return self._api

    @api.setter
    def api(self, value):
        self._api = value

    @property
    def token(self):
        return self._token

    @token.setter
    def token(self, value):
        self._token = value

    def init_tiku(self):
        # 仅用于题库初始化, 应该在题库载入后作初始化调用, 随后才可以使用题库
        # 尝试根据配置文件设置提交模式
        if not self._conf:
            self.config_set(self._get_conf())
        if not self.DISABLE:
            # 设置提交模式
            self.SUBMIT = True if self._conf['submit'] == 'true' else False
            self.COVER_RATE = float(self._conf['cover_rate'])
            self.true_list = self._conf['true_list'].split(',')
            self.false_list = self._conf['false_list'].split(',')
            # 关键配置缺失（远端题库没填 token / 地址）就直接停用：
            # 拦在发请求之前，否则题干会被白白送到第三方，还拿不到任何答案
            reason = self._ready_check()
            if reason:
                self.DISABLE = True
                logger.error(
                    f"{self.name}配置不完整，已跳过（不会向服务端发送题目）：{reason}")
                return
            # 调用自定义题库初始化
            self._init_tiku()

    def _ready_check(self) -> str:
        """返回「不能使用的原因」（空字符串 = 可用），由子类按需覆盖。

        远端题库缺 token / 服务地址时必须在**发请求之前**拦下 —— v1.5.6 实测：
        言溪 tokens 为空时 20 道章节检测题全部发了出去、全部失败、20 次随机猜，
        本地知识库一条都没沉淀。白送题干还拿不到答案是最糟的组合。
        """
        return ""

    def _init_tiku(self):
        # 仅用于题库初始化, 例如配置token, 交由自定义题库完成
        pass

    def config_set(self, config):
        self._conf = config

    def _get_conf(self):
        """
        从默认配置文件查询配置, 如果未能查到, 停用题库
        """
        try:
            config = configparser.ConfigParser()
            config.read(self._config_path, encoding="utf8")
            return config['tiku']
        except (KeyError, FileNotFoundError):
            logger.info("未找到tiku配置, 已忽略题库功能")
            self.DISABLE = True
            return None

    @property
    def _is_manual_mode(self) -> bool:
        return (
                getattr(self, 'is_manual', False) or
                self.__class__.__name__ == 'TikuManual' or
                (self.__class__.__name__ == 'TikuFallback' and any(
                    getattr(p, 'is_manual', False) or p.__class__.__name__ == 'TikuManual' for p in
                    getattr(self, 'providers', [])))
        )

    def query(self, q_info: dict) -> Optional[str]:
        if self.DISABLE:
            return None

        # 预处理：题面归一化（剥题号 / 【题型】标签 / 分值 / 首尾空白）
        # —— 与知识库键同口径，保证发给题库/AI 的题面干净、缓存命中一致（见 cache_key）
        if not self._is_manual_mode:
            logger.debug(f"原始标题：{q_info['title']}")
        q_info['title'] = cache_key(q_info['title'])
        if not self._is_manual_mode:
            logger.debug(f"处理后标题：{q_info['title']}")

        # 先过缓存（若存在章节检测错误反馈，说明处于重做模式，跳过缓存让大模型参考反馈重新作答）
        cache_dao = get_cache_dao()
        if not getattr(self, 'work_feedback', None):
            answer = cache_dao.get_cache(q_info['title'])
            if answer and isinstance(answer, str) and check_answer(answer, q_info['type'], self, cached=True):
                logger.info(f"从缓存中获取答案：{q_info['title']} -> {answer}")
                return answer.strip()
        try:
            answer = self._query(q_info)
        except Exception as e:
            logger.error(f"{self.name} 查询单个题目发生异常: {e}")
            return None
        if isinstance(answer, str) and answer.strip():
            answer = answer.strip()
            logger.info(f"从{self.name}获取答案：{q_info['title']} -> {answer}")
            if check_answer(answer, q_info['type'], self):
                cache_dao.add_cache(q_info['title'], answer)
                return answer
            logger.info(f"从{self.name}获取到的答案类型与题目类型不符，已舍弃")
        else:
            logger.error(f"从{self.name}获取答案失败：{q_info['title']}")
        return None

    def query_all(self, q_list: list[dict], query_delay: float = 0.0, *, _skip_cache=False) -> list[Optional[str]]:
        if self.DISABLE:
            return [None] * len(q_list)

        results = [None] * len(q_list)
        pending_indices = []

        cache_dao = get_cache_dao()
        skip_cache = _skip_cache or bool(getattr(self, 'work_feedback', None))
        cached_answers = None
        if not skip_cache and hasattr(cache_dao, 'get_many'):
            cached_answers = cache_dao.get_many([q['title'] for q in q_list])
        for idx, q in enumerate(q_list):
            if not self._is_manual_mode:
                logger.debug(f"原始标题：{q['title']}")
            # 与知识库键同口径归一化（剥题号 / 【题型】标签 / 分值 / 空白）
            q['title'] = cache_key(q['title'])
            if not self._is_manual_mode:
                logger.debug(f"处理后标题：{q['title']}")

            if skip_cache:
                # 重做模式：不走缓存，让大模型参考错误反馈重新作答
                pending_indices.append(idx)
                continue

            answer = cached_answers[idx] if cached_answers is not None else cache_dao.get_cache(q['title'])
            if answer and isinstance(answer, str) and check_answer(answer, q['type'], self, cached=True):
                logger.info(f"从缓存中获取答案：{q['title']} -> {answer}")
                results[idx] = answer.strip()
            else:
                pending_indices.append(idx)

        if not pending_indices:
            return results

        sub_q_list = [q_list[idx] for idx in pending_indices]
        sub_results = self._query_all(sub_q_list, query_delay=query_delay)

        if not isinstance(sub_results, list):
            logger.error(f"{self.name} _query_all 返回结果格式异常，期望列表")
            sub_results = [None] * len(pending_indices)
        elif len(sub_results) != len(pending_indices):
            logger.error(
                f"{self.name} _query_all 返回结果长度不匹配，期望 {len(pending_indices)}，实际 {len(sub_results)}")
            # 缺失题的位置未知，补齐可能把后题答案分配给前题；整批回退。
            sub_results = [None] * len(pending_indices)

        for idx, ans in zip(pending_indices, sub_results):
            q_info = q_list[idx]
            if isinstance(ans, str) and ans.strip():
                ans = ans.strip()
                logger.info(f"从{self.name}获取答案：{q_info['title']} -> {ans}")
                if check_answer(ans, q_info['type'], self):
                    if not isinstance(self, TikuFallback):
                        cache_dao.add_cache(q_info['title'], ans)
                    results[idx] = ans
                else:
                    logger.info(f"从{self.name}获取到的答案类型与题目类型不符，已舍弃")
            else:
                logger.error(f"从{self.name}获取答案失败：{q_info['title']}")

        return results

    @abstractmethod
    def _query(self, q_info: dict) -> Optional[str]:
        """
        查询接口, 交由自定义题库实现
        """
        pass


    def set_work_feedback(self, feedback) -> None:
        """
        设置上一轮章节检测的错误反馈，供支持反馈的大模型题库在重新作答时参考。

        Args:
            feedback: 错误反馈，可以是 str 或 list[str]（描述哪些题目答错、正确答案是什么）
        """
        self.work_feedback = feedback

    def _query_all(self, q_list: list[dict], query_delay: float = 0.0) -> list[Optional[str]]:
        """
        批量查询的实现接口，默认循环调用单个查询 _query。
        子类若有批量查询或交互需求（如手动模式），可重写此方法。
        """
        results = []
        for q in q_list:
            if query_delay > 0:
                time.sleep(query_delay)
            try:
                results.append(self._query(q))
            except Exception as e:
                logger.error(f"{self.name} 查询单个题目发生异常: {e}")
                results.append(None)
        return results

    @staticmethod
    def get_tiku_from_config(config: Optional[dict] = None, config_path: Optional[str] = None):
        """
        从配置文件加载题库, 这个配置可以是用户提供, 可以是默认配置文件
        """
        conf = config
        path = config_path or Tiku.CONFIG_PATH
        if not conf:
            # 尝试从默认配置文件加载
            try:
                config_parser = configparser.ConfigParser()
                config_parser.read(path, encoding="utf8")
                conf = config_parser['tiku']
            except (KeyError, FileNotFoundError):
                logger.error("未找到题库配置, 已忽略题库功能")
                dummy = DummyTiku(config_path=path)
                return dummy

        try:
            cls_name = conf['provider']
            if not cls_name:
                raise KeyError
        except KeyError:
            logger.error("未找到题库配置, 已忽略题库功能")
            dummy = DummyTiku(config_path=path)
            return dummy

        providers = [name.strip() for name in cls_name.split(',') if name.strip()]
        if not providers:
            logger.error("题库provider配置为空, 已忽略题库功能")
            dummy = DummyTiku(config_path=path)
            return dummy

        invalid_providers = [name for name in providers if name not in PROVIDER_REGISTRY]
        if invalid_providers:
            logger.error(f"题库provider配置无效: {', '.join(invalid_providers)}")
            dummy = DummyTiku(config_path=path)
            return dummy

        # A configured bank miss must reach the model, never a random answer.
        providers = [name for name in providers if name != 'AI'] + ['AI']

        if len(providers) == 1:
            provider_cls = PROVIDER_REGISTRY[providers[0]]
            if not isinstance(provider_cls, type) or not issubclass(provider_cls, Tiku):
                logger.error(f"题库provider配置无效: {providers[0]}")
                dummy = DummyTiku(config_path=path)
                return dummy
            new_cls = provider_cls(config_path=path)
            new_cls.config_set(conf)
            return new_cls

        chain_providers = []
        for provider_name in providers:
            provider_cls = PROVIDER_REGISTRY[provider_name]
            if not isinstance(provider_cls, type) or not issubclass(provider_cls, Tiku):
                logger.error(f"题库provider配置无效: {provider_name}")
                dummy = DummyTiku(config_path=path)
                return dummy
            provider = provider_cls(config_path=path)
            provider.config_set(conf)
            chain_providers.append(provider)
        fallback = TikuFallback(chain_providers, config_path=path)
        fallback.config_set(conf)
        return fallback

    def judgement_select(self, answer: str) -> bool:
        """
        这是一个专用的方法, 要求配置维护两个选项列表, 一份用于正确选项, 一份用于错误选项, 以应对题库对判断题答案响应的各种可能的情况
        它的作用是将获取到的答案answer与可能的选项列对比并返回对应的布尔值
        """
        if self.DISABLE:
            return False
        # 对响应的答案作处理
        answer = answer.strip().lower()

        # 内置的高频通用判断词规整
        if answer in ['true', 't', '1', '对', '正确', '√', '是', 'yes', 'y']:
            return True
        if answer in ['false', 'f', '0', '错', '错误', '×', '否', 'no', 'n', '不对', '不正确']:
            return False

        # 兼容自定义配置列表
        if answer in [x.lower() for x in self.true_list] or answer in self.true_list:
            return True
        elif answer in [x.lower() for x in self.false_list] or answer in self.false_list:
            return False
        else:
            logger.error(f'无法识别判断题答案 -> {answer}，不作答')
            return None

    def get_submit_params(self):
        """
        这是一个专用方法, 用于根据当前设置的提交模式, 响应对应的答题提交API中的pyFlag值
        """
        # 留空直接提交, 1保存但不提交
        if self.SUBMIT:
            return ""
        else:
            return "1"

    def check_llm_connection(self) -> bool:
        """
        检查大模型连接是否可用
        默认返回 True（非大模型题库不需要检查）
        """
        return True


class TikuFallback(Tiku):
    # 多题库回退实现，按 provider 中配置顺序依次查询。
    def __init__(self, providers=None, config_path: Optional[str] = None):
        """初始化多题库回退."""
        super().__init__(config_path)
        self.name = '多题库回退'
        self.providers = providers or []
        self.skip_answer_validation = True

    def _init_tiku(self):
        active = []
        for provider in self.providers:
            try:
                provider.init_tiku()
                if not provider.DISABLE:
                    active.append(provider)
            except Exception as e:
                logger.error(f'初始化题库 {provider.name} 失败: {e}')
        self.providers = active
        if not self.providers:
            logger.error('多题库回退初始化失败: 没有可用题库')
            self.DISABLE = True
        else:
            logger.info(f"多题库回退已启用，查询顺序: {', '.join([p.__class__.__name__ for p in self.providers])}")

    def _query(self, q_info: dict) -> Optional[str]:
        for provider in self.providers:
            if provider.DISABLE:
                continue
            try:
                answer = provider._query(q_info)
            except Exception as e:
                provider_id = f'{provider.name}({provider.__class__.__name__})'
                logger.exception(f'{self.name} 查询时 {provider_id} 异常: {e}')
                continue
            if not answer:
                logger.info(f'{provider.name} 未命中，回退到下一个题库')
                continue

            # 若当前题库返回答案但类型不符，则继续回退。
            if check_answer(answer, q_info['type'], provider):
                logger.info(f'{provider.name} 命中答案')
                return answer

            logger.info(f'{provider.name} 返回答案类型不符，回退到下一个题库')
        return None

    def _query_all(self, q_list: list[dict], query_delay: float = 0.0) -> list[Optional[str]]:
        results = [None] * len(q_list)
        pending_indices = list(range(len(q_list)))

        for provider in self.providers:
            if not pending_indices:
                break
            if provider.DISABLE:
                continue

            sub_q_list = [q_list[idx] for idx in pending_indices]
            try:
                sub_results = provider.query_all(sub_q_list, query_delay=query_delay, _skip_cache=True)
            except Exception as e:
                provider_id = f'{provider.name}({provider.__class__.__name__})'
                logger.exception(f'{self.name} 批量查询时 {provider_id} 异常: {e}')
                continue

            if not isinstance(sub_results, list):
                logger.error(f"{provider.name} 批量查询返回数据格式异常（非列表），跳过该题库")
                continue

            if len(sub_results) != len(pending_indices):
                logger.error(
                    f"{provider.name} 批量查询返回结果长度（{len(sub_results)}）与请求题目数（{len(pending_indices)}）不匹配，跳过该题库以防答案错位")
                continue

            next_pending_indices = []
            for orig_idx, ans in zip(pending_indices, sub_results):
                if ans:
                    logger.info(f'{provider.name} 命中答案: {q_list[orig_idx]["title"]} -> {ans}')
                    results[orig_idx] = ans
                else:
                    logger.info(f'{provider.name} 未命中或返回答案无效，将回退')
                    next_pending_indices.append(orig_idx)
            pending_indices = next_pending_indices

        return results

    def set_work_feedback(self, feedback) -> None:
        """将章节检测错误反馈转发给支持反馈的子题库（如 AI 大模型）."""
        super().set_work_feedback(feedback)
        for provider in self.providers:
            if hasattr(provider, 'set_work_feedback'):
                try:
                    provider.set_work_feedback(feedback)
                except Exception as e:
                    logger.warning(f"向子题库 {provider.name} 设置错误反馈失败: {e}")

    def check_llm_connection(self) -> bool:
        for provider in self.providers:
            if not provider.check_llm_connection():
                logger.error(f'{provider.name} 连接检查失败')
                return False
        return True


# 按照以下模板实现更多题库

class TikuYanxi(Tiku):
    # 言溪题库实现
    def __init__(self, config_path: Optional[str] = None) -> None:
        """初始化言溪题库实例."""
        super().__init__(config_path)
        self.name = '言溪题库'
        self.api = 'https://tk.enncy.cn/query'
        self._token = None
        self._token_index = 0  # token队列计数器
        self._times = 100  # 查询次数剩余, 初始化为100, 查询后校对修正

    def _query(self, q_info: dict):
        # 强制直连（api/_net.py）：题库域名全是国内站，吃环境代理只会撞死端口
        res = _net_send("GET",
            self.api,
            params={
                'question': q_info['title'],
                'token': self._token,
                # 'type':q_info['type'], #修复478题目类型与答案类型不符（不想写后处理了）
                # 没用，就算有type和options，言溪题库还是可能返回类型不符，问了客服，type仅用于收集
            },
            verify=False,
            timeout=15
        )
        if res.status_code == 200:
            res_json = res.json()
            if not res_json['code']:
                # 如果是因为TOKEN次数到期, 则更换token
                if self._times == 0 or '次数不足' in res_json['data']['answer']:
                    logger.info(f'TOKEN查询次数不足, 将会更换并重新搜题')
                    self._token_index += 1
                    self.load_token()
                    # 重新查询
                    return self._query(q_info)
                logger.error(
                    f'{self.name}查询失败:\n\t剩余查询数{res_json["data"].get("times", f"{self._times}(仅参考)")}:\n\t消息:{res_json["message"]}')
                return None
            self._times = res_json["data"].get("times", self._times)
            return res_json['data']['answer'].strip()
        else:
            logger.error(f'{self.name}查询失败:\n{res.text}')
        return None

    def load_token(self):
        token_list = self._conf['tokens'].split(',')
        if self._token_index == len(token_list):
            # TOKEN 用完
            logger.error('TOKEN用完, 请自行更换再重启脚本')
            raise PermissionError(f'{self.name} TOKEN 已用完, 请更换')
        self._token = token_list[self._token_index]

    def _ready_check(self) -> str:
        if not (self._conf.get('tokens') or '').strip():
            return ("未填写言溪 token（把题目发过去也拿不到答案）。"
                    "请到 https://tk.enncy.cn/ 获取后填进「参数设置 → 题库 → 题库 Token」，"
                    "或改用「本地知识库 + AI」组合")
        return ""

    def _init_tiku(self):
        self.load_token()


class TikuGo(Tiku):
    # GO题（网课小工具题库）实现
    def __init__(self, config_path: Optional[str] = None) -> None:
        """初始化GO题实例."""
        super().__init__(config_path)
        self.name = 'GO题（网课小工具题库）'
        self.api = 'https://q.icodef.com/wyn-nb?v=4'
        self._headers = {
            'Authorization': '',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        self._request_lock = threading.Lock()
        self._last_request_time = 0.0
        self._min_interval = 1.0
        self._retry_times = 3
        self._retry_backoff = 1.2

    def _sleep_for_next_request(self) -> None:
        with self._request_lock:
            now = time.time()
            wait_time = max(0.0, self._last_request_time + self._min_interval - now)
            self._last_request_time = now + wait_time
        if wait_time > 0:
            time.sleep(wait_time)

    def _mark_request_finished(self) -> None:
        with self._request_lock:
            self._last_request_time = time.time()

    def _request_question(self, question: str, attempt: int) -> Optional[requests.Response]:
        try:
            self._sleep_for_next_request()
            res = _net_send("POST",
                self.api,
                data={'question': question},
                headers=self._headers,
                verify=True,
                timeout=15
            )
            self._mark_request_finished()
            return res
        except requests.exceptions.RequestException as e:
            logger.error(f'{self.name}查询异常 ({attempt}/{self._retry_times}): {e}')
            self._mark_request_finished()
            return None

    def _parse_response(self, res: requests.Response) -> Optional[dict]:
        if res.status_code != 200:
            logger.error(f'{self.name}查询失败: 状态码 {res.status_code}, 响应: {res.text}')
            return None

        try:
            res_json = res.json()
        except ValueError:
            logger.error(f'{self.name}查询失败: 返回内容不是有效JSON, 响应: {res.text}')
            return None

        try:
            code = int(str(res_json.get('code', '')).strip())
        except ValueError:
            code = 0

        answer = str(res_json.get('data', '')).strip()
        msg = str(res_json.get('msg', '')).strip()
        raw_text = f'{answer} {msg}'
        is_throttled = any(key in raw_text for key in ['流控限制', '速度太快', '并发限制', '忙不过来'])
        return {
            'code': code,
            'answer': answer,
            'msg': msg,
            'is_throttled': is_throttled,
        }

    def _sleep_retry(self, attempt: int, reason: str, include_min_interval: bool = False) -> None:
        if include_min_interval:
            sleep_seconds = max(self._min_interval, self._retry_backoff * attempt)
        else:
            sleep_seconds = self._retry_backoff * attempt
        logger.warning(f'{self.name}{reason}，{sleep_seconds:.1f}s 后重试 ({attempt}/{self._retry_times})')
        time.sleep(sleep_seconds)

    @staticmethod
    def _is_placeholder_answer(answer: str, msg: str) -> bool:
        return '李恒雅' in answer or '李恒雅' in msg

    def _query(self, q_info: dict):
        title = q_info.get('title', '')
        candidates = [
            title,
            re.sub(r'^【[^】]+】\s*', '', title).strip(),
            re.sub(r'^\[[^\]]+\]\s*', '', title).strip(),
        ]
        seen = set()
        normalized_titles = []
        for item in candidates:
            if item and item not in seen:
                seen.add(item)
                normalized_titles.append(item)

        for query_title in normalized_titles:
            answer = self._query_once(query_title)
            if answer:
                return answer
        return None

    def _query_once(self, question: str) -> Optional[str]:
        for attempt in range(1, self._retry_times + 1):
            res = self._request_question(question, attempt)
            if res is None:
                if attempt < self._retry_times:
                    self._sleep_retry(attempt, '查询异常', include_min_interval=True)
                    continue
                break

            parsed = self._parse_response(res)
            if not parsed:
                return None

            code = parsed['code']
            answer = parsed['answer']
            msg = parsed['msg']
            is_throttled = parsed['is_throttled']

            if code != 1:
                if is_throttled and attempt < self._retry_times:
                    self._sleep_retry(attempt, '触发流控')
                    continue
                logger.info(f"{self.name}未命中或失败: {msg or '未知错误'}")
                return None

            if not answer:
                return None

            # GO题库在未搜到时可能在 data/msg 中返回“李恒雅正在努力撰写中...”。
            if self._is_placeholder_answer(answer, msg):
                if is_throttled and attempt < self._retry_times:
                    self._sleep_retry(attempt, '命中流控提示')
                    continue
                return None

            return answer

        return None

    def _init_tiku(self):
        self._headers['Authorization'] = self._conf.get('go_authorization', self._headers['Authorization'])
        try:
            min_interval = float(self._conf.get('go_min_interval', self._min_interval))
            if min_interval < 0:
                raise ValueError('go_min_interval must be non-negative')
            self._min_interval = min_interval
        except (TypeError, ValueError):
            logger.warning(f'{self.name}配置 go_min_interval 无效，使用默认值 {self._min_interval}')

        try:
            retry_times = int(self._conf.get('go_retry_times', self._retry_times))
            if retry_times < 1:
                raise ValueError('go_retry_times must be >= 1')
            self._retry_times = retry_times
        except (TypeError, ValueError):
            logger.warning(f'{self.name}配置 go_retry_times 无效，使用默认值 {self._retry_times}')

        try:
            retry_backoff = float(self._conf.get('go_retry_backoff', self._retry_backoff))
            if retry_backoff < 0:
                raise ValueError('go_retry_backoff must be non-negative')
            self._retry_backoff = retry_backoff
        except (TypeError, ValueError):
            logger.warning(f'{self.name}配置 go_retry_backoff 无效，使用默认值 {self._retry_backoff}')


class TikuLike(Tiku):
    # LIKE知识库实现 参考 https://www.datam.site/
    def __init__(self, config_path: Optional[str] = None) -> None:
        """初始化LIKE知识库实例."""
        super().__init__(config_path)
        self.name = 'LIKE知识库'
        self.ver = '2.0.0'  # 对应官网API版本
        self.query_api = 'https://app.datam.site/api/v1/query'
        self.models_api = 'https://app.datam.site/api/v1/query/models'
        self.balance_api = 'https://app.datam.site/api/v1/balance'
        self.homepage = 'https://www.datam.site'
        self._model = None
        self._timeout = 300
        self._retry = True
        self._retry_times = 3
        self._tokens = []
        self._balance = {}
        self._search = False
        self._vision = True
        self._count = 0
        self._headers = {"Content-Type": "application/json"}

    def _query(self, q_info: dict = None):
        if not q_info:
            logger.error("当前无题目信息，请检查")
            return ""

        q_info_map = {"single": "【单选题】", "multiple": "【多选题】", "completion": "【填空题】", "judgement": "【判断题】"}
        q_info_prefix = q_info_map.get(q_info['type'], "【其他类型题目】")
        options = ', '.join(q_info['options']) if isinstance(q_info['options'], list) else q_info['options']
        question = f"{q_info_prefix}{q_info['title']}\n"

        if q_info['type'] in ['single', 'multiple']:
            question += f"选项为: {options}\n"

        # 随机选择一个token进行查询
        token = random.choice(self._tokens)

        # 检查该token是否有余额
        if self._balance.get(token, 0) <= 0:
            logger.error(f'{self.name}当前Token查询次数不足: ...{token[-5:]}')
            # 尝试选择其他有余额的token
            available_tokens = [t for t in self._tokens if self._balance.get(t, 0) > 0]
            if available_tokens:
                token = random.choice(available_tokens)
            else:
                logger.error(f'{self.name}所有Token查询次数都不足')
                return None

        ans = None
        try_times = 0

        # 尝试查询，直到成功或达到重试次数
        while not ans and self._retry and try_times < self._retry_times:
            ans = self._query_single(token, question)
            try_times += 1
            if ans:  # 如果查询成功，减少余额
                self._balance[token] -= 1
                logger.info(f'使用Token ...{token[-5:]} 查询成功，剩余次数: {self._balance[token]}')
                break
            elif try_times < self._retry_times:
                logger.warning(f'使用Token ...{token[-5:]} 查询失败，进行第 {try_times + 1} 次重试...')

        # 10次查询后更新余额
        self._count = (self._count + 1) % 10
        if self._count == 0:
            self.update_times()

        return ans

    def _query_single(self, token: str = "", query: str = "") -> str:
        """
        查询单个问题的答案
        
        Args:
            token: API访问令牌
            query: 查询的问题内容
            
        Returns:
            查询到的答案，如果失败则返回None
        """
        # 验证输入参数
        if not token:
            logger.error(f'{self.name}查询失败: 未提供有效的token')
            return None

        if not query:
            logger.error(f'{self.name}查询失败: 查询内容为空')
            return None

        # 设置请求头
        temp_headers = self._headers.copy()
        temp_headers['Authorization'] = f'Bearer {token}'

        # 准备请求数据
        request_data = {
            'query': query,
            'model': self._model if self._model else '',
            'search': self._search,
            'vision': self._vision
        }

        # 发送API请求
        try:
            res = _net_send("POST",
                self.query_api,
                json=request_data,
                headers=temp_headers,
                verify=False,
                timeout=self._timeout  # 添加超时设置
            )
        except requests.exceptions.Timeout:
            logger.error(f'{self.name}查询超时: 请求超过300秒')
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f'{self.name}网络连接错误: 无法连接到API服务器')
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f'{self.name}查询异常: \n{e}')
            return None
        except Exception as e:
            logger.error(f'{self.name}查询发生未知错误: \n{e}')
            return None

        # 处理HTTP响应
        if res.status_code == 200:
            return self._parse_response(res)
        elif res.status_code == 401:
            logger.error(f'{self.name}认证失败: 请检查Token是否正确或已过期')
        elif res.status_code == 429:
            logger.error(f'{self.name}请求过于频繁: 已达到API速率限制')
        elif res.status_code == 500:
            logger.error(f'{self.name}服务器内部错误: API服务暂时不可用')
        elif res.status_code == 400:
            logger.error(f'{self.name}请求参数错误: 请检查查询内容格式')
        elif res.status_code == 403:
            logger.error(f'{self.name}访问被拒绝: 可能是Token权限不足')
        else:
            logger.error(f'{self.name}查询失败: 状态码 {res.status_code}, 响应内容: \n{res.text}')

        return None

    def _parse_response(self, response):
        """
        解析API响应
        
        Args:
            response: HTTP响应对象
            
        Returns:
            解析后的答案，如果解析失败则返回None
        """
        try:
            res_json = response.json()
        except json.JSONDecodeError:
            logger.error(f'{self.name}响应解析失败: 响应不是有效的JSON格式')
            return None
        except Exception as e:
            logger.error(f'{self.name}响应解析异常: {e}')
            return None

        # 记录响应消息
        msg = res_json.get('message', '')
        if msg:
            logger.info(f'{self.name}响应消息: {msg}')

        results = res_json.get('results', {})
        if not results or not isinstance(results, dict):
            logger.error(f'{self.name}查询结果格式错误: API返回结果中results字段格式不正确')
            return None

        output = results.get('output', None)
        if output is None or not isinstance(output, dict):
            logger.error(f'{self.name}查询结果中output字段格式错误或不存在')
            return None

        q_type = output.get('questionType', None)
        if q_type is None:
            logger.error(f'{self.name}查询结果中questionType字段不存在')
            return None

        answer = output.get('answer', None)
        if answer is None:
            logger.error(f'{self.name}查询结果中answer字段不存在')
            return None

        # 根据题目类型提取答案
        return self._extract_answer_by_type(q_type, answer)

    def _extract_answer_by_type(self, q_type: str, answer: dict) -> str:
        """
        根据题目类型提取答案
        
        Args:
            q_type: 题目类型
            answer: 答案字典
            
        Returns:
            提取的答案文本
        """
        if not isinstance(answer, dict):
            logger.error(f'{self.name}答案格式错误: 不是有效的字典格式')
            return None

        if q_type == "CHOICE":
            selected_options = answer.get('selectedOptions', None)
            if selected_options is not None:
                if isinstance(selected_options, list) and selected_options:
                    # 过滤掉None和空字符串
                    valid_options = [opt for opt in selected_options if opt is not None and str(opt).strip()]
                    if valid_options:
                        return '\n'.join(str(opt) for opt in valid_options)
                    else:
                        logger.error(f'{self.name}CHOICE类型题目没有有效的选项内容')
                else:
                    logger.error(f'{self.name}CHOICE类型题目没有有效的选项内容')
            else:
                logger.error(f'{self.name}CHOICE类型题目缺少selectedOptions字段')
        elif q_type == "FILL_IN_BLANK":
            blanks = answer.get('blanks', None)
            if blanks is not None:
                if isinstance(blanks, list) and blanks:
                    # 过滤掉None和空字符串
                    valid_blanks = [blank for blank in blanks if blank is not None and str(blank).strip()]
                    if valid_blanks:
                        return "\n".join(str(blank) for blank in valid_blanks)
                    else:
                        logger.error(f'{self.name}FILL_IN_BLANK类型题目没有有效的填空内容')
                else:
                    logger.error(f'{self.name}FILL_IN_BLANK类型题目没有有效的填空内容')
            else:
                logger.error(f'{self.name}FILL_IN_BLANK类型题目缺少blanks字段')
        elif q_type == "JUDGMENT":
            is_correct = answer.get('isCorrect', None)
            if is_correct is not None:
                return "正确" if is_correct else "错误"
            else:
                logger.error(f'{self.name}JUDGMENT类型题目缺少isCorrect字段')
        else:
            otherText = answer.get('otherText', None)
            if otherText is not None:
                return str(otherText)
            else:
                logger.error(f'{self.name}未知题目类型{q_type}且缺少otherText字段')

        return None

    def get_api_balance(self, token: str = ""):
        if not token:
            logger.error(f'{self.name}获取余额失败: 未提供有效的token')
            return 0

        temp_headers = self._headers.copy()
        temp_headers['Authorization'] = f'Bearer {token}'
        try:
            res = _net_send("GET",
                self.balance_api,
                headers=temp_headers,
                verify=False,
                timeout=self._timeout
            )
            if res.status_code == 200:
                res_json = res.json()
                return int(res_json.get("balance", 0))
            else:
                logger.error(f'{self.name}请求余额接口失败，状态码: {res.status_code}')
                return 0
        except requests.exceptions.Timeout:
            logger.error(f'{self.name}获取余额超时: 请求超过30秒')
            return 0
        except requests.exceptions.ConnectionError:
            logger.error(f'{self.name}网络连接错误: 无法连接到余额查询API服务器')
            return 0
        except ValueError:  # json解析错误或int转换错误
            logger.error(f'{self.name}余额响应解析失败: 响应格式不正确')
            return 0
        except Exception as e:
            logger.error(f'{self.name}Token余额查询过程中出现错误: {e}')
            return 0

    def update_times(self) -> None:
        if not self._tokens:
            logger.warning(f'{self.name}未加载任何Token, 无法更新余额')
            return
        for token in self._tokens:
            balance = self.get_api_balance(token)
            self._balance[token] = balance
            logger.info(
                f"当前LIKE知识库Token: ...{token[-5:]} 的剩余查询次数为: {balance} (仅供参考, 实际次数以查询结果为准)")

    def load_tokens(self) -> None:
        tokens_str = self._conf.get('tokens')
        if not tokens_str:
            logger.error(f'{self.name}配置中未找到tokens')
            self._tokens = []
            return
        if ',' in tokens_str:
            tokens = [token.strip() for token in tokens_str.split(',') if token.strip()]
        else:
            tokens = [tokens_str.strip()] if tokens_str.strip() else []
        self._tokens = tokens
        if not self._tokens:
            logger.warning(f'{self.name}未加载任何有效的Token')

    def load_config(self) -> None:
        # 从配置中获取参数，提供默认值
        self._search = self._conf.get('likeapi_search', False)
        self._model = self._conf.get('likeapi_model', None)
        self._vision = self._conf.get('likeapi_vision', True)
        self._retry = self._conf.get("likeapi_retry", True)
        self._retry_times = int(self._conf.get("likeapi_retry_times", 3))

    def _ready_check(self) -> str:
        if not (self._conf.get('tokens') or '').strip():
            return ("未填写 LIKE token（题目会被上传到 datam.site 且拿不到答案）。"
                    "推荐改用「本地知识库 + AI」组合")
        return ""

    def _init_tiku(self) -> None:
        self.load_config()
        self.load_tokens()
        if self._tokens:
            self.update_times()
        else:
            logger.error(f'{self.name}初始化失败: 未加载任何有效的Token')
            self.DISABLE = True


# 自建聚合题库（kc-api）的入口密钥：内置默认值，用户无需配置。
# 换用自己部署的服务时，在 config.ini [tiku] 段加 `adapter_key=` 覆盖即可。
# 仅用于挡住公开扫描/白嫖（服务端 nginx 校验 X-Leke-Key），不是安全边界。
ADAPTER_ENTRY_KEY = "d1b3cae875425d3361c4d7cc72b81f62"


class TikuAdapter(Tiku):
    # TikuAdapter题库实现 https://github.com/DokiDoki1103/tikuAdapter
    def __init__(self, config_path: Optional[str] = None) -> None:
        """初始化TikuAdapter题库实例."""
        super().__init__(config_path)
        self.name = 'TikuAdapter题库'
        self.api = ''
        self._entry_key = ADAPTER_ENTRY_KEY

    def _query(self, q_info: dict):
        # 判断题目类型
        if q_info['type'] == "single":
            type = 0
        elif q_info['type'] == 'multiple':
            type = 1
        elif q_info['type'] == 'completion':
            type = 2
        elif q_info['type'] == 'judgement':
            type = 3
        else:
            type = 4

        options = q_info['options']
        headers = {}
        if getattr(self, '_entry_key', ''):
            headers['X-Leke-Key'] = self._entry_key
        # 自建题库在 kc-api（腾讯云国内），直连即可；吃环境代理会被死端口卡死
        res = _net_send("POST",
            self.api,
            json={
                'question': q_info['title'],
                'options': [sub(r'^[A-Za-z]\.?、?\s?', '', option) for option in options.split('\n')],
                'type': type
            },
            headers=headers,
            verify=False,
            timeout=15
        )
        if res.status_code == 200:
            res_json = res.json()
            # if bool(res_json['plat']):
            # plat无论搜没搜到答案都返回0
            # 这个参数是tikuadapter用来设定自定义的平台类型
            answer_data = res_json.get('answer')
            best = answer_data.get('bestAnswer') if isinstance(answer_data, dict) else None
            if not best:
                logger.error("查询失败, 返回：" + res.text)
                return None
            if isinstance(best, str):
                return best.strip() or None
            if isinstance(best, list) and all(isinstance(item, str) for item in best):
                return "\n".join(item.strip() for item in best if item.strip()) or None
            return None
        # else:
        #   logger.error(f'{self.name}查询失败:\n{res.text}')
        return None

    def _ready_check(self) -> str:
        if not (self._conf.get('url') or '').strip():
            return ("未填写自建 TikuAdapter 服务地址（url）。"
                    "请填「参数设置 → 题库 → TikuAdapter 配置」，或改用其他题库")
        return ""

    def _init_tiku(self):
        # self.load_token()
        self.api = self._conf['url']
        self._entry_key = (self._conf.get('adapter_key') or '').strip() or ADAPTER_ENTRY_KEY


class AI(Tiku):
    # AI大模型答题实现
    def __init__(self, config_path: Optional[str] = None) -> None:
        """初始化AI大模型答题实现."""
        super().__init__(config_path)
        self.name = 'AI大模型答题'
        self.last_request_time = None
        self._lock = threading.Lock()
        self.work_feedback = None  # 章节检测错误反馈（重做时参考）

    def set_work_feedback(self, feedback) -> None:
        """
        设置章节检测上一轮的错误反馈，供重新作答时参考。

        Args:
            feedback: str 或 list[str]，描述答错的题目与正确答案
        """
        self.work_feedback = feedback

    def _build_work_feedback_text(self) -> str:
        """将 work_feedback 转为提示词文本."""
        fb = self.work_feedback
        if not fb:
            return ""
        if isinstance(fb, str):
            return fb
        lines = [
            "你上一次作答的章节检测中有以下题目回答错误，"
            "请根据题目与正确答案仔细思考错因，纠正你的判断，本次作答务必保证每道题都正确："
        ]
        for item in fb:
            lines.append(str(item))
        return "\n".join(lines)

    def _is_deepseek_v4(self) -> bool:
        return (
                'api.deepseek.com' in (self.endpoint or '').lower()
                and (self.model or '').lower().startswith('deepseek-v4')
        )

    def _completion_kwargs(self, **kwargs):
        if self._is_deepseek_v4():
            # DeepSeek V4 defaults to thinking mode, which can leave message.content empty.
            kwargs['extra_body'] = {'thinking': {'type': 'disabled'}}
        return kwargs

    def _wait_for_interval(self):
        if self.last_request_time:
            interval_time = time.time() - self.last_request_time
            if interval_time < self.min_interval_seconds:
                sleep_time = self.min_interval_seconds - interval_time
                logger.debug(f"API请求间隔过短, 等待 {sleep_time} 秒")
                time.sleep(sleep_time)

    def _query(self, q_info: dict):
        with self._lock:
            return self._query_locked(q_info)

    def _query_locked(self, q_info: dict):
        def remove_md_json_wrapper(md_str):
            # 使用正则表达式匹配Markdown代码块并提取内容
            pattern = r'^\s*```(?:json)?\s*(.*?)\s*```\s*$'
            match = re.search(pattern, md_str, re.DOTALL)
            return match.group(1).strip() if match else md_str.strip()

        if self.http_proxy:
            proxy = self.http_proxy
            httpx_client = httpx.Client(proxy=proxy)
            client = OpenAI(http_client=httpx_client, base_url=self.endpoint, api_key=self.key)
        else:
            # 未配代理时强制不吃环境代理（trust_env=False），避免撞死端口
            httpx_client = httpx.Client(trust_env=False)
            client = OpenAI(http_client=httpx_client, base_url=self.endpoint, api_key=self.key)
        # 去除选项字母，防止大模型直接输出字母而非内容
        options_list = q_info['options'].split('\n')
        cleaned_options = [re.sub(r"^[A-Z]\s*", "", option) for option in options_list]
        options = "\n".join(cleaned_options)

        # 上一轮章节检测的错误反馈（若有）
        feedback_text = self._build_work_feedback_text()

        def _make_messages(system_content: str, user_content: str) -> list:
            """构造带反馈上下文的消息列表."""
            messages = [
                {
                    "role": "system",
                    "content": system_content
                },
            ]
            if feedback_text:
                messages.append(
                    {
                        "role": "system",
                        "content": feedback_text
                    }
                )
            messages.append(
                {
                    "role": "user",
                    "content": user_content
                }
            )
            return messages

        # 判断题目类型
        self._wait_for_interval()
        self.last_request_time = time.time()
        if q_info['type'] == "single":
            completion = tracked_completion(client, **self._completion_kwargs(
                model=self.model,
                messages=_make_messages(
                    "本题为单选题，你只能选择一个选项，请根据题目和选项回答问题，以json格式输出正确的选项内容，示例回答：{\"Answer\": [\"答案\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料",
                    f"题目：{q_info['title']}\n选项：{options}"
                )
            ))
        elif q_info['type'] == 'multiple':
            completion = tracked_completion(client, **self._completion_kwargs(
                model=self.model,
                messages=_make_messages(
                    "本题为多选题，你必须选择两个或以上选项，请根据题目和选项回答问题，以json格式输出正确的选项内容，示例回答：{\"Answer\": [\"答案1\",\n\"答案2\",\n\"答案3\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料",
                    f"题目：{q_info['title']}\n选项：{options}"
                )
            ))
        elif q_info['type'] == 'completion':
            completion = tracked_completion(client, **self._completion_kwargs(
                model=self.model,
                messages=_make_messages(
                    "本题为填空题，你必须根据语境和相关知识填入合适的内容，请根据题目回答问题，以json格式输出正确的答案，示例回答：{\"Answer\": [\"答案\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料",
                    f"题目：{q_info['title']}"
                )
            ))
        elif q_info['type'] == 'judgement':
            completion = tracked_completion(client, **self._completion_kwargs(
                model=self.model,
                messages=_make_messages(
                    "本题为判断题，你只能回答正确或者错误，请根据题目回答问题，以json格式输出正确的答案，示例回答：{\"Answer\": [\"正确\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料",
                    f"题目：{q_info['title']}"
                )
            ))
        else:
            completion = tracked_completion(client, **self._completion_kwargs(
                model=self.model,
                messages=_make_messages(
                    "本题为简答题，你必须根据语境和相关知识填入合适的内容，请根据题目回答问题，以json格式输出正确的答案，示例回答：{\"Answer\": [\"这是我的答案\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料",
                    f"题目：{q_info['title']}"
                )
            ))

        try:
            response = json.loads(remove_md_json_wrapper(completion.choices[0].message.content))
            from api.work_answer_policy import parse_model_answer
            return parse_model_answer(response) or None
        except:
            logger.error("无法解析大模型输出内容")
            return None

    def _ready_check(self) -> str:
        ep = (self._conf.get('endpoint') or '').strip()
        key = (self._conf.get('key') or '').strip()
        model = (self._conf.get('model') or '').strip()
        if not (ep and key and model):
            missing = "、".join(
                n for n, v in (("服务地址", ep), ("密钥", key), ("模型名", model)) if not v)
            return (f"AI 配置缺少 {missing} —— 到「AI 大模型」页填好后点「保存并使用」"
                    "（填完会自动把 AI 加进题库来源）")
        return ""

    def _init_tiku(self):
        self.endpoint = self._conf['endpoint']
        self.key = self._conf['key']
        self.model = self._conf['model']
        self.http_proxy = self._conf['http_proxy']
        self.min_interval_seconds = int(self._conf['min_interval_seconds'])

    def check_llm_connection(self) -> bool:
        """
        检查大模型连接是否可用
        发送一个简单的测试请求来验证 API 配置
        """
        with self._lock:
            logger.info(f'正在检查 {self.name} 连接...')
            try:
                # 初始化客户端
                if self.http_proxy:
                    httpx_client = httpx.Client(proxy=self.http_proxy)
                    client = OpenAI(http_client=httpx_client, base_url=self.endpoint, api_key=self.key)
                else:
                    # 未配代理时强制不吃环境代理（trust_env=False）
                    httpx_client = httpx.Client(trust_env=False)
                    client = OpenAI(http_client=httpx_client, base_url=self.endpoint, api_key=self.key)

                # 限流等待
                self._wait_for_interval()
                self.last_request_time = time.time()

                # 发送测试请求
                completion = tracked_completion(client, **self._completion_kwargs(
                    model=self.model,
                    messages=[
                        {
                            'role': 'user',
                            'content': '你好，请回答：1+1 等于几？只回答数字。'
                        }
                    ],
                    max_tokens=200  # 增大以支持可能返回的 reasoning_content
                ))

                # 统一检查响应
                if completion.choices:
                    msg = completion.choices[0].message
                    if msg.content or getattr(msg, 'reasoning_content', None):
                        logger.info(f'{self.name} 连接检查成功')
                        return True

                logger.error(f'{self.name} 连接检查失败：未收到响应')
                return False

            except Exception as e:
                logger.error(f'{self.name} 连接检查失败：{e}')
                return False

class SiliconFlow(Tiku):

    def __init__(self, config_path: Optional[str] = None):
        """初始化硅基流动大模型题库."""
        super().__init__(config_path)
        self.name = '硅基流动大模型'
        self.last_request_time = None
        self._lock = threading.Lock()

    def _wait_for_interval(self):
        if self.last_request_time:
            interval = time.time() - self.last_request_time
            if interval < self.min_interval:
                sleep_time = self.min_interval - interval
                logger.debug(f"API请求间隔过短, 等待 {sleep_time} 秒")
                time.sleep(sleep_time)

    def _query(self, q_info: dict):
        with self._lock:
            return self._query_locked(q_info)

    def _query_locked(self, q_info: dict):
        def remove_md_json_wrapper(md_str):
            # 解析可能存在的JSON包装
            pattern = r'^\s*```(?:json)?\s*(.*?)\s*```\s*$'
            match = re.search(pattern, md_str, re.DOTALL)
            return match.group(1).strip() if match else md_str.strip()

        # 构造请求头
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        # 构造系统提示词
        system_prompt = ""
        if q_info['type'] == "single":
            system_prompt = "本题为单选题，请根据题目和选项选择唯一正确答案，输出的是选项的具体内容，而不是内容前的ABCD，并以JSON格式输出：示例回答：{\"Answer\": [\"正确选项内容\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
        elif q_info['type'] == 'multiple':
            system_prompt = "本题为多选题，请选择所有正确选项，输出的是选项的具体内容，而不是内容前的ABCD，以JSON格式输出：示例回答：{\"Answer\": [\"选项1\",\"选项2\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
        elif q_info['type'] == 'completion':
            system_prompt = "本题为填空题，请直接给出填空内容，以JSON格式输出：示例回答：{\"Answer\": [\"答案文本\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
        elif q_info['type'] == 'judgement':
            system_prompt = "本题为判断题，请回答'正确'或'错误'，以JSON格式输出：示例回答：{\"Answer\": [\"正确\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"

        # 构造请求体
        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": f"题目：{q_info['title']}\n选项：{q_info['options']}"
                }
            ],
            "stream": False,

            "max_tokens": 4096,

            "temperature": 0.7,
            "top_p": 0.7,
            "response_format": {"type": "text"}
        }

        self._wait_for_interval()

        try:
            usage_context = account_context()
            response = _net_send("POST",
                self.api_endpoint,
                headers=headers,
                json=payload,
                timeout=30
            )
            self.last_request_time = time.time()

            if response.status_code == 200:
                result = response.json()
                if usage_context:
                    record_response(result, usage_context)
                content = result['choices'][0]['message']['content']
                parsed = json.loads(remove_md_json_wrapper(content))
                return "\n".join(parsed['Answer']).strip()
            else:
                logger.error(f"API请求失败：{response.status_code} {response.text}")
                return None

        except Exception as e:
            logger.error(f"硅基流动API异常：{e}")
            return None

    def _ready_check(self) -> str:
        if not (self._conf.get('siliconflow_key') or '').strip():
            return ("未填写硅基流动 key（到 https://cloud.siliconflow.cn/account/ak 获取后"
                    "填进「参数设置 → 题库 → 硅基流动」）")
        return ""

    def _init_tiku(self):
        # 从配置文件读取参数
        self.api_endpoint = self._conf.get('siliconflow_endpoint', 'https://api.siliconflow.cn/v1/chat/completions')
        self.api_key = self._conf['siliconflow_key']

        self.model_name = self._conf.get('siliconflow_model', 'deepseek-ai/DeepSeek-V3')

        self.min_interval = int(self._conf.get('min_interval_seconds', 3))

    def check_llm_connection(self) -> bool:
        """
        检查硅基流动大模型连接是否可用
        发送一个简单的测试请求来验证 API 配置
        """
        with self._lock:
            logger.info(f'正在检查 {self.name} 连接...')
            try:
                headers = {
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json'
                }

                payload = {
                    'model': self.model_name,
                    'messages': [
                        {
                            'role': 'user',
                            'content': '你好，请回答：1+1 等于几？只回答数字。'
                        }
                    ],
                    'stream': False,
                    'max_tokens': 10,
                    'temperature': 0.7,
                    'top_p': 0.7,
                    'response_format': {'type': 'text'}
                }

                # 在测试 API 连接时同样执行节流校验
                self._wait_for_interval()

                # 出网强制直连（api/_net）：与答题路径 _query 保持一致，
                # 避免环境代理化石让「连接检查」误报失败
                usage_context = account_context()
                response = _net_send("POST",
                    self.api_endpoint,
                    headers=headers,
                    json=payload,
                    timeout=30
                )
                self.last_request_time = time.time()

                if response.status_code == 200:
                    result = response.json()
                    if usage_context:
                        record_response(result, usage_context)
                    if result.get('choices') and result['choices'][0]['message']['content']:
                        logger.info(f'{self.name} 连接检查成功')
                        return True
                    else:
                        logger.error(f'{self.name} 连接检查失败：未收到有效响应')
                        return False
                else:
                    logger.error(f'{self.name} 连接检查失败：{response.status_code} {response.text}')
                    return False

            except Exception as e:
                logger.error(f'{self.name} 连接检查失败：{e}')
                return False


class TikuManual(Tiku):
    _manual_lock = threading.Lock()
    is_manual = True

    def __init__(self, config_path: Optional[str] = None) -> None:
        """初始化手动题库实例."""
        super().__init__(config_path)
        self.name = '手动输入题库'
        self.default_mode = 'batch'
        self.skip_answer_validation = True

    @staticmethod
    def _extract_option_letters(ans: str) -> list[str]:
        cleaned = re.sub(r'[\s,，;；、]+', '', ans)
        if not cleaned or not re.fullmatch(r'[A-Za-z]+', cleaned):
            return []
        return [c.upper() for c in cleaned]

    def _init_tiku(self):
        self.default_mode = self._conf.get('manual_mode_default', 'batch').strip().lower()
        if self.default_mode not in ['batch', 'single']:
            self.default_mode = 'batch'

        self.separator = self._conf.get('manual_mode_separator', ';')
        if self.separator.lower() in ['\\n', 'newline', '换行']:
            self.separator = '\n'
        elif self.separator.lower() in ['space', '空格']:
            self.separator = ' '
        elif self.separator.lower() in ['tab', '制表符']:
            self.separator = '\t'

    @staticmethod
    def _safe_close_tqdm_bars():
        """安全地清除并关闭所有活动的 tqdm 进度条，防止私有属性变更引发异常."""
        try:
            from tqdm import tqdm
            if hasattr(tqdm, '_instances') and hasattr(tqdm._instances, '__iter__'):
                # 复制一份以防遍历时容器大小发生变化 (WeakSet/list)
                instances = list(tqdm._instances)
                for instance in instances:
                    try:
                        if hasattr(instance, 'leave'):
                            instance.leave = False
                        if hasattr(instance, 'clear'):
                            instance.clear()
                        if hasattr(instance, 'close'):
                            instance.close()
                    except Exception as ie:
                        logger.debug(f"清理单个 tqdm 实例失败: {ie}")
        except Exception as e:
            logger.debug(f"获取/清理 tqdm 实例列表失败: {e}")

    def _query(self, q_info: dict) -> Optional[str]:
        # 强行关闭清除所有当前活动的 tqdm 进度条
        self._safe_close_tqdm_bars()

        with self._manual_lock:
            ans = self._single_query(q_info)
        logger.debug("手动答题结束，冲刷缓存日志")
        return ans

    def _query_all(self, q_list: list[dict], query_delay: float = 0.0) -> list[Optional[str]]:
        # 强行关闭清除所有当前活动的 tqdm 进度条
        self._safe_close_tqdm_bars()

        with self._manual_lock:
            print(f"\n{'=' * 20} 手动输入题库 (共 {len(q_list)} 题) {'=' * 20}")
            if self.default_mode == 'batch':
                ans_list = self._batch_query_flow(q_list)
            else:
                ans_list = [self._single_query(q) for q in q_list]
        logger.debug("手动答题结束，冲刷缓存日志")
        return ans_list

    @staticmethod
    def _get_type_display(type_str: str) -> str:
        type_map = {
            'single': '单选题',
            'multiple': '多选题',
            'completion': '填空题',
            'judgement': '判断题'
        }
        return type_map.get(type_str, '其他类型')

    def _single_query(self, q: dict) -> Optional[str]:
        type_str = self._get_type_display(q['type'])
        if q['type'] in ['single', 'multiple'] and q.get('options'):
            options = q['options']
            parts = []
            if isinstance(options, str):
                parts = [o.strip() for o in options.split('\n') if o.strip()]
                if len(parts) <= 1:
                    from api.answer_check import cut
                    cut_parts = cut(options)
                    if cut_parts:
                        parts = cut_parts
            elif isinstance(options, list):
                parts = [str(o).strip() for o in options if str(o).strip()]

            options_text = "  ".join(parts)
            print(f"\n【{type_str}】 {q['title']} 选项: {options_text}")
        elif q['type'] == 'judgement':
            print(f"\n【{type_str}】 {q['title']} 选项: 正确 / 错误")
        else:
            print(f"\n【{type_str}】 {q['title']}")

        while True:
            ans = input("请输入答案 (直接回车表示跳过/无答案): ").strip()
            if not ans:
                print(f"  [已记录] 题目: {q['title']} ---> 答案: [跳过]")
                return None

            # 即时校验
            ok, err_msg = self._validate_user_input(ans, q)
            if not ok:
                print(f"  \033[31m[输入错误] {err_msg}\033[0m")
                continue

            normalized_ans = self._normalize_user_input(ans, q)
            print(f"  [已记录] 题目: {q['title']} ---> 答案: {normalized_ans}")
            return normalized_ans

    def _validate_user_input(self, ans: str, q: dict) -> tuple[bool, str]:
        """
        验证用户手动输入的答案是否合规.
        """
        if not ans:
            return True, ""

        ans = ans.strip()
        if not ans:
            return True, ""

        q_type = q.get('type')
        if q_type == 'judgement':
            return self._validate_judgement_input(ans)
        elif q_type in ['single', 'multiple']:
            return self._validate_choice_input(ans, q)
        return True, ""

    def _validate_judgement_input(self, ans: str) -> tuple[bool, str]:
        """验证判断题手动输入是否合规."""
        val = ans.lower()
        valid_judgements = [
            'true', 't', '1', '对', '正确', '√', '是', 'yes', 'y',
            'false', 'f', '0', '错', '错误', '×', '否', 'no', 'n', '不对', '不正确'
        ]
        if val not in valid_judgements:
            return False, f"无法识别的判断词 '{ans}'，请输入：对/错、正确/错误、T/F、1/0"
        return True, ""

    def _validate_choice_input(self, ans: str, q: dict) -> tuple[bool, str]:
        """
        验证选择题手动输入是否合规.
        """
        options = q.get('options', '')
        parts = self._parse_options(options)
        valid_keys = self._extract_valid_keys(parts)

        if not valid_keys:
            return True, ""

        letters = self._extract_option_letters(ans)
        if not letters:
            return self._validate_text_match(ans, parts)

        invalid_letters = [letter for letter in letters if letter not in valid_keys]
        if invalid_letters:
            return False, f"输入包含无效的选项字母 {invalid_letters}，当前题目的可用选项为: {', '.join(valid_keys)}"

        if q.get('type') == 'single' and len(letters) > 1:
            return False, "当前是单选题，但输入了多个选项字母！"

        return True, ""

    def _parse_options(self, options) -> list[str]:
        """
        解析选项.
        """
        parts = []
        if isinstance(options, str):
            parts = [o.strip() for o in options.split('\n') if o.strip()]
            if len(parts) <= 1:
                from api.answer_check import cut
                cut_parts = cut(options)
                if cut_parts:
                    parts = cut_parts
        elif isinstance(options, list):
            parts = [str(o).strip() for o in options if str(o).strip()]
        return parts

    def _extract_valid_keys(self, parts: list[str]) -> list[str]:
        """
        提取合法的选项字母.
        """
        valid_keys = []
        for p in parts:
            first_char = p[:1].upper()
            if first_char.isalpha():
                valid_keys.append(first_char)
        return valid_keys

    def _validate_text_match(self, ans: str, parts: list[str]) -> tuple[bool, str]:
        """
        验证用户输入的文本是否和选项文本匹配.
        """
        from api.answer_check import cut
        split_ans = cut(ans)
        if split_ans:
            for item in split_ans:
                matched = False
                for p in parts:
                    p_norm = re.sub(r'^[A-Za-z]\s*[.、:：)?）]?\s*', '', p).strip().lower()
                    if item.strip().lower() in p_norm or p_norm in item.strip().lower():
                        matched = True
                        break
                if not matched:
                    return False, f"输入的文本 '{item}' 在所有选项中均无法匹配，请输入合法的选项文本或字母"
        return True, ""

    def _normalize_user_input(self, ans: str, q: dict) -> Optional[str]:
        """
        规整化用户的手动输入答案.
        """
        if not ans:
            return None

        ans = ans.strip()
        if not ans:
            return None

        q_type = q.get('type')
        if q_type == 'judgement':
            return self._normalize_judgement_input(ans)
        elif q_type in ['single', 'multiple']:
            return self._normalize_choice_input(ans, q)
        return ans

    def _normalize_judgement_input(self, ans: str) -> str:
        """
        规整化判断题的手动输入.
        """
        val = ans.lower()
        if val in ['true', 't', '1', '对', '正确', '√', '是', 'yes', 'y']:
            return "正确"
        elif val in ['false', 'f', '0', '错', '错误', '×', '否', 'no', 'n', '不对', '不正确']:
            return "错误"
        return ans

    def _normalize_choice_input(self, ans: str, q: dict) -> str:
        """
        规整化选择题的手动输入.
        """
        options = q.get('options', '')
        parts = self._parse_options(options)
        valid_keys = self._extract_valid_keys(parts)

        letters = self._extract_option_letters(ans)
        if letters and all(letter in valid_keys for letter in letters):
            unique_ordered_letters = []
            for letter in letters:
                if letter not in unique_ordered_letters:
                    unique_ordered_letters.append(letter)
            return "\n".join(unique_ordered_letters)

        from api.answer_check import cut
        split_ans = cut(ans)
        if split_ans:
            return "\n".join(split_ans)
        return ans

    def _batch_query_flow(self, q_list: list[dict]) -> list[Optional[str]]:
        """
        执行批量手动搜题交互.
        """
        self._print_batch_questions(q_list)

        sep_desc = self.separator
        if self.separator == '\n':
            sep_desc = '换行 (每题一行)'
        elif self.separator == ' ':
            sep_desc = '空格'
        elif self.separator == '\t':
            sep_desc = 'Tab制表符'

        self._print_batch_instructions(sep_desc)

        while True:
            answers = []
            if self.separator == '\n':
                print(f"请直接粘贴或依次输入各题答案（每行一个，共 {len(q_list)} 行）：")
                for i in range(len(q_list)):
                    try:
                        ans = input(f"  第 {i + 1} 题答案: ").strip()
                    except EOFError:
                        ans = ""
                    answers.append(ans)
            else:
                raw_input = input(f"\n请一次性输入所有题目的答案 (使用 '{sep_desc}' 分割): ").strip()
                answers = self._split_batch_answers(raw_input, len(q_list))

            has_error, temp_answers = self._parse_and_validate_batch(q_list, answers)

            if has_error:
                print("\033[31m检测到存在不合规的答案，已拒绝确认，请重新输入！\033[0m")
                continue

            confirm = input("确认使用上述答案？[Y/n]: ").strip().lower()
            if confirm in ['', 'y', 'yes']:
                return temp_answers
            elif confirm == 'switch':
                return [self._single_query(q) for q in q_list]
            else:
                print("已取消，请重新输入，或输入 'switch' 切换为单题输入模式。")

    def _print_batch_questions(self, q_list: list[dict]) -> None:
        """批量打印题目内容及选项."""
        for idx, q in enumerate(q_list):
            type_str = self._get_type_display(q['type'])
            if q['type'] in ['single', 'multiple'] and q.get('options'):
                options = q['options']
                parts = []
                if isinstance(options, str):
                    parts = [o.strip() for o in options.split('\n') if o.strip()]
                    if len(parts) <= 1:
                        from api.answer_check import cut
                        cut_parts = cut(options)
                        if cut_parts:
                            parts = cut_parts
                elif isinstance(options, list):
                    parts = [str(o).strip() for o in options if str(o).strip()]

                options_text = "  ".join(parts)
                print(f"\n[{idx + 1}] 【{type_str}】 {q['title']} 选项: {options_text}")
            elif q['type'] == 'judgement':
                print(f"\n[{idx + 1}] 【{type_str}】 {q['title']} 选项: 正确 / 错误")
            else:
                print(f"\n[{idx + 1}] 【{type_str}】 {q['title']}")

    def _print_batch_instructions(self, sep_desc: str) -> None:
        """打印批量输入的使用引导说明."""
        print("\n" + "=" * 50)
        print("请依次输入每道题的答案。")
        print(f"格式要求：当前配置要求使用【{sep_desc}】分割各题的答案。")
        print("如果是多选题，答案中的多个选项直接连着写即可（例如：AB 或 AC）。")
        print("直接按回车或输入空格跳过的题，对应的答案将为空（不作答）。")
        if self.separator == '\n':
            print("粘贴多行时，每行会被解析为对应一题的答案。")
        else:
            print(f"示例输入: A{self.separator} B{self.separator} 正确{self.separator} 答案1, 答案2{self.separator} 错")
        print("=" * 50)

    def _split_batch_answers(self, raw_input: str, expected_len: int) -> list[str]:
        """根据配置的分割符将批量的答案进行分拆和补齐."""
        if not raw_input:
            return [''] * expected_len

        if self.separator in [';', '；']:
            raw_input = raw_input.replace('；', ';')
            answers = [ans.strip() for ans in raw_input.split(';')]
        elif self.separator in [',', '，']:
            raw_input = raw_input.replace('，', ',')
            answers = [ans.strip() for ans in raw_input.split(',')]
        else:
            answers = [ans.strip() for ans in raw_input.split(self.separator)]

        if len(answers) < expected_len:
            answers.extend([''] * (expected_len - len(answers)))
        elif len(answers) > expected_len:
            answers = answers[:expected_len]
        return answers

    def _parse_and_validate_batch(self, q_list: list[dict], answers: list[str]) -> tuple[bool, list[Optional[str]]]:
        """批量解析用户输入并进行合法性校验."""
        print("\n--- 解析答案结果 ---")
        has_error = False
        temp_answers = []
        for idx, (q, ans) in enumerate(zip(q_list, answers)):
            ok, err_msg = self._validate_user_input(ans, q)
            if not ok:
                has_error = True
                print(f"第 {idx + 1} 题: {q['title']} ---> \033[31m[错误: {err_msg}]\033[0m")
                temp_answers.append(None)
            else:
                normalized_ans = self._normalize_user_input(ans, q)
                temp_answers.append(normalized_ans)
                print(f"第 {idx + 1} 题: {q['title']} ---> 答案: {normalized_ans if normalized_ans else '[跳过]'}")
        print("-------------------")
        return has_error, temp_answers


class TikuLocal(Tiku):
    """云端共享题库来源；保留 TikuLocal 配置名兼容旧版。"""

    def __init__(self, config_path: Optional[str] = None) -> None:
        super().__init__(config_path)
        self.name = '云端共享题库'

    def _init_tiku(self):
        logger.info('云端共享题库已启用（使用乐课登录状态）')

    def _query(self, q_info: dict) -> Optional[str]:
        # 基类 query/query_all 已查询云端题库；未命中返回 None 供下一来源回退。
        return None


class DummyTiku(Tiku):
    def __init__(self, config_path: Optional[str] = None) -> None:
        """初始化空题库."""
        super().__init__(config_path)
        self.name = '空/禁用题库'
        self.DISABLE = True

    def _query(self, q_info: dict) -> Optional[str]:
        return None


PROVIDER_REGISTRY = {
    'TikuYanxi': TikuYanxi,
    'TikuGo': TikuGo,
    'TikuLike': TikuLike,
    'TikuAdapter': TikuAdapter,
    'AI': AI,
    'SiliconFlow': SiliconFlow,
    'TikuLocal': TikuLocal,
    'TikuManual': TikuManual,
}
