"""User-owned, workspace-scoped lexical aliases; never secret/ID aliases."""
import json
import re


class SynonymDictionary:
    MAX_GROUPS = 200
    MAX_TERMS = 12
    MAX_EXPANSION = 64

    def __init__(self, connection):
        self.db = connection
        self.db.execute('''CREATE TABLE IF NOT EXISTS memory_synonyms(
            workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
            name TEXT NOT NULL, terms TEXT NOT NULL,
            PRIMARY KEY(workspace_id,name))''')
        self.db.commit()

    @staticmethod
    def normalize(term):
        if not isinstance(term, str):
            raise ValueError('Синоним должен быть текстом.')
        term = ' '.join(term.casefold().replace('ё', 'е').split())
        # Numbers, punctuation and opaque credentials must never be rewritten.
        if not re.fullmatch(r'[a-zа-я]{2,40}(?: [a-zа-я]{2,40}){0,3}', term):
            raise ValueError('Используйте слова без номеров, паролей и специальных символов.')
        if any(word in {'пароль', 'password', 'token', 'токен', 'secret', 'секрет'} for word in term.split()):
            raise ValueError('Пароли и токены нельзя включать в словарь.')
        return term

    def list_groups(self, workspace_id):
        return [{'name': row[0], 'terms': json.loads(row[1])} for row in self.db.execute(
            'SELECT name,terms FROM memory_synonyms WHERE workspace_id=? ORDER BY name', (workspace_id,))]

    def set_group(self, workspace_id, name, terms):
        name = self.normalize(name)
        if not isinstance(terms, (list, tuple)) or not 2 <= len(terms) <= self.MAX_TERMS:
            raise ValueError('Группа должна содержать от 2 до 12 синонимов.')
        normalized = sorted({self.normalize(term) for term in terms})
        if len(normalized) < 2:
            raise ValueError('Укажите хотя бы два разных синонима.')
        exists = self.db.execute('SELECT 1 FROM memory_synonyms WHERE workspace_id=? AND name=?', (workspace_id, name)).fetchone()
        if not exists and len(self.list_groups(workspace_id)) >= self.MAX_GROUPS:
            raise ValueError('Достигнут лимит: 200 групп в пространстве.')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO memory_synonyms VALUES(?,?,?)',
                            (workspace_id, name, json.dumps(normalized, ensure_ascii=False)))

    def delete_group(self, workspace_id, name):
        with self.db:
            return self.db.execute('DELETE FROM memory_synonyms WHERE workspace_id=? AND name=?',
                                   (workspace_id, self.normalize(name))).rowcount > 0

    def expand(self, query, workspace_id):
        normalized = ' '.join(query.casefold().replace('ё', 'е').split())
        if re.search(r'(?<!\w)(?:парол\w*|password\w*|токен\w*|token\w*|secret\w*|секрет\w*)(?!\w)', normalized):
            return []
        result = set()
        for group in self.list_groups(workspace_id):
            if any(re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', normalized) for term in group['terms']):
                result.update(group['terms'])
        # One hop only: avoid recursive synonym explosions.
        return sorted(result)[:self.MAX_EXPANSION]
