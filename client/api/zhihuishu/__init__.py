"""智慧树（知到）自动学习引擎 —— vendor 包。

来源：https://github.com/Boger0319/fuckZHS （MIT License, © VermiIIi0n）
上游参考：luoyily/zhihuishu-tool、zhixiaobai/Python-zhihuishu

本包为**纯接口实现**（requests + AES），不依赖 selenium / 浏览器：
登录走 passport.zhihuishu.com 的账密校验，学习走 studyservice-api / hike 两套 API。
AES 走项目自带的纯 Python `pyaes`（见 zd_utils.py），零新增依赖。

对外只暴露 client.py 里的 `Fucker` 与 `TimeLimitExceeded`。
"""

from .client import Fucker, TimeLimitExceeded  # noqa: F401

__all__ = ["Fucker", "TimeLimitExceeded"]
