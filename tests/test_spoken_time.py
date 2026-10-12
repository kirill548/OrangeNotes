import pytest
from app.services.spoken_time import normalize_spoken_time


@pytest.mark.parametrize('phrase, expected', [
    ('завтра в пятнадцать часов', 'завтра в 15:00'),
    ('в двенадцать часов тридцать минут', 'в 12:30'),
    ('в двадцать один час пять минут', 'в 21:05'),
    ('в 14 часов 30 минут', 'в 14:30'),
    ('в ноль часов две минуты', 'в 00:02'),
    ('в двадцать пять часов', 'в 25:00'),
    ('купить пятнадцать яблок', 'купить пятнадцать яблок'),
    ('в половине третьего', 'в половине третьего'),
])
def test_explicit_clock_phrases(phrase, expected):
    assert normalize_spoken_time(phrase) == expected


def test_spoken_time_creates_correct_draft():
    from datetime import datetime
    from app.services.assistant_actions import intent_response
    response = intent_response('Напомни завтра в пятнадцать часов купить молоко',
                               'personal', now=datetime(2026, 10, 13, 10))
    assert response['action_draft']['once_at'] == '2026-10-14T15:00:00'


def test_invalid_spoken_hour_does_not_create_draft():
    from app.services.assistant_actions import intent_response
    response = intent_response('Напомни завтра в двадцать пять часов купить молоко', 'personal')
    assert 'action_draft' not in response
    assert 'неверно' in response['text']

@pytest.mark.parametrize('phrase, expected', [
    ('четырнадцать ноль ноль', '14:00'),
    ('двенадцать тридцать', '12:30'),
    ('в двенадцать тридцать', 'в 12:30'),
    ('завтра в четырнадцать', 'завтра в 14:00'),
    ('завтра в двадцать один', 'завтра в 21:00'),
    ('в двадцать один', 'в двадцать один'),
    ('Напомни завтра в четырнадцать купить молоко', 'Напомни завтра в 14:00 купить молоко'),
    ('завтра четырнадцать ноль ноль', 'завтра 14:00'),
    ('в двадцать один тридцать пять', 'в 21:35'),
    ('в четырнадцать нуль нуль', 'в 14:00'),
    ('в четырнадцать шестьдесят', 'в 14:60'),
    ('в двадцать пять ноль ноль', 'в 25:00'),
    ('купить двенадцать тридцать яблок', 'купить двенадцать тридцать яблок'),
    ('сумма четырнадцать ноль ноль рублей', 'сумма четырнадцать ноль ноль рублей'),
    ('в два магазина', 'в два магазина'),
    ('номер 14 00', 'номер 14 00'),
    ('завтра в два магазина', 'завтра в два магазина'),
    ('в четырнадцать', 'в четырнадцать'),
    ('завтра в 14:00', 'завтра в 14:00'),
])
def test_colloquial_clock_phrases(phrase, expected):
    assert normalize_spoken_time(phrase) == expected


@pytest.mark.parametrize('phrase, expected', [
    ('Напомни завтра в четырнадцать ноль ноль купить молоко', '2026-10-14T14:00:00'),
    ('Напомни завтра в двенадцать тридцать купить молоко', '2026-10-14T12:30:00'),
    ('Напомни завтра в четырнадцать купить молоко', '2026-10-14T14:00:00'),
])
def test_colloquial_time_draft(phrase, expected):
    from datetime import datetime
    from app.services.assistant_actions import intent_response
    response = intent_response(phrase, 'personal', now=datetime(2026, 10, 13, 10))
    assert response['action_draft']['once_at'] == expected


@pytest.mark.parametrize('phrase', [
    'Напомни завтра в четырнадцать шестьдесят купить молоко',
    'Напомни завтра в двадцать пять ноль ноль купить молоко',
])
def test_invalid_colloquial_clock_keeps_validation_gate(phrase):
    from app.services.assistant_actions import intent_response
    response = intent_response(phrase, 'personal')
    assert 'action_draft' not in response
    assert 'неверно' in response['text']


@pytest.mark.parametrize('phrase', ['работать двенадцать часов', 'в двенадцать тридцать рублей', 'в двенадцать тридцать яблок'])
def test_quantities_are_not_clock_times(phrase):
    assert normalize_spoken_time(phrase) == phrase


def test_compound_currency_not_reinterpreted():
    assert normalize_spoken_time('в двенадцать тридцать пять рублей') == 'в двенадцать тридцать пять рублей'


@pytest.mark.parametrize('phrase, expected', [
    ('Напомни завтра в 12.00 позвонить врачу.', 'Напомни завтра в 12:00 позвонить врачу.'),
    ('Напомни завтра в 14 00 купить молоко.', 'Напомни завтра в 14:00 купить молоко.'),
    ('завтра 14 30', 'завтра 14:30'),
    ('в 12.00', 'в 12:00'),
    ('Напомни завтра в 14 000 купить молоко.', 'Напомни завтра в 14 000 купить молоко.'),
    ('купить в 14 00 рублей', 'купить в 14 00 рублей'),
    ('Напомни завтра купить молоко в 14 00 рублей', 'Напомни завтра купить молоко в 14 00 рублей'),
    ('версия 12.00', 'версия 12.00'),
    ('в 14 00', 'в 14 00'),
    ('Напомни завтра в 14.60 купить молоко', 'Напомни завтра в 14:60 купить молоко'),
])
def test_numeric_stt_clock_formats(phrase, expected):
    assert normalize_spoken_time(phrase) == expected
