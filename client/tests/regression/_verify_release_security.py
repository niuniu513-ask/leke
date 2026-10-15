"""Check a release archive for accidental local secrets and development modules."""
import configparser
import fnmatch
import json
import sys
from pathlib import PurePosixPath
from PyInstaller.archive.readers import CArchiveReader


def check(path):
    archive = CArchiveReader(path)
    failures = []
    patterns = ('accounts.json', '*_accounts.json', 'cookies*.txt', 'gui_settings.json',
                'config.ini', 'secret.key', 'le.pem', '.env', '*.db', '*.sqlite', '*.log')
    for name in archive.toc:
        normalized = name.replace('\\', '/')
        p = PurePosixPath(normalized)
        if (any(fnmatch.fnmatch(p.name.lower(), pattern) for pattern in patterns)
                or any(part in {'kc_server', '.token_usage_pending', 'rain_profiles', 'unipus_profiles'}
                       for part in p.parts)):
            failures.append(normalized)
    modules = archive.open_embedded_archive('PYZ.pyz').toc
    for module in modules:
        if module.split('.')[0] in {'pytest', 'bandit', 'pip_audit'}:
            failures.append(module)
    template_key = next(k for k in archive.toc if k.replace('\\', '/') == 'config_template.ini')
    parser = configparser.RawConfigParser()
    parser.read_string(archive.extract(template_key).decode('utf-8-sig'))
    for section in parser.sections():
        for key, value in parser.items(section):
            placeholder = section == 'common' and key == 'password' and value.strip() == 'xxx'
            if key.lower() in {'password', 'key', 'token', 'tokens', 'adapter_key'} and value.strip() and not placeholder:
                failures.append(f'non-empty template credential: {section}.{key}')
    for required in ['gui_app.credential_protection', 'gui_app.gate', 'api.kc_client']:
        if required not in modules:
            failures.append('missing ' + required)
    result = {'passed': not failures, 'failures': failures, 'archive_items': len(archive.toc),
              'modules': len(modules), 'scope': 'packaging hygiene; not proof against reverse engineering'}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(check(sys.argv[1]))
