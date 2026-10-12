"""Normalize unambiguous Russian clock phrases without guessing AM/PM.

Unmarked numbers in ordinary prose remain untouched. Invalid clock values are
rendered as numeric times so the reminder validator can request clarification.
"""
import re

_UNITS = dict(zip('ноль один два три четыре пять шесть семь восемь девять десять одиннадцать двенадцать тринадцать четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать'.split(), range(20)))
_UNITS.update({'нуль': 0, 'одну': 1, 'две': 2})
_TENS = {'двадцать': 20, 'тридцать': 30, 'сорок': 40, 'пятьдесят': 50, 'шестьдесят': 60, 'семьдесят': 70, 'восемьдесят': 80, 'девяносто': 90}
_UNIT_WORDS = '|'.join(sorted(_UNITS, key=len, reverse=True))
_TEN_WORDS = '|'.join(sorted(_TENS, key=len, reverse=True))
_SMALL_WORDS = '|'.join(word for word, value in _UNITS.items() if 0 < value < 10)
_WORD_NUMBER = rf'(?:(?:{_TEN_WORDS})(?:\s+(?:{_SMALL_WORDS}))?|(?:{_UNIT_WORDS}))'
_NUMBER = rf'(?:\d{{1,2}}|{_WORD_NUMBER})'
_MINUTE = rf'(?:(?:ноль|нуль)\s+(?:ноль|нуль)|{_NUMBER})'


def _number(value):
    value = value.casefold()
    if value.isdigit():
        return int(value)
    words = value.split()
    if len(words) == 1:
        return {**_UNITS, **_TENS}.get(words[0])
    if len(words) == 2 and all(_UNITS.get(word) == 0 for word in words):
        return 0
    if len(words) == 2 and words[0] in _TENS and 0 < _UNITS.get(words[1], 99) < 10:
        return _TENS[words[0]] + _UNITS[words[1]]
    return None


def _clock(match):
    hour = _number(match.group('h'))
    minute = _number(match.group('m')) if match.groupdict().get('m') else 0
    if hour is None or minute is None:
        return match.group()
    return f'{match.groupdict().get("prefix", "")}{hour:02d}:{minute:02d}'


def normalize_spoken_time(text):
    # STT may render clock punctuation as a dot or separate two-digit minutes.
    # Never consume part of a three-digit group ("14 000") or a money amount.
    quantity = r'(?!\s+(?:руб\w*|копе\w*|евро|доллар\w*|яблок\w*|штук\w*|номер\w*|процент\w*)\b)'
    numeric = rf'\b(?P<prefix>в\s+|(?:сегодня|завтра|послезавтра)\s+(?:в\s+)?)(?P<h>\d{{1,2}})(?P<separator>\.|\s+)(?P<m>\d{{2}})(?!\d){quantity}'
    def numeric_clock(match):
        if match.group('separator') != '.':
            before = text[:match.start()]
            temporal = re.search(r'\b(?:напом\w*|напомин\w*|завтра|сегодня|послезавтра|\d{1,2}\s+\w*(?:ября|варя|враля|марта|апреля|мая|июня|июля|августа))\b', before + match.group('prefix'), re.I)
            if not temporal:
                return match.group()
        return _clock(match)
    text = re.sub(numeric, numeric_clock, text, flags=re.I)
    # Explicit units are authoritative, including out-of-range times.
    explicit = rf'\b(?P<prefix>в\s+)(?P<h>{_NUMBER})\s+час(?:а|ов)?(?:\s+(?P<m>{_NUMBER})\s+минут(?:а|ы)?)?\b'
    text = re.sub(explicit, _clock, text, flags=re.I)
    # Two clock components after "в" or an adjacent relative date. Require
    # spoken hours: bare digit pairs can be quantities or identifiers.
    pair = rf'\b(?P<prefix>(?:в\s+|(?:сегодня|завтра|послезавтра)\s+(?:в\s+)?))(?P<h>(?>{_WORD_NUMBER}))\s+(?P<m>(?>{_MINUTE}))\b(?!\s+(?:руб\w*|копе\w*|евро|доллар\w*|яблок\w*|штук\w*|номер\w*|процент\w*)\b)'
    text = re.sub(pair, _clock, text, flags=re.I)
    standalone = re.fullmatch(rf'\s*(?P<h>(?>{_WORD_NUMBER}))\s+(?P<m>(?>{_MINUTE}))\s*[.!]?\s*', text, flags=re.I)
    if standalone:
        return _clock(standalone)
    # An hour without minutes needs an adjacent date anchor. Never infer
    # afternoon from "в два" or interpret counts like "в два магазина".
    single = rf'\b(?P<prefix>(?:сегодня|завтра|послезавтра)\s+в\s+)(?P<h>(?>{_WORD_NUMBER}))\b(?=\s*(?:$|[,.!?;]|(?:купить|позвонить|сходить|проверить|отправить|встретиться|забрать|сделать|оплатить|записаться|прийти|приехать|посетить)\b))'
    return re.sub(single, _clock, text, flags=re.I)





