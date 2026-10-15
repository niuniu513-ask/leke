# 第三方源码声明 —— 智慧树引擎

本目录（`api/zhihuishu/`）内的以下文件为第三方开源代码的 vendor 副本：

| 本目录文件 | 上游文件 | 说明 |
|---|---|---|
| `client.py` | `fucker.py` | 智慧树学习引擎主体（含本地适配改动） |
| `zd_utils.py` | `zd_utils.py` | AES 加解密 / watchPoint / ev 签名 |
| `sign.py` | `sign.py` | 请求签名 |
| `objdict.py` | `ObjDict.py` | 属性字典 |
| `utils.py` | `utils.py` | 终端工具函数（已做无终端兜底） |
| `_logging.py` | `logger.py` | 日志（**已重写**为桥接乐课统一日志） |

- 上游项目：<https://github.com/Boger0319/fuckZHS>
- 作者：VermiIIi0n
- 许可证：MIT（见同目录 `LICENSE`）
- 上游最后提交：2022-06（本仓库引入时间为 2026-10）

## 乐课做的适配改动（不属于上游）

1. **导入改为包内相对导入**（`from .zd_utils import ...`），使其成为正规子包，
   PyInstaller 打包时可用 hidden-import 精确收集。
2. **`os.get_terminal_size()` 兜底**：无控制台（noconsole exe）下该调用会抛
   `OSError`，已改为安全取宽（失败回退 80 列）。
3. **默认禁用系统代理**：原版 `proxies or urllib.request.getproxies()` 会自动采用
   系统代理，遇到「代理化石」（环境变量残留指向已关闭的端口）会让全部请求失败；
   改为默认 `{}`，仅在显式传入时才使用代理。
4. **日志不落盘**：原版 `logger = MonoLogger(path="./logs")` 会在导入时创建
   `./logs` 目录并写 5 个文件；已重写为桥接 `api.logger`。
5. 未引入上游的 `main.py` / `decrypt/` / `mirai/`（CLI、反混淆示例、机器人部署）。

上游代码版权归原作者所有，本项目仅作个人学习用途。
