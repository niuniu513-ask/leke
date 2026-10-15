# -*- coding: utf-8 -*-
"""把 stdout / stderr 接管到 GUI。

所有日志（loguru -> tqdm.write）和 tqdm 进度条都写往 sys.stderr，
在 pythonw / 打包后 stderr 可能为 None 会直接抛异常，所以必须替换。
"""
from __future__ import annotations

import re
import sys
import threading

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_LEVEL = re.compile(r"\|\s*(TRACE|DEBUG|INFO|SUCCESS|WARNING|ERROR|CRITICAL)\s*\|")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def parse_level(text: str) -> str:
    m = _LEVEL.search(text)
    return m.group(1) if m else "INFO"


class StreamProxy:
    """给 tqdm / print 用的假流。

    分流规则：一个片段属于「日志」还是「进度条」，取决于引出它的那个控制字符 ——
    由 \\r 引出的片段是进度条（tqdm 用 \\r 回到行首重绘），由 \\n 引出的是普通日志行。
    用「终结符」判断是错的：tqdm 最后一帧后面跟的是 \\n，会被误判成日志。
    """

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, on_line, on_progress=None, echo=None):
        self.on_line = on_line
        self.on_progress = on_progress
        self.echo = echo
        self.buf = ""
        self._kind = "line"      # 当前缓冲区里的内容属于哪一类
        self._closed = False
        # 多线程刷课（并发 N 个章节线程 + tqdm 帧）会同时 write，
        # buf 拼接 / _kind 切换不加锁会产生竞态：日志行被误判成进度条帧
        # 丢进 progress 通道覆盖掉，或整段文本被并发 += 互相覆盖 —— 表现为
        # 「日志不完整 / 中间缺行」。所有缓冲操作必须在锁内完成。
        self._lock = threading.Lock()

    def _emit(self, seg: str, kind: str):
        seg = seg.strip()
        if not seg:
            return
        try:
            if kind == "progress":
                # 防粘连（真实丢日志机制）：\r 进度帧没有终结符，紧随其后的
                # \n 结尾日志会跟帧拼在同一段里。检测到日志头就把帧前缀与
                # 日志行拆开，日志必须进 line 通道，不能被进度帧覆盖掉。
                m = _LEVEL.search(seg)
                if m:
                    head, tail = seg[:m.start()].strip(), seg[m.start():].strip()
                    if head and self.on_progress is not None:
                        self.on_progress(head)
                    if tail and self.on_line is not None:
                        self.on_line(tail)
                    return
                if self.on_progress is not None:
                    self.on_progress(seg)
            else:
                self.on_line(seg)
        except Exception:
            pass

    def write(self, text):
        if self._closed or not text:
            return len(text or "")
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        # 取出完整片段后到锁外 emit：回调（Qt 信号 emit）可能较慢，
        # 持锁回调会把其他写线程全部堵死
        pending: list[tuple[str, str]] = []
        with self._lock:
            self.buf += text
            while True:
                i_n = self.buf.find("\n")
                i_r = self.buf.find("\r")
                if i_n == -1 and i_r == -1:
                    break
                if i_n != -1 and i_r != -1 and i_n == i_r + 1:
                    seg, self.buf = self.buf[:i_r], self.buf[i_n + 1:]
                    pending.append((seg, self._kind))
                    self._kind = "line"
                elif i_n != -1 and (i_r == -1 or i_n < i_r):
                    seg, self.buf = self.buf[:i_n], self.buf[i_n + 1:]
                    pending.append((seg, self._kind))
                    self._kind = "line"
                else:
                    seg, self.buf = self.buf[:i_r], self.buf[i_r + 1:]
                    pending.append((seg, self._kind))
                    self._kind = "progress"
        for seg, kind in pending:
            self._emit(seg, kind)
        return len(text)

    def flush(self):
        with self._lock:
            if not self.buf:
                return
            seg, kind, self.buf = self.buf, self._kind, ""
        self._emit(seg, kind)

    def isatty(self):
        return False

    def fileno(self):
        raise OSError("StreamProxy has no fileno")

    def close(self):
        self._closed = True

    def writable(self):
        return True

    def readable(self):
        return False

    def seekable(self):
        return False


_originals: dict[str, object] = {}
_on_line = None
_on_progress = None
_buffer: list[tuple[str, str]] = []
_BUFFER_MAX = 3000


def _dispatch(kind: str, text: str):
    handler = _on_line if kind == "line" else _on_progress
    if handler is not None:
        try:
            handler(text)
        except Exception:
            pass
    else:
        # 还没有接收方：先缓存，等界面就绪后回放
        if len(_buffer) < _BUFFER_MAX:
            _buffer.append((kind, text))


def install() -> None:
    """接管 stdout / stderr。

    必须在 import api.logger 之前调用 —— 该模块在导入时就把当时的 sys.stderr
    存成了模块级变量，之后替换就来不及了。
    接收方可以稍后用 set_handlers() 注册。
    """
    if not _originals:
        _originals["stdout"] = sys.stdout
        _originals["stderr"] = sys.stderr
    sys.stdout = StreamProxy(lambda t: _dispatch("line", t),
                             lambda t: _dispatch("progress", t))
    sys.stderr = StreamProxy(lambda t: _dispatch("line", t),
                             lambda t: _dispatch("progress", t))


def set_handlers(on_line, on_progress=None) -> None:
    """注册接收方，并回放安装期间积压的内容。"""
    global _on_line, _on_progress
    _on_line = on_line
    _on_progress = on_progress
    pending, _buffer[:] = list(_buffer), []
    for kind, text in pending:
        try:
            if kind == "line" and _on_line:
                _on_line(text)
            elif kind == "progress" and _on_progress:
                _on_progress(text)
        except Exception:
            pass


def uninstall() -> None:
    global _on_line, _on_progress
    if _originals:
        sys.stdout = _originals.get("stdout") or sys.__stdout__
        sys.stderr = _originals.get("stderr") or sys.__stderr__
    _on_line = None
    _on_progress = None

