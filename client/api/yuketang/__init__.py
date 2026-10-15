# -*- coding: utf-8 -*-
"""雨课堂（yuketang / 学堂在线）vendor 引擎包。

来源：MuWinds/yuketangHelperBUU（再上游 heyblackC/yuketangHelper、
Cat1007/yuketangHelperSCUTLite；二维码来自 lincolnloop/python-qrcode）。
上游仓库没有 LICENSE 文件，按「个人学习用途 + 注明出处」处理，
改动清单见同目录 NOTICE.md。

对外入口是 api.yuketang.client；本模块只做延迟再导出，不引入任何硬依赖，
因此 import api.yuketang 在缺 websocket / qrcode / PIL 的环境里也不会炸。
"""

__all__ = ["DEFAULT_DOMAIN", "YuketangClient", "YuketangError"]


def __getattr__(name):
    """延迟再导出（PEP 562），避免包初始化时就加载 requests / websocket / qrcode。"""
    if name in __all__:
        from . import client as _client

        return getattr(_client, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
