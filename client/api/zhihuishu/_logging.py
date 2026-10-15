"""vendor 日志桥接 —— 把 fuckZHS 的 logger 调用转发到乐课统一日志。

原实现 MonoLogger 会在**导入时**创建 ./logs 目录并挂 5 个 FileHandler，
GUI / PyInstaller 环境下会污染工作目录；这里改为桥接 api.logger（loguru 单例），
不落额外文件，调用接口与原版一致（debug/info/warning/error/critical/exception）。
"""

import logging

_fallback = logging.getLogger("leke.zhihuishu.vendor")
if not _fallback.handlers:
    _fallback.addHandler(logging.NullHandler())

try:  # 失败不阻断导入（单测环境可能不完整）
    from api.logger import logger as _proj
except Exception:
    _proj = None


class _BridgeLogger:
    """与 vendor 原 logger 同接口的转发器。"""

    def __init__(self):
        self._level = "DEBUG"

    @staticmethod
    def _text(msg, args) -> str:
        if not args:
            return str(msg)
        try:
            return str(msg) % args
        except Exception:
            return str(msg) + " " + " ".join(str(a) for a in args)

    def _emit(self, level, msg, *args, **kwargs):
        text = self._text(msg, args)
        try:
            if _proj is not None:
                getattr(_proj, level)(text)
            else:
                getattr(_fallback, level)(text)
        except Exception:
            pass

    def debug(self, msg, *a, **k):
        self._emit("debug", msg, *a, **k)

    def info(self, msg, *a, **k):
        self._emit("info", msg, *a, **k)

    def warning(self, msg, *a, **k):
        self._emit("warning", msg, *a, **k)

    def error(self, msg, *a, **k):
        self._emit("error", msg, *a, **k)

    def critical(self, msg, *a, **k):
        self._emit("critical", msg, *a, **k)

    def exception(self, msg, *a, **k):
        self._emit("error", msg, *a, **k)

    def setLevel(self, level):
        self._level = str(level).upper()

    def getLevel(self):
        return self._level


logger = _BridgeLogger()
