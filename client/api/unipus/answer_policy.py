"""U校园统一策略：匹配题库，未命中调用模型，失败不作答。"""
import hashlib
import json
import re


def question_key(question, context=''):
    def option(o):
        return {'letter': getattr(o, 'letter', ''), 'text': getattr(o, 'text', str(o))}
    payload = {'platform': 'unipus', 'type': question.q_type.name, 'text': question.text,
               'options': [option(o) for o in question.options],
               'banked': question.banked_options, 'directions': question.directions,
               'context': context, 'number': question.number}
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return 'unipus:' + digest


def cached_answer(key):
    try:
        from api.answer import get_cache_dao
        value = get_cache_dao().get_cache(key)
        return value.strip() if isinstance(value, str) else ''
    except Exception:
        return ''


def remember_answer(key, answer):
    try:
        from api.answer import get_cache_dao
        get_cache_dao().add_cache(key, answer, verified=False)
    except Exception:
        pass


def choice_answer(answer, letters, multiple=False):
    """Validate an explicit answer, never pick a default option from prose."""
    answer = str(answer or '').strip().upper()
    pattern = r'[A-Z](?:[\s,、，;]*[A-Z])*' if multiple else r'[A-Z]'
    if not re.fullmatch(pattern, answer):
        return ''
    selected = re.findall('[A-Z]', answer)
    if not selected or any(x not in letters for x in selected):
        return ''
    return ''.join(dict.fromkeys(selected))
