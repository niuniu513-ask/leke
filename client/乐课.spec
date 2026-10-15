# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('D:/学习通/chaoxing-main/config_template.ini', '.'), ('D:/学习通/chaoxing-main/resource', 'resource'), ('D:/学习通/chaoxing-main/THIRD_PARTY_NOTICES.md', '.'), ('D:/学习通/chaoxing-main/licenses', 'licenses')]
binaries = []
hiddenimports = ['main', 'api', 'api.base', 'api.answer', 'api.decode', 'api.process', 'api.notification', 'api.live', 'api.live_process', 'api.cxsecret_font', 'api.font_decoder', 'api.captcha', 'api.cookies', 'api.logger', 'gui_app', 'gui_app.app', 'gui_app.main_window', 'gui_app.worker', 'gui_app.study_engine', 'gui_app.store', 'gui_app.stream', 'gui_app.theme', 'gui_app.widgets', 'gui_app.page_account', 'gui_app.page_courses', 'gui_app.page_settings', 'gui_app.page_run', 'gui_app.page_ai', 'gui_app.ai_panel', 'gui_app.ai_providers', 'gui_app.ai_icons', 'api.unipus', 'api.unipus.unipus_engine', 'api.unipus.audio_recognizer', 'gui_app.unipus_engine', 'gui_app.page_unipus', 'api.yuketang', 'api.yuketang.client', 'api.yuketang._vendor_qrcode', 'api.zhihuishu', 'api.zhihuishu.client', 'api.zhihuishu.zd_utils', 'api.zhihuishu.sign', 'api.zhihuishu.objdict', 'api.zhihuishu.utils', 'api.zhihuishu._logging', 'gui_app.zhihuishu_engine', 'gui_app.page_zhihuishu', 'api.kc_client', 'gui_app.login_dialog', 'gui_app.gate', 'gui_app.brand', 'gui_app.page_profile', 'api.cx_live', 'api.ykt_live', 'gui_app.page_live', 'pyaes', 'websocket', 'selenium', 'selenium.webdriver', 'openai', 'PySide6.QtSvg']
hiddenimports += collect_submodules('api.yuketang')
hiddenimports += collect_submodules('api.zhihuishu')
hiddenimports += collect_submodules('api.unipus')
hiddenimports += collect_submodules('selenium')
tmp_ret = collect_all('ddddocr')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('cv2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('fontTools')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('loguru')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tqdm')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('faster_whisper')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('ctranslate2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('av')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tokenizers')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['D:/学习通/chaoxing-main/乐课.py'],
    pathex=['D:/学习通/chaoxing-main'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'bandit', 'pip_audit'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='乐课',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['D:/学习通/chaoxing-main/resource/leke.ico'],
)
