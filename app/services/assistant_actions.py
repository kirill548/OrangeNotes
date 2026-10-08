"""Small explicit assistant intents; drafts never write until confirmed by the user."""
import re
from datetime import datetime, timedelta
from html import escape
from uuid import uuid4


def _reply(text, **extra):
    return dict(text=text, sources=[], engine_label='Помощник заметок', retrieval='local_intent', status='answered', **extra)


def intent_response(query, workspace_id, history=None, now=None):
    now = now or datetime.now()
    text = ' '.join(query.strip().split())
    normalized = text.casefold().replace('ё', 'е')
    if re.search(r'\b(?:как|где)\s+(?:мне\s+)?(?:создать|сделать|добавить|изменить|удалить)\s+(?:заметку|напоминание)', normalized):
        return _reply('Создайте заметку кнопкой + или Ctrl+N, введите название и текст — они сохраняются автоматически. Под заметкой нажмите «Настроить расписание», выберите дату и время и сохраните. Или попросите меня: «Создай заметку позвонить врачу завтра в 10:00».')
    if re.search(r'\bкак\b.*\b(?:начать|вести|пользоваться|работать)\b.*\b(?:заметк\w*|приложени\w*)\b', normalized):
        return _reply('Начните с одной заметки: нажмите + или Ctrl+N, введите название и текст. Всё сохраняется автоматически. Разделите дела по папкам с помощью + рядом с подписью папки. Для важных задач нажмите «Настроить расписание» под заметкой и выберите дату и время. В «Мой день» добавляйте то, чем хотите заняться сегодня. Мне можно написать «создай заметку позвонить врачу завтра в 10:00» — сначала покажу черновик.')
    if re.search(r'\bне\s+(?:создавай|создай|создать|делай|сделай|добавляй|добавь|записывай|запиши)\b', normalized):
        return _reply('Хорошо, ничего не создаю. Можем просто обсудить идею или найти нужную запись.')
    if re.fullmatch(r'(привет|здравствуй(?:те)?|добрый (?:день|вечер)|доброе утро|хай|hello|hi)[!.,\s]*', normalized):
        return _reply('Привет! Помогу найти записи, обсудить идею или подготовить заметку. Например: «Создай заметку позвонить врачу завтра в 10:00». Перед сохранением покажу черновик.')
    if re.fullmatch(r'(спасибо|благодарю)[!.,\s]*', normalized):
        return _reply('Пожалуйста! Что ещё нужно записать или разобрать?')
    if re.fullmatch(r'(что (?:ты )?умеешь|как (?:тебя|тобой|пользоваться приложением)|помощь|help|как пользоваться)[?!.,\s]*', normalized):
        return _reply('Создавайте заметки кнопкой + или Ctrl+N — текст сохраняется автоматически. Папку назначает отдельная кнопка + рядом с её названием. Напоминания настраиваются под заметкой. Мне можно написать «найди запись о поездке» или «создай заметку купить лекарства завтра в 18:30». Для свободного разговора подключите локальную модель через «Подключение ИИ».')
    creation = re.search(r'\b(?:создай|создать|сделай|составь|запиши|добавь)\b.*?\b(?:заметк[ауи]|напоминание)\b', normalized)
    combined = text
    if not creation:
        # Only resume our own immediately pending clarification in the same workspace.
        scoped = [t for t in (history or []) if isinstance(t, dict) and t.get('workspace_id') == workspace_id]
        if len(scoped) >= 2 and scoped[-1].get('role') == 'assistant' and 'Уточните' in scoped[-1].get('content', scoped[-1].get('text', '')):
            for previous in reversed(scoped[:-1]):
                value = previous.get('content', previous.get('text', ''))
                if previous.get('role') == 'user' and re.search(r'(создай|сделай|создать|запиши|добавь|составь).*?(заметк|напоминание)', value.casefold()):
                    clarification=scoped[-1].get('content',scoped[-1].get('text','')).casefold()
                    # Replace only a slot-only correction explicitly requested by us.
                    # Free prose remains a continuation; it must not silently erase dates.
                    if 'время' in clarification and re.fullmatch(r'(?:в\s*)?\d{1,2}:\d{2}[.!]?', normalized):
                        value=re.sub(r'\b(?:в\s*)?\d{1,2}:\d{2}\b','',value)
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
    n = combined.casefold().replace('ё', 'е')
    if len(set(re.findall(r'\b(?:сегодня|завтра|послезавтра)\b', n)))>1:
        return _reply('Уточните одну дату напоминания: сегодня, завтра или конкретную дату с годом.')
    if re.search(r'ежедневно|кажд\w*|по (?:понедельникам|вторникам|средам)|через \d+|следующ\w*|вечером|утром|после обеда', n):
        return _reply('Уточните конкретную дату и время в формате «завтра в 10:00» или «15.12.2027 в 18:30». Повторяющееся расписание можно настроить в редакторе заметки.')
    dates = list(re.finditer(r'\b(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?\b', n))
    if len(dates)>1 or (dates and re.search(r'\b(?:сегодня|завтра|послезавтра)\b', n)):
        return _reply('Уточните одну дату напоминания: в запросе указаны разные даты.')
    date_match = dates[0] if dates else None
    date = None
    try:
        if date_match:
            day, month, year = date_match.groups()
            if not year:
                return _reply('Уточните год: напишите полную дату, например «15.12.2027 в 18:30».')
            date = datetime(int(year), int(month), int(day)).date()
        elif 'послезавтра' in n: date = (now + timedelta(days=2)).date()
        elif re.search(r'\bзавтра\b', n): date = (now + timedelta(days=1)).date()
        elif re.search(r'\bсегодня\b', n): date = now.date()
    except ValueError:
        return _reply('Уточните дату: такой даты нет в календаре. Например «15.12.2027 в 18:30».')
    times = list(re.finditer(r'\b(?:в\s*)?(\d{1,2}):(\d{2})\b', n))
    if len(times) > 1:
        return _reply('Уточните одно время для этого напоминания. Несколько времён можно добавить в редакторе расписания.')
    wants_reminder = bool(date or times or 'напоминание' in n or re.search(r'\b(?:на|в)\s+\d', n))
    if not date and re.search(r'\b(?:понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье|день|неделе|месяце)\b', n):
        return _reply('Уточните конкретную дату и время напоминания, например «15.12.2027 в 18:30».')
    if wants_reminder and (not date or not times):
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
    body = re.sub(r'^.*?\b(?:заметк[ауи]|напоминание)\b\s*[:—-]?\s*', '', combined, count=1, flags=re.I)
    body = re.sub(r'\b(?:на\s+)?(?:послезавтра|завтра|сегодня)\b', '', body, flags=re.I)
    body = re.sub(r'\b(?:на\s+)?\d{1,2}[./]\d{1,2}(?:[./]\d{4})?\b', '', body)
    body = re.sub(r'\b(?:в\s*)?\d{1,2}:\d{2}\b', '', body)
    body = ' '.join(body.strip(' ,.;:-').split())
    if not body:
        prefix = re.search(r'мне\s+(?:надо|нужно)\s+(.+?)[,.;]\s*(?:создай|сделай|составь)', combined, re.I)
        if prefix: body = prefix.group(1).strip()
    if not body:
        return _reply('Уточните текст заметки: о чём нужно напомнить?')
    draft = dict(draft_id=uuid4().hex, kind='create_note', title=body[:100], body=body, once_at=stamp.isoformat(timespec='seconds') if stamp else None, workspace_id=workspace_id)
    return _reply('Подготовил черновик. Проверьте текст'+(' и время напоминания' if stamp else '')+' и нажмите «Создать». До этого ничего не сохранено.', action_draft=draft)


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
            from app.utils.timezones import device_zone, utc_stamp
            name = device_zone()
            store.db.execute("INSERT INTO reminders(note_id,mode,once_at,created_at,timezone_id,once_utc,created_utc) VALUES (?,'once',?,?,?,?,?)", (note_id,stamp,current,name,utc_stamp(datetime.fromisoformat(stamp),name),utc_stamp(datetime.fromisoformat(current))))
        store.db.execute('INSERT INTO assistant_action_receipts VALUES (?,?)', (key,note_id))
        return note_id
