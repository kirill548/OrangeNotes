"""Conservative local titles: preserve the action, keep scheduling in the body."""
import re


def suggest_note_title(body):
    text = ' '.join(body.split()).strip()
    text = re.sub(r'^(?:что\s+)?(?:мне\s+)?(?:нужно|надо|необходимо)\s+', '', text, flags=re.I)
    text = re.sub(r'^у\s+меня\s+', '', text, flags=re.I)
    # A trailing clock with a preposition is scheduling, not an arbitrary ID.
    text = re.sub(r'\s+(?:в|на|к)\s+\d{1,2}:\d{2}\s*[.!]?$', '', text, flags=re.I)
    if re.fullmatch(r'\d{1,2}:\d{2}', text):
        return 'Напоминание'
    text = ' '.join(text.split()).strip(' ,.;:—-')
    # Match a whole, simple intent. Extra actions or negations must remain visible.
    date_tail = r'(?:\s+(?:на\s+)?(?:сегодня|завтра|послезавтра|\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)(?:\s+\d{4})?))?'
    visit = re.fullmatch(r'(?:сходить\s+|пойти\s+|записан[а]?\s+|(?:при[её]м|запись|визит)(?:\s+на\s+при[её]м)?\s+)?(?:на\s+при[её]м\s+)?к\s+(врачу|парикмахеру|стоматологу|терапевту|окулисту)' + date_tail, text, re.I)
    if visit:
        return 'Визит к ' + visit.group(1).lower()
    outing = re.fullmatch(r'(?:сходить|пойти|зайти)\s+(в|на)\s+(магазин|аптеку|банк|почту|рынок|работу|тренировку|собрание|встречу)' + date_tail, text, re.I)
    if outing:
        return 'Поход ' + outing.group(1).lower() + ' ' + outing.group(2).lower()
    # Keep people/topics verbatim; never turn preparation/cancellation into attendance.
    for verb, label in ((r'созвониться', 'Созвон'), (r'встретиться', 'Встреча')):
        matched = re.fullmatch(verb + r'\s+с\s+(.+)', text, re.I)
        if matched:
            text = label + ' с ' + matched.group(1)
            break
    meeting = re.fullmatch(r'(?:присутствовать|участвовать|принять участие)\s+(?:на|в)\s+(ВКС|созвоне|собрании|совещании|встрече)(\s+.*)?', text, re.I)
    if meeting:
        labels = {'вкс': 'ВКС', 'созвоне': 'Созвон', 'собрании': 'Собрание',
                  'совещании': 'Совещание', 'встрече': 'Встреча'}
        text = labels[meeting.group(1).lower()] + (meeting.group(2) or '')
    if not text:
        return 'Напоминание'
    if len(text) > 60:
        text = text[:57].rsplit(' ', 1)[0].rstrip(' ,.;:—-') + '…'
    return text
