"""在临时源码副本中串行运行桌面回归，不修改真实配置或账号。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = [
    "_selftest_core.py", "_test_boot.py", "_test_login_dialog.py",
    "_test_login_robust.py", "_test_multiacct.py", "_test_platform_accounts.py",
    "_test_chapters.py", "_test_autopass.py", "_test_knowledge.py",
    "_test_knowledge_privacy.py", "_test_workgrade.py", "_test_video_mode.py",
    "_test_vmode_gui.py", "_test_runprogress.py", "_test_profile.py",
    "_test_unipus.py", "_test_yuketang.py", "_test_yuketang_exercise.py",
    "_test_yuketang_gui.py", "_test_zhihuishu.py", "_test_live_act.py",
]


def main():
    output = ROOT / "_reg_logs" / "chain_audit"
    output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONUTF8": "1", "QT_QPA_PLATFORM": "offscreen"}
    results = []
    with tempfile.TemporaryDirectory(prefix="leke_audit_") as tmp:
        work = Path(tmp)
        for directory in ("api", "gui_app", "tests", "resource", "assets"):
            shutil.copytree(ROOT / directory, work / directory,
                            ignore=shutil.ignore_patterns("__pycache__"))
        for path in ROOT.glob("*.py"):
            shutil.copy2(path, work / path.name)
        shutil.copy2(ROOT / "config_template.ini", work / "config_template.ini")
        for name in SCRIPTS:
            # Each script receives a fresh template and no real account files.
            shutil.copy2(ROOT / "config_template.ini", work / "config.ini")
            for pattern in ("*accounts.json", "gui_settings.json", "cookies*.txt"):
                for path in work.glob(pattern):
                    path.unlink()
            log = output / (Path(name).stem + ".log")
            with log.open("w", encoding="utf-8") as fp:
                try:
                    proc = subprocess.run(
                        [sys.executable, "-u", str(work / "tests" / "regression" / name)],
                        cwd=work, env=env, stdout=fp, stderr=subprocess.STDOUT, timeout=120,
                    )
                    code = proc.returncode
                except subprocess.TimeoutExpired:
                    code = "timeout"
            results.append({"script": name, "exit_code": code, "log": str(log)})
            print(f"{'PASS' if code == 0 else 'FAIL'} {name}: {code}", flush=True)
            (output / "summary.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return int(any(item["exit_code"] != 0 for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
