"""Small explicit assistant intents; drafts never write until confirmed by the user."""
import re
from datetime import datetime, timedelta
from html import escape
from uuid import uuid4


_WEEKDAYS = ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')
_DAY_WORDS = (r'понедельник(?:ам)?', r'вторник(?:ам)?', r'сред(?:у|ам|ы)', r'четверг(?:ам)?', r'пятниц(?:у|ам|ы)', r'суббот(?:у|ам|ы)', r'воскресень(?:е|ям|я)')
_MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')
_MONTH_DATE = r'\b(\d{1,2})\s+(' + '|'.join(_MONTHS) + r')(?:\s+(\d{4})(?:\s+года)?)?\b'


def _split_content(text):
    """Explicit '..., что ...' separates notification slots from event content."""
    match = re.search(r'(?:,\s*|\s+)(?:что|чтобы)\s+', text, re.I)
    if match:
        return text[:match.start()], text[match.end():]
    # The event may precede the command: 'Встреча ..., напомни в 12:00'.
    reverse = re.search(r'[,;.\n]\s*(?:а\s+)?(напомни\b.*)$', text, re.I)
    if reverse and not re.match(r'\s*(?:напомни|создай|сделай|добавь|запиши)\b', text, re.I):
        return reverse.group(1), text[:reverse.start()].strip()
    # A clock preceding the separator is explicitly the notification clock.
    for separator in re.finditer(r',\s*|\s+о\s+', text, re.I):
        if re.search(r'\d{1,2}:\d{2}', text[:separator.start()]):
            return text[:separator.start()], text[separator.end():]
    return text, None


def _creation_match(text):
    header, _ = _split_content(text)
    return re.search(r'\b(?:создай|создать|сделай|составь|запиши|добавь)\b.*?\b(?:заметк[ауи]|напоминание|задач[ауи])\b|^(?:напомни|уведоми(?:те)?)\b', header, re.I)


def _one_edit(word, candidate):
    if word == candidate:
        return True
    if abs(len(word) - len(candidate)) > 1:
        return False
    if len(word) == len(candidate):
        positions = [i for i, (a, b) in enumerate(zip(word, candidate)) if a != b]
        return len(positions) == 1 or (len(positions) == 2 and positions[1] == positions[0] + 1
            and word[positions[0]] == candidate[positions[1]] and word[positions[1]] == candidate[positions[0]])
    short, long = sorted((word, candidate), key=len)
    return any(long[:i] + long[i+1:] == short for i in range(len(long)))


def _schedule_text(text):
    """Normalize only scheduling syntax; never guess or change numeric values."""
    text = re.sub(r'(\d)(?=[а-яА-ЯёЁ])|([а-яА-ЯёЁ])(?=\d)', lambda m: m.group() + ' ', text)
    from app.services.date_vocabulary import MONTH_ALIASES, RELATIVE_ALIASES, WEEKDAY_ALIASES
    text = re.sub(r'\bпосле\s+(?:завтра|завра|завтпа|завтро)\b', 'послезавтра', text, flags=re.I)
    text = re.sub(r'\b([а-яё]+)в(?=\s*\d)', lambda m: RELATIVE_ALIASES.get(m.group(1).lower(), m.group(1)) + ' в ' if m.group(1).lower() in RELATIVE_ALIASES else m.group(), text, flags=re.I)
    text = re.sub(r'(\b\d{1,2}\s+)([а-яё]{1,})\b', lambda m: m.group(1) + MONTH_ALIASES.get(m.group(2).lower(), m.group(2)), text, flags=re.I)
    text = re.sub(r'\b[а-яё]+\b', lambda m: RELATIVE_ALIASES.get(m.group().lower(), m.group()), text, flags=re.I)
    text = re.sub(r'(\b(?:каждый|каждую|каждое|по|в)\s+)([а-яёa-z]+)\b', lambda m: m.group(1) + WEEKDAY_ALIASES.get(m.group(2).lower(), m.group(2)), text, flags=re.I)
    text = re.sub(r'(\d)\s*:\s*(\d)', r'\1:\2', text)
    text = re.sub(r'\b(в|на)\s+(\d{1,2})[.,](\d{2})(?!\d|[.,]\d)', r'\1 \2:\3', text, flags=re.I)
    def correct(match, candidates):
        word = match.group(2).casefold().replace('ё', 'е')
        found = [candidate for candidate in candidates if _one_edit(word, candidate)]
        return match.group(1) + found[0] if len(found) == 1 else match.group()
    text = re.sub(r'(\b\d{1,2}\s+)([а-яё]{4,})\b', lambda m: correct(m, _MONTHS), text, flags=re.I)
    days = ('понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу', 'воскресенье')
    text = re.sub(r'(\b(?:каждый|каждую|каждое|по)\s+)([а-яё]{4,})\b', lambda m: correct(m, days), text, flags=re.I)
    return text


def _normalize_schedule_request(text):
    from .intent_router import normalize_action_words
    text = normalize_action_words(text)
    from .spoken_time import normalize_spoken_time
    text = normalize_spoken_time(text)
    header, content = _split_content(text)
    return _schedule_text(header) + (', что ' + content if content is not None else '')


def _parse_recurrence(text):
    grouped = re.search(r'\bпо (будням|выходным)\b', text)
    if grouped:
        if re.search(r'\b(?:кроме|до|раз|через|только|в течение)\b', text):
            return False, None
        return {'days': list(range(5)) if grouped.group(1) == 'будням' else [5, 6], 'frequency': 'WEEKLY'}, grouped.group()
    daily = re.search(r'\b(?:ежедневно|каждый день)\b', text)
    if daily:
        if re.search(r'\b(?:кроме|до|раз|через|месяц\w*|недел\w*|только|в течение)\b', text):
            return False, None
        return {'days': list(range(7)), 'frequency': 'DAILY'}, daily.group()
    weekly = re.search(r'\b(?:кажд(?:ый|ую|ое)|по)\s+(' + '|'.join(_DAY_WORDS) + r')\b', text)
    if weekly:
        # Unsupported extra intervals/days must never quietly become one weekday.
        suffix = text[weekly.end():]
        if re.search(r'\b(?:и|кроме|до|раз|через|только|в течение|недел\w*|месяц\w*|год\w*)\b', suffix):
            return False, None
        weekday = next(i for i, word in enumerate(_DAY_WORDS) if re.fullmatch(word, weekly.group(1)))
        return {'days': [weekday], 'frequency': 'WEEKLY'}, weekly.group()
    if re.search(r'\b(?:кажд\w*|еженедельно|ежемесячно|ежегодно|раз в)\b', text):
        return False, None
    return None, None


def _rrule(recurrence, stamp):
    value = 'FREQ=' + recurrence['frequency']
    if recurrence['frequency'] == 'WEEKLY':
        value += ';BYDAY=' + ','.join(_WEEKDAYS[d] for d in recurrence['days'])
    return value + ';BYHOUR=' + str(stamp.hour) + ';BYMINUTE=' + str(stamp.minute)


def validate_recurrence(rule, stamp):
    """Restricted RRULE maps losslessly to the scheduler weekday/time tables."""
    if not isinstance(rule, str) or len(rule) > 128 or not stamp:
        raise ValueError('Некорректное правило повторения.')
    try:
        parts = rule.split(';')
        pairs = [part.split('=') for part in parts]
        if any(len(pair) != 2 for pair in pairs):
            raise ValueError()
        fields = dict(pairs)
        if len(fields) != len(parts):
            raise ValueError()
        value = datetime.fromisoformat(stamp)
        if fields.get('FREQ') == 'DAILY' and set(fields) == {'FREQ', 'BYHOUR', 'BYMINUTE'}:
            days = list(range(7))
        elif fields.get('FREQ') == 'WEEKLY' and set(fields) == {'FREQ', 'BYDAY', 'BYHOUR', 'BYMINUTE'}:
            codes = fields['BYDAY'].split(',')
            days = [_WEEKDAYS.index(code) for code in codes]
            if not days or len(days) != len(set(days)):
                raise ValueError()
        else:
            raise ValueError()
        if not re.fullmatch(r'\d{1,2}', fields['BYHOUR']) or not re.fullmatch(r'\d{1,2}', fields['BYMINUTE']):
            raise ValueError()
        if value.second or value.microsecond or value.tzinfo or int(fields['BYHOUR']) != value.hour or int(fields['BYMINUTE']) != value.minute or value.weekday() not in days:
            raise ValueError()
        return days
    except (ValueError, TypeError):
        raise ValueError('Правило повторения не соответствует дате и времени.') from None


def validate_tool_call(call):
    """Validate model-produced calls without executing them or accepting extra fields."""
    if not isinstance(call, dict) or set(call) != {'name', 'arguments'} or call['name'] not in ('create_task', 'create_reminder'):
        raise ValueError('Неизвестный инструмент.')
    args = call['arguments']
    if not isinstance(args, dict) or set(args) != {'title', 'date', 'time', 'recurrence', 'timezone'}:
        raise ValueError('Некорректные параметры инструмента.')
    if not isinstance(args['title'], str) or not args['title'].strip() or len(args['title']) > 100:
        raise ValueError('Укажите название задачи.')
    from app.utils.timezones import zone
    if not isinstance(args['timezone'], str):
        raise ValueError('Укажите часовой пояс.')
    zone(args['timezone'])
    if call['name'] == 'create_task':
        if any(args[key] is not None for key in ('date', 'time', 'recurrence')):
            raise ValueError('Для напоминания используйте create_reminder.')
    else:
        if not isinstance(args['date'], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', args['date']) or not isinstance(args['time'], str) or not re.fullmatch(r'\d{2}:\d{2}', args['time']):
            raise ValueError('Укажите дату и время.')
        stamp = datetime.fromisoformat(args['date'] + 'T' + args['time'])
        if args['recurrence'] is not None:
            validate_recurrence(args['recurrence'], stamp.isoformat())
    # Preserve the draft handler's legacy error messages and tool allowlist;
    # the shared model boundary additionally enforces exact types and limits.
    from app.services.tool_schema import validate_tool_call as validate_schema_call
    return validate_schema_call(call)


def recurrence_label(rule):
    if not rule:
        return 'Без повторения'
    try:
        fields = dict(part.split('=') for part in rule.split(';'))
        if fields['FREQ'] == 'DAILY':
            label = 'Каждый день'
        elif fields['FREQ'] == 'WEEKLY':
            codes = fields['BYDAY'].split(',')
            names = ('понедельникам', 'вторникам', 'средам', 'четвергам', 'пятницам', 'субботам', 'воскресеньям')
            label = 'По будням' if codes == list(_WEEKDAYS[:5]) else 'По выходным' if codes == list(_WEEKDAYS[5:]) else 'По ' + ', '.join(names[_WEEKDAYS.index(code)] for code in codes)
        else:
            return 'Проверьте повторение в настройках'
        if 'BYHOUR' in fields and 'BYMINUTE' in fields:
            label += f" в {int(fields['BYHOUR']):02d}:{int(fields['BYMINUTE']):02d}"
        return label
    except (ValueError, KeyError, AttributeError):
        return 'Проверьте повторение в настройках'


TOOL_DEFINITIONS = [{'type': 'function', 'function': {'name': name, 'description': 'Prepare a draft; saving requires explicit user confirmation.', 'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['title', 'date', 'time', 'recurrence', 'timezone'], 'properties': {'title': {'type': 'string', 'minLength': 1, 'maxLength': 100}, 'date': {'type': ['string', 'null'], 'pattern': '^\\d{4}-\\d{2}-\\d{2}$'}, 'time': {'type': ['string', 'null'], 'pattern': '^\\d{2}:\\d{2}$'}, 'recurrence': {'type': ['string', 'null'], 'pattern': r'^FREQ=(?:DAILY|WEEKLY;BYDAY=(?:MO|TU|WE|TH|FR|SA|SU)(?:,(?:MO|TU|WE|TH|FR|SA|SU))*);BYHOUR=\d{1,2};BYMINUTE=\d{1,2}$'}, 'timezone': {'type': 'string'}}}}} for name in ('create_task', 'create_reminder')]


def _reply(text, **extra):
    return dict(text=text, sources=[], engine_label='Помощник заметок', retrieval='local_intent', status='answered', **extra)


def intent_response(query, workspace_id, history=None, now=None):
    now = now or datetime.now()
    text = ' '.join(query.strip().split())
    text = _normalize_schedule_request(text)
    text = re.sub(r'^уведоми(?:те)?\s+(?:меня\s+)?', 'Напомни ', text, flags=re.I)
    normalized = text.casefold().replace('ё', 'е')
    if re.search(r'\b(?:utc|gmt|часов\w* пояс|по .{0,30}времени)\b', normalized) and re.search(r'\b(?:напомни|создай|сделай|добавь|запиши)\b', normalized):
        return _reply('Уточните дату и время в часовом поясе устройства. Явное указание другого часового пояса пока не поддерживается; не буду заменять его местным временем.')
    if re.search(r'\b(?:как|где)\s+(?:мне\s+)?(?:создать|сделать|добавить|изменить|удалить)\s+(?:заметку|напоминание)', normalized):
        return _reply('Создайте заметку кнопкой + или Ctrl+N, введите название и текст — они сохраняются автоматически. Под заметкой нажмите «Настроить расписание», выберите дату и время и сохраните. Или попросите меня: «Создай заметку позвонить врачу завтра в 10:00».')
    if re.search(r'\bкак\b.*\b(?:начать|вести|пользоваться|работать)\b.*\b(?:заметк\w*|приложени\w*)\b', normalized):
        return _reply('Начните с одной заметки: нажмите + или Ctrl+N, введите название и текст. Всё сохраняется автоматически. Разделите дела по папкам с помощью + рядом с подписью папки. Для важных задач нажмите «Настроить расписание» под заметкой и выберите дату и время. В «Мой день» добавляйте то, чем хотите заняться сегодня. Мне можно написать «создай заметку позвонить врачу завтра в 10:00» — сначала покажу черновик.')
    if re.search(r'\bне\s+(?:создавай|создай|создать|делай|сделай|добавляй|добавь|записывай|запиши|напоминай|напомни|уведоми(?:те)?)\b', normalized):
        return _reply('Хорошо, ничего не создаю. Можем просто обсудить идею или найти нужную запись.')
    if re.fullmatch(r'(привет|здравствуй(?:те)?|добрый (?:день|вечер)|доброе утро|хай|hello|hi)[!.,\s]*', normalized):
        return _reply('Привет! Помогу найти записи, обсудить идею или подготовить заметку. Например: «Создай заметку позвонить врачу завтра в 10:00». Перед сохранением покажу черновик.')
    if re.fullmatch(r'(спасибо|благодарю)[!.,\s]*', normalized):
        return _reply('Пожалуйста! Что ещё нужно записать или разобрать?')
    if re.fullmatch(r'(что (?:ты )?умеешь|как (?:тебя|тобой|пользоваться приложением)|помощь|help|как пользоваться)[?!.,\s]*', normalized):
        return _reply('Создавайте заметки кнопкой + или Ctrl+N — текст сохраняется автоматически. Папку назначает отдельная кнопка + рядом с её названием. Напоминания настраиваются под заметкой. Мне можно написать «найди запись о поездке» или «создай заметку купить лекарства завтра в 18:30». Для свободного разговора подключите локальную модель через «Настройки ИИ».')
    creation = _creation_match(text)
    combined = text
    if not creation:
        # Only resume our own immediately pending clarification in the same workspace.
        scoped = [t for t in (history or []) if isinstance(t, dict) and t.get('workspace_id') == workspace_id]
        if len(scoped) >= 2 and scoped[-1].get('role') == 'assistant' and any(marker in scoped[-1].get('content', scoped[-1].get('text', '')) for marker in ('Уточните', 'Подготовил черновик')):
            if re.fullmatch(r'(?:не понял|не поняла|не понимаю)[.!?]*', normalized):
                return _reply('Уточните время уведомления — когда напомнить: например «напомнить мне надо в 12:00». Время самого события можно оставить в тексте заметки. Сначала покажу черновик, затем его нужно подтвердить кнопкой «Сохранить в заметки».')
            for previous in reversed(scoped[:-1]):
                value = previous.get('content', previous.get('text', ''))
                value = _normalize_schedule_request(value)
                if previous.get('role') == 'user' and _creation_match(value):
                    clarification=scoped[-1].get('content',scoped[-1].get('text','')).casefold()
                    # Replace only a slot-only correction explicitly requested by us.
                    # Free prose remains a continuation; it must not silently erase dates.
                    correction = re.fullmatch(r'(?:(?:напомнить|напомни|напоминание)\s*)?(?:мне\s*)?(?:(?:надо|нужно)\s*)?(?:(?:в|на)\s*)?(\d{1,2}:\d{2})[.!]?', normalized)
                    if ('время' in clarification or 'когда' in clarification or 'черновик' in clarification or 'уведомление' in clarification) and correction:
                        header, content = _split_content(value)
                        header = re.sub(r'\b(?:(?:в|на)\s*)?\d{1,2}:\d{2}\b', '', header)
                        combined = header + ' в ' + correction.group(1) + (', что ' + content if content is not None else '')
                        creation = True
                        break
                    date_slot = r'(?:сегодня|завтра|послезавтра|\d{1,2}[./]\d{1,2}[./]\d{4}|' + _MONTH_DATE + r')'
                    date_correction = re.fullmatch(r'(?:на\s+)?' + date_slot + r'(?:\s+(?:(?:в|на)\s*)?\d{1,2}:\d{2})?[.!]?', normalized)
                    if date_correction:
                        header, content = _split_content(value)
                        header = re.sub(r'\b(?:на\s+)?(?:сегодня|завтра|послезавтра|\d{1,2}[./]\d{1,2}(?:[./]\d{4})?)\b', '', header, flags=re.I)
                        header = re.sub(r'(?:\bна\s+)?' + _MONTH_DATE, '', header, flags=re.I)
                        if re.search(r'\d{1,2}:\d{2}', normalized):
                            header = re.sub(r'\b(?:(?:в|на)\s*)?\d{1,2}:\d{2}\b', '', header)
                        combined = header + ' ' + text + (', что ' + content if content is not None else '')
                        creation = True
                        break
                    if 'дат' in clarification and re.fullmatch(r'(?:на\s+)?(?:сегодня|завтра|послезавтра|\d{1,2}[./]\d{1,2}[./]\d{4})(?:\s+(?:в\s*)?\d{1,2}:\d{2})?[.!]?', normalized):
                        value=re.sub(r'\b(?:на\s+)?(?:сегодня|завтра|послезавтра|\d{1,2}[./]\d{1,2}(?:[./]\d{4})?)\b','',value,flags=re.I)
                        if re.search(r'\d{1,2}:\d{2}',normalized):value=re.sub(r'\b(?:в\s*)?\d{1,2}:\d{2}\b','',value)
                    combined = value + ' ' + text
                    creation = True
                    break
        if not creation:
            if re.fullmatch(r'[а-яa-z]{1,4}', normalized) and normalized in ('фыв','asdf','йцу','абв'):
                return _reply('Не понял запрос. Напишите, что хотите записать, найти или обсудить — например «создай заметку купить хлеб завтра в 19:00».')
            return None
    schedule_text, explicit_body = _split_content(combined)
    n = schedule_text.casefold().replace('ё', 'е')
    if len(set(re.findall(r'\b(?:сегодня|завтра|послезавтра)\b', n)))>1:
        return _reply('Уточните одну дату напоминания: сегодня, завтра или конкретную дату с годом.')
    recurrence, recurrence_span = _parse_recurrence(n)
    if recurrence is False:
        return _reply('Уточните правило повторения: поддерживаются каждый день и конкретные дни недели. Интервалы, месяцы и ограничение числа повторов пока не поддерживаются.')
    for clock in re.finditer(r'\b(\d+):(\d+)\b', n):
        hour, minute = map(int, clock.groups())
        if hour >= 24 or minute >= 60:
            reason = 'в часе 60 минут' if minute >= 60 else 'часы должны быть от 00 до 23'
            return _reply('Время [' + clock.group() + '] указано неверно (' + reason + '). Уточните, пожалуйста, точное время.')
        if len(clock.group(2)) != 2 or len(clock.group(1)) > 2:
            return _reply('Уточните время в формате ЧЧ:ММ, например «14:00».')
    periods = {'утром': '09:00', 'днем': '13:00', 'вечером': '18:00', 'ночью': '23:00'}
    found_periods = set(re.findall(r'\b(?:утром|днем|вечером|ночью)\b', n))
    if not re.search(r'\d+:\d+', n) and len(found_periods) > 1:
        return _reply('Уточните одно время уведомления: указаны разные части суток.')
    if not re.search(r'\d+:\d+', n) and found_periods:
        n += ' в ' + periods[next(iter(found_periods))]
    n = re.sub(r'\b(?:утром|днем|вечером|ночью)\b', '', n)
    if re.search(r'через \d+|следующ\w*|после обеда', n):
        return _reply('Уточните конкретную дату и время в формате «завтра в 10:00» или «15.12.2027 в 18:30». Повторяющееся расписание можно настроить в редакторе заметки.')
    dates = list(re.finditer(r'\b(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?\b', n))
    named_dates = list(re.finditer(_MONTH_DATE, n))
    notification_requested = bool(re.search(r'\bнапоминание\b|^напомни\b|\d{1,2}:\d{2}', n))
    if notification_requested and not recurrence and not dates and not named_dates and not re.search(r'\b(?:сегодня|завтра|послезавтра)\b', n) and explicit_body:
        event = _schedule_text(explicit_body).casefold().replace('ё', 'е')
        event_dates = list(re.finditer(r'\b(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?\b', event))
        event_named_dates = list(re.finditer(_MONTH_DATE, event))
        event_relative = set(re.findall(r'\b(?:сегодня|завтра|послезавтра)\b', event))
        if len(event_dates) + len(event_named_dates) + len(event_relative) > 1:
            return _reply('Уточните дату уведомления: в тексте указано несколько дат. Напишите, например «напомни 15 октября в 12:00, что …».')
        if event_dates or event_named_dates or event_relative:
            # Only borrow the event DAY. Its clocks remain untouched in the body.
            event_day = (event_dates or event_named_dates)[0].group() if event_dates or event_named_dates else next(iter(event_relative))
            n += ' ' + event_day
            dates = list(re.finditer(r'\b(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?\b', n))
            named_dates = list(re.finditer(_MONTH_DATE, n))
    if len(dates) + len(named_dates)>1 or ((dates or named_dates) and re.search(r'\b(?:сегодня|завтра|послезавтра)\b', n)):
        return _reply('Уточните одну дату напоминания: в запросе указаны разные даты.')
    date_match = dates[0] if dates else None
    date = None
    try:
        if re.search(r'\bвчера\b', n):
            return _reply('Уточните будущую дату: вчера уже прошло.')
        if date_match:
            day, month, year = date_match.groups()
            if not year:
                return _reply('Уточните год: напишите полную дату, например «15.12.2027 в 18:30».')
            date = datetime(int(year), int(month), int(day)).date()
        elif named_dates:
            day, month, year = named_dates[0].groups()
            date = datetime(int(year) if year else now.year, _MONTHS.index(month) + 1, int(day)).date()
        elif 'послезавтра' in n: date = (now + timedelta(days=2)).date()
        elif re.search(r'\bзавтра\b', n): date = (now + timedelta(days=1)).date()
        elif re.search(r'\bсегодня\b', n): date = now.date()
    except ValueError:
        return _reply('Уточните дату: такой даты нет в календаре. Например «15.12.2027 в 18:30».')
    times = list(re.finditer(r'\b(?:в\s*)?(\d{1,2}):(\d{2})\b', n))
    if len(times) > 1:
        return _reply('Уточните время уведомления: например «напомнить мне надо в 12:00». Время самого события отделите словами «, что …»: «напомни завтра в 12:00, что приём в 15:00».')
    wants_reminder = bool(recurrence or date or times or 'напоминание' in n or n.startswith('напомни') or re.search(r'\b(?:на|в)\s+\d', n))
    if recurrence and date:
        return _reply('Уточните повторение без отдельной даты начала: отложенный старт повторов пока не поддерживается.')
    if recurrence and times:
        try:
            hour, minute = map(int, times[0].groups())
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            for offset in range(8):
                first = candidate + timedelta(days=offset)
                if first.weekday() in recurrence['days'] and first > now:
                    date = first.date()
                    break
        except ValueError:
            return _reply('Уточните время от 00:00 до 23:59.')
    if not date and not recurrence and re.search(r'\b(?:понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье|день|неделе|месяце)\b', n):
        return _reply('Уточните конкретную дату и время напоминания, например «15.12.2027 в 18:30».')
    if wants_reminder and (not date or not times):
        if recurrence and not times:
            return _reply('Уточните время повторяющегося напоминания, например «в 10:00».')
        return _reply('Уточните '+('дату и время' if not date and not times else 'дату' if not date else 'время')+' напоминания. Например «завтра в 10:00».')
    stamp = None
    if date:
        try:
            hour, minute = map(int, times[0].groups())
            stamp = datetime.combine(date, datetime.min.time()).replace(hour=hour, minute=minute)
        except ValueError:
            return _reply('Уточните время от 00:00 до 23:59.')
        if stamp <= now:
            return _reply('Уточните будущую дату и время: указанное время уже прошло.')
    body = re.sub(r'^.*?\b(?:заметк[ауи]|напоминание|задач[ауи])\b\s*[:—-]?\s*', '', schedule_text, count=1, flags=re.I)
    body = re.sub(r'^напомни\s+', '', body, flags=re.I)
    if recurrence_span:
        body = re.sub(re.escape(recurrence_span), '', body, flags=re.I)
    body = re.sub(r'\b(?:на\s+)?(?:послезавтра|завтра|сегодня)\b', '', body, flags=re.I)
    body = re.sub(r'\b(?:на\s+)?\d{1,2}[./]\d{1,2}(?:[./]\d{4})?\b', '', body)
    body = re.sub(r'(?:\bна\s+)?' + _MONTH_DATE, '', body, flags=re.I)
    body = re.sub(r'\b(?:в\s*)?\d{1,2}:\d{2}\b', '', body)
    body = ' '.join(body.strip(' ,.;:-').split())
    if explicit_body is not None:
        body = explicit_body.strip()
    if not body:
        prefix = re.search(r'мне\s+(?:надо|нужно)\s+(.+?)[,.;]\s*(?:создай|сделай|составь)', combined, re.I)
        if prefix: body = prefix.group(1).strip()
    if not body:
        return _reply('Уточните текст заметки: о чём нужно напомнить?')
    from app.services.note_titles import suggest_note_title
    draft = dict(draft_id=uuid4().hex, kind='create_note', title=suggest_note_title(body), body=body, once_at=stamp.isoformat(timespec='seconds') if stamp else None, workspace_id=workspace_id)
    from app.utils.timezones import device_zone
    draft['timezone_id'] = device_zone()
    draft['recurrence'] = _rrule(recurrence, stamp) if recurrence else None
    tool = {'name': 'create_reminder' if stamp else 'create_task', 'arguments': {'title': draft['title'], 'date': stamp.date().isoformat() if stamp else None, 'time': stamp.strftime('%H:%M') if stamp else None, 'recurrence': draft['recurrence'], 'timezone': draft['timezone_id']}}
    validate_tool_call(tool)
    preview = ' Уведомление: ' + stamp.strftime('%d.%m.%Y в %H:%M') + '.' if stamp else ''
    button = 'Сохранить в заметки'
    return _reply('Подготовил черновик.' + preview + ' Проверьте текст и нажмите «' + button + '». До подтверждения ничего не сохранено.', action_draft=draft, tool_calls=[tool])


def commit_action_draft(store, draft, now=None):
    """Validate again and commit note + reminder + duplicate receipt in one transaction."""
    now = now or datetime.now()
    if not isinstance(draft, dict) or draft.get('kind') != 'create_note': raise ValueError('Неизвестное действие.')
    key = draft.get('draft_id')
    if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{32}', key): raise ValueError('Некорректный черновик.')
    title, body, workspace = draft.get('title'), draft.get('body'), draft.get('workspace_id')
    if not isinstance(title, str) or not isinstance(body, str) or not (title.strip() or body.strip()) or len(title)>500 or len(body)>100000 or type(workspace) is not int or workspace not in (1, 2): raise ValueError('Проверьте текст и пространство заметки.')
    stamp = draft.get('once_at')
    if stamp:
        try:
            value = datetime.fromisoformat(stamp)
            if value.tzinfo is not None or value <= now: raise ValueError()
            stamp = value.isoformat(timespec='seconds')
        except (ValueError, TypeError): raise ValueError('Укажите будущую дату и местное время.')
    recurrence = draft.get('recurrence')
    days = validate_recurrence(recurrence, stamp) if recurrence else []
    from app.utils.timezones import device_zone, zone, utc_stamp
    name = draft.get('timezone_id') or device_zone()
    zone(name)
    if store.db.in_transaction: raise ValueError('Дождитесь сохранения заметок.')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        store.db.execute('CREATE TABLE IF NOT EXISTS assistant_action_receipts(draft_id TEXT PRIMARY KEY,note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL)')
        receipt = store.db.execute('SELECT note_id FROM assistant_action_receipts WHERE draft_id=?', (key,)).fetchone()
        if receipt:
            if receipt[0] is None: raise ValueError('Этот черновик уже был создан и удалён. Подготовьте новый.')
            return receipt[0]
        current = now.isoformat(timespec='seconds')
        html = '<p>'+escape(body).replace('\n','<br>')+'</p>'
        note_id = store.db.execute('INSERT INTO notes(title,body,workspace_id,created_at,updated_at) VALUES (?,?,?,?,?)', (title.strip(),html,workspace,current,current)).lastrowid
        if stamp:
            reminder_id = store.db.execute("INSERT INTO reminders(note_id,mode,once_at,created_at,timezone_id,once_utc,created_utc) VALUES (?,'once',?,?,?,?,?)", (note_id,stamp,current,name,utc_stamp(datetime.fromisoformat(stamp),name),utc_stamp(datetime.fromisoformat(current)))).lastrowid
            if recurrence:
                store.db.execute("UPDATE reminders SET mode='repeat',once_at=NULL,once_utc=NULL WHERE id=?", (reminder_id,))
                store.db.executemany('INSERT INTO reminder_days VALUES (?,?)', [(reminder_id, day) for day in days])
                store.db.execute('INSERT INTO reminder_times VALUES (?,?)', (reminder_id, datetime.fromisoformat(stamp).strftime('%H:%M')))
        store.db.execute('INSERT INTO assistant_action_receipts VALUES (?,?)', (key,note_id))
        return note_id
