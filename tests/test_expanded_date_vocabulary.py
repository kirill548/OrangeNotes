import unittest
from datetime import datetime, timedelta
from app.services.assistant_actions import intent_response
from app.services.date_vocabulary import MONTH_GROUPS, RELATIVE_GROUPS, WEEKDAY_GROUPS
from app.services.intent_router import conversational_response

class ExpandedDateVocabularyTests(unittest.TestCase):
    now = datetime(2026, 1, 1, 7)
    def ask(self, slot):
        return intent_response('Создай напоминание на '+slot+', что встреча в 15:00', 1, now=self.now)
    def test_every_month_alias_compact_and_spaced(self):
        for month, (canonical, aliases) in enumerate(MONTH_GROUPS, 1):
            for word in [canonical, *aliases.split()]:
                for slot in (f'15 {word} 10:00',f'15{word}10:00'):
                    with self.subTest(slot=slot):
                        self.assertEqual(self.ask(slot)['action_draft']['once_at'],f'2026-{month:02d}-15T10:00:00')
    def test_every_relative_alias(self):
        for index,(canonical,aliases) in enumerate(RELATIVE_GROUPS):
            for word in [canonical,*aliases.split()]:
                with self.subTest(word=word):
                    result=self.ask(word+' в 10:00')
                    if canonical=='вчера': self.assertNotIn('action_draft',result)
                    else: self.assertEqual(result['action_draft']['once_at'],(self.now+timedelta(days=index)).replace(hour=10).isoformat())
        for slot in ('после завтпа 10:00','завтрав15:00'):
            self.assertIn('action_draft', self.ask(slot))
    def test_every_weekday_alias(self):
        for day,(canonical,aliases) in zip(('MO','TU','WE','TH','FR','SA','SU'),WEEKDAY_GROUPS):
            for word in [canonical,*aliases.split()]:
                with self.subTest(word=word):
                    result=intent_response('Напомни каждый '+word+' в 10:00, что встреча',1,now=self.now)
                    self.assertIn('BYDAY='+day,result['action_draft']['recurrence'])
    def test_period_defaults_and_explicit_override(self):
        for period,clock in (('утром','09:00'),('днем','13:00'),('вечером','18:00'),('ночью','23:00')):
            with self.subTest(period=period):
                self.assertEqual(self.ask('завтра '+period)['action_draft']['once_at'],'2026-01-02T'+clock+':00')
                self.assertEqual(self.ask('завтра '+period+' в 14:30')['action_draft']['once_at'],'2026-01-02T14:30:00')
    def test_invalid_time_never_creates_draft(self):
        for clock in ('14:60','10:90','25:00','24:15','100:00','10:0'):
            with self.subTest(clock=clock):
                result=self.ask('завтра '+clock)
                self.assertNotIn('action_draft',result)
                self.assertIn('Уточните',result['text'])
        self.assertIn('[14:60]',self.ask('завтра 14:60')['text'])
    def test_goal_clarification_does_not_search_notes(self):
        result=conversational_response('Помоги разобраться в ситуации. Сначала уточни, чего я хочу добиться.')
        self.assertIn('Чего вы хотите',result['text'])
        self.assertEqual(result['sources'],[])

    def test_new_compact_relative_aliases_and_followup(self):
        for slot,day,clock in [('завра13:00',2,'13:00'),('завтрав13:00',2,'13:00'),('заврав10:00',2,'10:00'),('затрав15:00',2,'15:00'),('сегодня14:00',1,'14:00'),('сегдняв15:00',1,'15:00'),('седняв10:00',1,'10:00'),('послезавра16:00',3,'16:00'),('послезавтрав10:00',3,'10:00'),('после завра 10:00',3,'10:00')]:
            with self.subTest(slot=slot):
                self.assertEqual(self.ask(slot)['action_draft']['once_at'],f'2026-01-{day:02d}T{clock}:00')
        query='Сделай напоминание, что встреча'
        response=intent_response(query,1,now=self.now)
        history=[{'role':'user','workspace_id':1,'content':query},{'role':'assistant','workspace_id':1,'content':response['text']}]
        result=intent_response('завра 13:00',1,history,now=self.now)
        self.assertEqual(result['action_draft']['once_at'],'2026-01-02T13:00:00')

    def test_requested_relative_spellings_in_followup_matrix(self):
        # Keep this independent of the production dictionary: a removed alias
        # must fail a test rather than disappear from both implementation and test.
        cases = (
            (1, 'сегодня сегдня сегоня седня сгдня сегня севодня сигодня согодня сегодняа сешодня сеодня сегодням'),
            (2, 'завтра завра завтр затра зватра зата завт завтпа завтро завтром завтраа звтра совтора завтрк завтрп завтрв'),
            (3, 'послезавтра послезавра послезавтр послезатра послязавтра послезата послезавтпа послезавтро поселязавтра послезавтром'),
        )
        request = 'Сделай напоминание, что приём у врача в 15:00'
        pending = intent_response(request, 1, now=self.now)
        history = [
            {'role': 'user', 'workspace_id': 1, 'content': request},
            {'role': 'assistant', 'workspace_id': 1, 'content': pending['text']},
        ]
        for day, words in cases:
            for word in words.split():
                for slot in (word+' 13:00', word+'13:00', word+'в13:00'):
                    with self.subTest(slot=slot):
                        result = intent_response(slot, 1, history, now=self.now)
                        self.assertEqual(result['action_draft']['once_at'], f'2026-01-{day:02d}T13:00:00')
                        self.assertIn('приём у врача в 15:00', result['action_draft']['body'])

    def test_spaced_day_after_tomorrow_spellings(self):
        for slot in ('после завтра 10:00', 'после завра 10:00', 'после завтпа 10:00'):
            with self.subTest(slot=slot):
                self.assertEqual(self.ask(slot)['action_draft']['once_at'], '2026-01-03T10:00:00')

    def test_requested_yesterday_spellings_never_create_past_draft(self):
        for word in ('вчера', 'вчеро', 'вчерась', 'вчра', 'вечера', 'вчеа'):
            with self.subTest(word=word):
                result = self.ask(word+' 13:00')
                self.assertNotIn('action_draft', result)
                self.assertIn('Уточните', result['text'])
