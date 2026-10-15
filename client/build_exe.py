# -*- coding: utf-8 -*-
"""打包脚本：把桌面版打成 Windows 可执行文件。

用法（在项目根目录）：
    .venv\\Scripts\\python.exe build_exe.py            # 默认 onedir，启动快、便于排查
    .venv\\Scripts\\python.exe build_exe.py --onefile  # 单个 exe，体积大、首启动慢

产物在 dist/ 目录下。

说明：
- chaoxing 依赖链里有 ddddocr / onnxruntime / opencv，都是带二进制和模型文件的包，
  必须用 --collect-all 收全，否则打包后验证码识别会失败。
- config_template.ini 与 resource/ 是运行期需要的数据文件，一并带上。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "乐课"
ENTRY = os.path.join(HERE, "乐课.py")

COLLECT_ALL = ["ddddocr", "onnxruntime", "cv2", "fontTools", "loguru", "tqdm", "faster_whisper", "ctranslate2", "av", "tokenizers"]
#: 整个子包都要收的（隐藏 import 只保证顶层模块进包，子包得显式 collect）
#: ⚠ selenium 必须整包 collect：webdriver/__init__.py 用 __getattr__ 动态懒加载
#:   子模块（webdriver.EdgeOptions() → selenium.webdriver.edge.options），
#:   PyInstaller 静态分析看不见，只列 selenium.webdriver 会漏掉 edge/chrome 等，
#:   打包后 U校园一点「连接」就报 No module named 'selenium.webdriver.edge.options'。
COLLECT_SUBMODULES = ["api.yuketang", "api.zhihuishu", "api.unipus", "selenium"]
HIDDEN = ["main", "api", "api.base", "api.answer", "api.decode", "api.process",
          "api.notification", "api.live", "api.live_process", "api.cxsecret_font",
          "api.font_decoder", "api.captcha", "api.cookies", "api.logger",
          "gui_app", "gui_app.app", "gui_app.main_window", "gui_app.worker",
          "gui_app.study_engine", "gui_app.store", "gui_app.stream", "gui_app.theme",
          "gui_app.widgets", "gui_app.page_account", "gui_app.page_courses",
          "gui_app.page_settings", "gui_app.page_run",
          # AI 配置页 + 配置面板（厂商目录 + 内嵌 SVG 图标）
          "gui_app.page_ai", "gui_app.ai_panel", "gui_app.ai_providers", "gui_app.ai_icons",
          # U校园（vendor 引擎 + Selenium 驱动 Edge + OpenAI 兼容客户端）
          "api.unipus", "api.unipus.unipus_engine", "api.unipus.audio_recognizer",
          "gui_app.unipus_engine", "gui_app.page_unipus",
          # 雨课堂（vendor 引擎 + 纯 Python 二维码兜底副本 + WebSocket 扫码登录）
          # ⚠ 这几个是**函数内延迟导入**的，PyInstaller 静态分析看不全，
          #   必须显式列出来，否则打包后点「扫码登录」才报 ModuleNotFoundError。
          "api.yuketang", "api.yuketang.client", "api.yuketang._vendor_qrcode",
          # 智慧树（vendor 引擎 + 纯接口刷课；AES 走项目自带的 pyaes，零新增依赖）
          # ⚠ client 用包内相对导入 + 函数内延迟导入 api.logger，必须显式列出。
          "api.zhihuishu", "api.zhihuishu.client", "api.zhihuishu.zd_utils",
          "api.zhihuishu.sign", "api.zhihuishu.objdict", "api.zhihuishu.utils",
          "api.zhihuishu._logging",
          "gui_app.zhihuishu_engine", "gui_app.page_zhihuishu",
          # 授权闸门（登录框 / 闸门 / kc 客户端均为函数内延迟导入，必须显式列出）
          "api.kc_client", "gui_app.login_dialog", "gui_app.gate",
          # 品牌图标（v1.6.1，app.py / main_window / login_dialog 里函数内延迟导入）
          "gui_app.brand",
          # 用户中心（v1.6.3，用户名 / 头像 / 设备，走 api.kc_client 的资料接口）
          "gui_app.page_profile",
          # 课堂互动（v1.6.25：学习通移动端协议 + 雨课堂课堂互动）
          # ⚠ api.ykt_live 是页面里**函数内延迟导入**的，静态分析看不见；
          #   api.cx_live 虽然是静态导入，一并列出避免裁剪时漏收（pyaes 纯 Python）。
          "api.cx_live", "api.ykt_live", "gui_app.page_live", "pyaes",
          "websocket",
          "selenium", "selenium.webdriver", "openai",
          "PySide6.QtSvg"]
ADD_DATA = [
    (os.path.join(HERE, "config_template.ini"), "."),
    (os.path.join(HERE, "resource"), "resource"),
    (os.path.join(HERE, "THIRD_PARTY_NOTICES.md"), "."),
    (os.path.join(HERE, "licenses"), "licenses"),
]
#: exe 图标（16→256 七档），由 kc_server/brand/gen_icons.py 生成后同步过来
ICON = os.path.join(HERE, "resource", "leke.ico")


def build(onefile: bool = False, noconsole: bool = True,
          distpath: str | None = None) -> int:
    dp = distpath or os.path.join(HERE, "dist")
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--noupx",
        "--exclude-module", "pytest",
        "--exclude-module", "bandit",
        "--exclude-module", "pip_audit",
        "--name", APP_NAME,
        "--onefile" if onefile else "--onedir",
        "--noconsole" if noconsole else "--console",
        "--distpath", dp,
        "--paths", HERE,
    ]
    if os.path.exists(ICON):
        args += ["--icon", ICON]
    else:
        print("⚠ 未找到图标 %s，将打包为默认图标" % ICON)
    for pkg in COLLECT_ALL:
        args += ["--collect-all", pkg]
    for mod in HIDDEN:
        args += ["--hidden-import", mod]
    for pkg in COLLECT_SUBMODULES:
        args += ["--collect-submodules", pkg]
    for src, dst in ADD_DATA:
        if os.path.exists(src):
            args += ["--add-data", f"{src}{os.pathsep}{dst}"]
    args.append(ENTRY)

    print("执行:", " ".join(args))
    # 不把 Codex/图像工具的同名 ICU 等 DLL 收入 Qt 应用。
    build_env = os.environ.copy()
    windows = os.environ.get("SystemRoot", r"C:\Windows")
    build_env["PATH"] = os.pathsep.join([
        os.path.dirname(sys.executable), windows,
        os.path.join(windows, "System32"),
    ])
    rc = subprocess.call(args, cwd=HERE, env=build_env)
    if rc == 0:
        out = os.path.join(dp, APP_NAME + '.exe' if onefile else APP_NAME)
        print("\n打包完成：", out)
        if not onefile:
            print("· 把整个 dist\\%s 目录拷给别人即可绿色运行" % APP_NAME)
    return rc


if __name__ == "__main__":
    onefile = "--onefile" in sys.argv
    keep_console = "--console" in sys.argv
    distpath = None
    for i, a in enumerate(sys.argv):
        if a == "--distpath" and i + 1 < len(sys.argv):
            distpath = sys.argv[i + 1]
    if not shutil.which("pyinstaller") and not os.path.isdir(
            os.path.join(HERE, ".venv", "Lib", "site-packages", "PyInstaller")):
        print("未检测到 PyInstaller，正在安装…")
        subprocess.call([sys.executable, "-m", "pip", "install", "pyinstaller",
                         "-i", "https://mirrors.aliyun.com/pypi/simple/"])
    raise SystemExit(build(onefile=onefile, noconsole=not keep_console,
                           distpath=distpath))
