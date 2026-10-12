"""Trusted local application help; note data never participates in routing."""
import re
from app.utils.shortcuts import shortcut_label

APP_HELP = {
    'synonyms': ('В окне помощника откройте «Параметры», затем нажмите «Синонимы». Добавьте группу слов через запятую, например «итерация, спринт». Словарь действует только в выбранном пространстве.', 'Настроить синонимы'),
    'workspaces': ('В Orange Notes доступны «Личное» и «Рабочее». Выберите пространство заметки в редакторе, а область поиска — в окне помощника. Произвольное создание новых пространств пока не поддерживается.', None),
    'attach_notes': ('В окне помощника добавьте заметку через панель прикреплённого контекста: найдите её по заголовку и выберите. Кнопка удаления на карточке убирает привязку, сохраняя заметку. Доступны только записи выбранного пространства.', 'Прикрепить заметки'),
    'quick_actions': ('Ctrl+K открывает быстрые действия. Ctrl+N создаёт заметку; Ctrl+F открывает поиск; Ctrl+S сохраняет; Ctrl+B и Ctrl+I включают жирный текст и курсив; Ctrl+Shift+D создаёт копию заметки.', 'Быстрые действия'),
    'ai_settings': ('Откройте «Настройки ИИ» в окне помощника. Можно подключить Ollama или локальный AI-pack; обычный поиск доступен без модели. Управляемые модели можно активировать и удалять в настройках AI-pack.', 'Подключение ИИ'),
    'reminders': ('Создайте заметку кнопкой + или Ctrl+N. Под заметкой нажмите «Настроить расписание», выберите дату, время и повторение. Также можно попросить помощника подготовить напоминание: перед сохранением он покажет черновик.', None),
}


def normalize_action_words(text):
    """Repair known dictation errors only after an explicit creation command."""
    return re.sub(
        r'(\b(?:сделай|создай|добавь|создать)\s+(?:мне\s+)?)на\s+поменание\b',
        r'\1напоминание', str(text), flags=re.I)


def classify_intent(query):
    """Return small_talk, app_help, tool_call or note_rag conservatively."""
    text = ' '.join(normalize_action_words(query).casefold().replace('ё', 'е').split())
    if re.fullmatch(r'(?:привет(?:[, ]+как дела)?|здравствуй(?:те)?|добрый (?:день|вечер)|доброе утро|хай|hello|hi|спасибо|благодарю|кто ты|как тебя зовут|что ты умеешь)[!?. ,]*', text):
        return 'small_talk'
    # Explicit recall wins over application vocabulary (e.g. a note on synonyms).
    if re.search(r'\b(?:что я|я записывал|я записала|в моих заметках|в заметке|найди|вспомни)\b', text):
        return 'note_rag'
    if re.search(r'^(?:как|где|почему|помощь|help|какие)\b', text) and re.search(r'синоним|пространств|прикреп|привяз|контекст|горяч\w* клавиш|быстр\w* действ|ctrl\+|настро\w* ии|подключ\w* ии|ai.pack|ollama|создать замет|напоминан|расписан|пользоваться прилож', text):
        return 'app_help'
    creation = re.search(r'\bнапомни\b', text) or re.search(r'\b(?:создай|создать|сделай|добавь|запиши|составь)\b.*\b(?:заметк\w*|задач\w*|напоминан\w*)\b', text)
    if creation and not re.search(r'\bне\s+(?:напомни|создай|создать|сделай|добавь|запиши)\b', text):
        return 'tool_call'
    return 'note_rag'


def conversational_response(query):
    intent = classify_intent(query)
    text = str(query).casefold().replace('ё', 'е')
    if re.search(r'сначала\s+уточни.*(?:хочу|добиться|цель)', text):
        return {'text': 'Чего вы хотите добиться в этой ситуации? Какой результат был бы для вас хорошим?', 'sources': [], 'engine_label': 'Помощник Orange Notes', 'retrieval': 'local_intent', 'status': 'answered', 'intent': 'clarification', 'help_actions': []}
    if intent == 'small_talk':
        if re.search('спасибо|благодарю', text):
            answer = 'Пожалуйста! Помогу записать задачу, настроить напоминание или найти нужную заметку.'
        elif re.search('кто ты|как тебя зовут|что ты умеешь', text):
            answer = 'Я помощник Orange Notes. Помогу разобраться в приложении, найти записи и подготовить задачи с напоминаниями. Изменения сохраняются только после вашего подтверждения.'
        else:
            answer = 'Привет! Что сделаем: найдём заметку, подготовим напоминание или разберёмся с приложением?'
        topic = None
    elif intent == 'app_help':
        topic = ('synonyms' if 'синоним' in text else 'workspaces' if 'пространств' in text else
                 'attach_notes' if re.search('прикреп|привяз|контекст', text) else
                 'quick_actions' if re.search('клавиш|быстр|ctrl', text) else
                 'ai_settings' if re.search('ии|ollama|ai.pack', text) else 'reminders')
        answer, label = APP_HELP[topic]
        answer = shortcut_label(answer)
    else:
        return None
    actions = []
    if topic and APP_HELP[topic][1]:
        actions = [{'action': topic, 'label': APP_HELP[topic][1]}]
    return {'text': answer, 'sources': [], 'engine_label': 'Помощник Orange Notes',
            'retrieval': 'local_intent', 'status': 'answered', 'intent': intent,
            'help_topic': topic, 'help_actions': actions}
