"""检查包内字节码与当前源码一致，防止修复未进入可执行文件。"""
from pathlib import Path
import sys
import types
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[2]
MODULES = {
    'api.log_privacy': 'api/log_privacy.py',
    'api.logger': 'api/logger.py',
    'gui_app.credential_protection': 'gui_app/credential_protection.py',
    'gui_app.store': 'gui_app/store.py',
    'gui_app.app': 'gui_app/app.py',
    'api.decode': 'api/decode.py',
    'gui_app.page_courses': 'gui_app/page_courses.py',
    'gui_app.page_run': 'gui_app/page_run.py',
    'gui_app.workspace_diagnostics': 'gui_app/workspace_diagnostics.py',
    'gui_app.page_account': 'gui_app/page_account.py',
    'gui_app.brand': 'gui_app/brand.py',
    'gui_app.icons': 'gui_app/icons.py',
    'gui_app.browser_workspace': 'gui_app/browser_workspace.py',
    'gui_app.page_unipus': 'gui_app/page_unipus.py',
    'gui_app.unipus_engine': 'gui_app/unipus_engine.py',
    'gui_app.main_window': 'gui_app/main_window.py',
    'gui_app.theme': 'gui_app/theme.py', 'gui_app.widgets': 'gui_app/widgets.py',
    'api.diagnostics': 'api/diagnostics.py', 'main': 'main.py',
    'gui_app.study_engine': 'gui_app/study_engine.py',
    'api.token_usage': 'api/token_usage.py', 'gui_app.gate': 'gui_app/gate.py',
    'gui_app.ai_panel': 'gui_app/ai_panel.py', 'api.yuketang.client': 'api/yuketang/client.py',
    'api.unipus.unipus_engine': 'api/unipus/unipus_engine.py',
    'api.answer': 'api/answer.py', 'api._net': 'api/_net.py',
    'api.base': 'api/base.py', 'api.answer_check': 'api/answer_check.py',
    'api.kc_client': 'api/kc_client.py', 'api.zhihuishu.client': 'api/zhihuishu/client.py',
    'gui_app': 'gui_app/__init__.py', 'gui_app.app': 'gui_app/app.py',
    'gui_app.page_settings': 'gui_app/page_settings.py', 'gui_app.worker': 'gui_app/worker.py',
    'api.yuketang.browser_login': 'api/yuketang/browser_login.py',
    'api.yuketang.standard_courses': 'api/yuketang/standard_courses.py',
    'api.unipus.login_input': 'api/unipus/login_input.py',
    'api.unipus.answer_policy': 'api/unipus/answer_policy.py',
    'api.unipus.audio_recognizer': 'api/unipus/audio_recognizer.py',
    'api.unipus.reference_knowledge': 'api/unipus/reference_knowledge.py',
    'api.work_answer_policy': 'api/work_answer_policy.py',
    'gui_app.yuketang_engine': 'gui_app/yuketang_engine.py',
    'gui_app.page_yuketang': 'gui_app/page_yuketang.py',
    'gui_app.page_live': 'gui_app/page_live.py',
    'api.cx_live': 'api/cx_live.py',
    'api.ykt_live': 'api/ykt_live.py',
}


def signature(code):
    return (code.co_code, code.co_names, code.co_varnames,
            tuple(signature(c) if isinstance(c, types.CodeType) else c for c in code.co_consts))


def main():
    archive = CArchiveReader(sys.argv[1]).open_embedded_archive('PYZ.pyz')
    failures = []
    for module, filename in MODULES.items():
        current = compile((ROOT / filename).read_text(encoding='utf-8'), filename, 'exec')
        same = signature(archive.extract(module)) == signature(current)
        print(('PASS ' if same else 'FAIL ') + module)
        if not same:
            failures.append(module)
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
