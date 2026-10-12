"""Deterministic production-routing gate; deliberately separate from live LLM gates."""
import json
import time
from datetime import datetime
from pathlib import Path
from app.services.intent_router import conversational_response, classify_intent
from app.services.assistant_actions import intent_response


CASES = (
    ('greeting', 'Привет', 'small_talk', None),
    ('identity', 'Кто ты?', 'small_talk', None),
    ('thanks', 'Спасибо!', 'small_talk', None),
    ('synonyms', 'Как настроить синонимы?', 'app_help', None),
    ('spaces', 'Как создать пространство?', 'app_help', None),
    ('pins', 'Как прикрепить контекст?', 'app_help', None),
    ('shortcuts', 'Какие горячие клавиши?', 'app_help', None),
    ('recall-not-help', 'Что я записывал про синонимы?', 'note_rag', None),
    ('weekly-tool', 'Напомни проверить отчет каждый понедельник в 10:00', 'tool_call', 'FREQ=WEEKLY'),
    ('daily-tool', 'Напомни пить воду ежедневно в 15:30', 'tool_call', 'FREQ=DAILY'),
    ('once-tool', 'Создай напоминание позвонить врачу завтра в 10:00', 'tool_call', None),
)


def run(output, now=datetime(2026, 10, 12, 12)):
    rows = []
    for name, query, expected, recurrence in CASES:
        start = time.perf_counter()
        actual = classify_intent(query)
        result = conversational_response(query) or intent_response(query, 1, now=now)
        passed = actual == expected
        if expected == 'note_rag':
            passed = passed and result is None
        else:
            passed = passed and isinstance(result, dict) and not result.get('sources')
        if expected == 'tool_call':
            from app.services.assistant_actions import validate_tool_call
            draft = (result or {}).get('action_draft', {})
            passed = passed and bool(draft) and bool((result or {}).get('tool_calls'))
            if passed:
                arguments = validate_tool_call(result['tool_calls'][0])['arguments']
                expected_args = {
                    'weekly-tool': ('проверить отчет', '2026-10-19', '10:00', 'FREQ=WEEKLY;BYDAY=MO;BYHOUR=10;BYMINUTE=0'),
                    'daily-tool': ('пить воду', '2026-10-12', '15:30', 'FREQ=DAILY;BYHOUR=15;BYMINUTE=30'),
                    'once-tool': ('позвонить врачу', '2026-10-13', '10:00', None),
                }[name]
                passed = (arguments['title'].casefold() == expected_args[0]
                    and arguments['date'] == expected_args[1] and arguments['time'] == expected_args[2]
                    and arguments['recurrence'] == expected_args[3])
        rows.append({'name': name, 'query': query, 'intent': actual, 'passed': bool(passed),
                     'latency_seconds': time.perf_counter() - start, 'result': result})
    report = {'scope': 'Production deterministic router and validated drafts; no live LLM measurement',
              'clock': now.isoformat(), 'cases': rows, 'passed': sum(r['passed'] for r in rows),
              'failed': sum(not r['passed'] for r in rows)}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def run_live_tools(client, output):
    """Probe actual model JSON, independently of the deterministic parser. Never execute."""
    from app.services.assistant_actions import validate_tool_call
    rows = []
    scenarios = (
        ('weekly', 'Напомни проверить отчет каждый понедельник в 10:00',
         '2026-10-19', '10:00', 'FREQ=WEEKLY;BYDAY=MO;BYHOUR=10;BYMINUTE=0'),
        ('daily', 'Напомни пить воду ежедневно в 15:30',
         '2026-10-12', '15:30', 'FREQ=DAILY;BYHOUR=15;BYMINUTE=30'),
        ('once', 'Напомни позвонить врачу завтра в 10:00',
         '2026-10-13', '10:00', None),
    )
    for name, query, date, clock, recurrence in scenarios:
        start = time.perf_counter()
        row = {'name': name, 'passed': False}
        try:
            raw = client.chat([{'role': 'system', 'content':
                'Верни только JSON tool_calls с одним create_reminder. Не выполняй действие. '
                'Сейчас понедельник 2026-10-12 12:00, часовой пояс Asia/Yekaterinburg. '
                'date — ближайшая будущая дата YYYY-MM-DD, time — HH:MM. '
                'recurrence — строго FREQ=DAILY;BYHOUR=h;BYMINUTE=m либо '
                'FREQ=WEEKLY;BYDAY=код;BYHOUR=h;BYMINUTE=m. Без префикса RRULE:. '
                'Коды дней: понедельник MO, вторник TU, среда WE, четверг TH, пятница FR, суббота SA, воскресенье SU. '
                'Если сегодняшнее время прошло, выбирай следующее подходящее будущее число. '
                'без повтора null. title — только содержание задачи.'},
                {'role': 'user', 'content': query}], json_mode='tools', stream=True)
            row['raw'] = raw
            packet = json.loads(raw)
            if set(packet) != {'tool_calls'} or len(packet['tool_calls']) != 1:
                raise ValueError('Expected exactly one tool call')
            call = validate_tool_call(packet['tool_calls'][0])
            # Validator may return None; inspect the validated original structure.
            call = packet['tool_calls'][0]
            args = call['arguments']
            expected_title = {'weekly': 'проверить отчет', 'daily': 'пить воду', 'once': 'позвонить врачу'}[name]
            row['passed'] = (call['name'] == 'create_reminder' and args['date'] == date
                and args['time'] == clock and args['recurrence'] == recurrence
                and ' '.join(args['title'].casefold().replace('ё', 'е').split()) == expected_title
                and args['timezone'] == 'Asia/Yekaterinburg')
        except Exception as error:
            row['error'] = str(error)
        row['latency_seconds'] = time.perf_counter() - start
        rows.append(row)
    report = {'scope': 'Actual model tool JSON; no writes', 'model': client.model,
              'cases': rows, 'passed': sum(r['passed'] for r in rows),
              'failed': sum(not r['passed'] for r in rows)}
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='work/v013-conversation/conversational-quality-results.json')
    args = parser.parse_args()
    report = run(args.output)
    print(json.dumps({'passed': report['passed'], 'failed': report['failed']}))
    raise SystemExit(bool(report['failed']))
