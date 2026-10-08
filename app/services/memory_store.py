"""Workspace-scoped local retrieval; lexical fallback, not semantic embeddings.

Versions capture changes observed by sync, not every edit between synchronisations.
Each instance owns a connection and must be constructed in the thread using it.
"""
import hashlib
import json
import math
import re
from datetime import datetime
from difflib import SequenceMatcher

from app.database.store import Store, plain_body


def _normal(value):
    return ' '.join((value or '').casefold().replace('ё','е').split())


def _words(value):
    return re.findall(r'[a-zа-я0-9]+',_normal(value))


def _opaque_identifiers(value):
    """Long mixed identifiers require literal evidence, unlike ordinary concepts.

    Embeddings can associate unrelated IDs merely because both contain digits.
    Never guess a secret/token/code when the requested identifier is absent.
    """
    tokens = re.findall(r'[a-zа-я0-9_\-]+', _normal(value))
    identifiers = {token for token in tokens if
                   (len(re.findall(r'\d', token)) >= 4 and len(re.findall(r'[a-zа-я]', token)) >= 3)
                   or (token.isdigit() and len(token) >= 6)}
    identifiers.update(re.findall(r'(?<![\w])(?:[a-zа-я]{1,6}-?\d{2,12})(?![\w])', _normal(value)))
    # Preserve exact phone matching across spaces, parentheses and separators.
    for phone in re.findall(r'(?<![\w])\+?\d[\d ()-]{4,}\d(?![\w])', value):
        digits = ''.join(re.findall(r'\d', phone))
        if 6 <= len(digits) <= 15:
            identifiers.add(digits)
    return identifiers


def _stem(word):
    for ending in ('иями','ями','ами','ого','его','ому','ему','ая','яя','ый','ий','ей','ой','ов','ев','ах','ях','ы','и','а','я','у','ю','е','о'):
        if word.endswith(ending) and len(word)-len(ending)>=3:
            return word[:-len(ending)]
    return word


def _hash(title,body):
    return hashlib.sha256(json.dumps([title,body],ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()


_STOP = set(_words('найди найти покажи показать пожалуйста мне мои моя мое о об про в во на и или а но для у с со к из по что где как кто какие какой когда это есть ли было бы the a an of to and in my find show'))
_SYNONYMS = [set(_stem(word) for word in _words(group)) for group in (
    'гараж бокс парковка автомобиль машина авто car garage parking',
    'студия мастерская ателье studio workshop',
    'аренда арендовать снять съем rental rent lease',
    'маркетинг реклама продвижение marketing advertising promotion',
    'контакт телефон звонок связаться contact phone call',
)]


class MemoryStore:
    retrieval_mode = 'keyword_fallback'
    CHUNK_SIZE = 500
    CHUNK_OVERLAP = 100
    INDEX_FORMAT = '500-overlap100-v1'

    @staticmethod
    def _dense_threshold(model):
        # Cosine distributions differ between model families. This Qwen threshold
        # admits the checked .268 relationship while rejecting the .162 control.
        return 0.25 if str(model).casefold() == 'qwen3-embedding:0.6b' else 0.35

    def __init__(self, store_or_path=None, embedding_provider=None):
        path = store_or_path.path if isinstance(store_or_path,Store) else store_or_path
        self.store = Store(path)
        self.db = self.store.db
        # Reserved extension point. No unconfigured provider or network is invoked.
        self.embedding_provider = embedding_provider
        self.fts_available = False
        try:
            self._initialize()
        except Exception:
            self.close()
            raise

    def close(self):
        self.db.close()

    def _initialize(self):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute("CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('personal','work')))")
            self.db.executemany('INSERT OR IGNORE INTO workspaces(id,name,kind) VALUES(?,?,?)',[(1,'Личное','personal'),(2,'Рабочее','work')])
            columns={row['name'] for row in self.store.rows('PRAGMA table_info(notes)')}
            for name,definition in [('created_at','TEXT'),('workspace_id','INTEGER NOT NULL DEFAULT 1')]:
                if name not in columns:
                    self.db.execute(f'ALTER TABLE notes ADD COLUMN {name} {definition}')
            self.db.execute('CREATE INDEX IF NOT EXISTS notes_workspace ON notes(workspace_id,deleted,archived)')
            self.db.execute('''CREATE TABLE IF NOT EXISTS note_versions(
                id INTEGER PRIMARY KEY,note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                revision INTEGER NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,content_hash TEXT NOT NULL,
                workspace_id INTEGER NOT NULL REFERENCES workspaces(id),recorded_at TEXT NOT NULL,
                UNIQUE(note_id,revision))''')
            self.db.execute('''CREATE TABLE IF NOT EXISTS memory_chunks(
                note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                version_id INTEGER NOT NULL REFERENCES note_versions(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,chunk TEXT NOT NULL,
                PRIMARY KEY(note_id,position))''')
            self.db.execute('''CREATE TABLE IF NOT EXISTS memory_embeddings(
                note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                version_id INTEGER NOT NULL REFERENCES note_versions(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
                model TEXT NOT NULL,text_hash TEXT NOT NULL,vector TEXT NOT NULL,
                PRIMARY KEY(version_id,position,model))''')
            self.db.execute('''CREATE TABLE IF NOT EXISTS memory_index_state(
                note_id INTEGER PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
                version_id INTEGER NOT NULL REFERENCES note_versions(id) ON DELETE CASCADE,
                format TEXT NOT NULL)''')
            try:
                self.db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(chunk,note_id UNINDEXED,version_id UNINDEXED,position UNINDEXED)')
                self.fts_available=True
            except Exception as error:
                # Only the optional FTS module absence is a supported fallback.
                if 'no such module: fts5' not in str(error).lower():
                    raise

    def list_workspaces(self):
        return [dict(row) for row in self.store.rows('SELECT id,name,kind FROM workspaces ORDER BY id')]

    def _workspace(self,workspace_id):
        if type(workspace_id) is not int or not self.store.rows('SELECT 1 FROM workspaces WHERE id=?',(workspace_id,)):
            raise ValueError('Выберите существующее пространство: личное или рабочее.')
        return workspace_id

    def move_note(self,note_id,workspace_id):
        workspace_id=self._workspace(workspace_id)
        result=self.store.execute('UPDATE notes SET workspace_id=? WHERE id=? AND deleted=0',(workspace_id,note_id))
        if result.rowcount!=1:
            raise ValueError('Заметка удалена или больше не существует.')
        # Remove old searchable fragments immediately; the new scope sync rebuilds.
        with self.db:
            self.db.execute('DELETE FROM memory_chunks WHERE note_id=?',(note_id,))
            if self.fts_available:
                self.db.execute('DELETE FROM memory_fts WHERE note_id=?',(note_id,))

    def sync(self,workspace_id=1,include_trash=False,_snapshots=None):
        workspace_id=self._workspace(workspace_id)
        changed=0
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            rows=self.store.rows('SELECT id,title,body,workspace_id,updated_at,deleted,archived FROM notes WHERE workspace_id=? AND (? OR deleted=0)',(workspace_id,int(include_trash)))
            for note in rows:
                digest=_hash(note['title'],note['body'])
                if _snapshots is not None:
                    _snapshots[note['id']]=dict(note,content_hash=digest)
                versions=self.store.rows('SELECT id,revision,content_hash,workspace_id FROM note_versions WHERE note_id=? ORDER BY revision DESC LIMIT 1',(note['id'],))
                previous=versions[0] if versions else None
                if not previous or previous['content_hash']!=digest or previous['workspace_id']!=workspace_id:
                    revision=previous['revision']+1 if previous else 1
                    version_id=self.db.execute('''INSERT INTO note_versions(note_id,revision,title,body,content_hash,workspace_id,recorded_at)
                        VALUES(?,?,?,?,?,?,?)''',(note['id'],revision,note['title'],note['body'],digest,workspace_id,datetime.now().isoformat(timespec='seconds'))).lastrowid
                    changed+=1
                else:
                    version_id=previous['id']
                indexed=self.store.rows('SELECT version_id,format FROM memory_index_state WHERE note_id=?',(note['id'],))
                if indexed and indexed[0]['version_id']==version_id and indexed[0]['format']==self.INDEX_FORMAT and self.store.rows('SELECT 1 FROM memory_chunks WHERE note_id=? LIMIT 1',(note['id'],)):
                    continue
                self.db.execute('DELETE FROM memory_embeddings WHERE note_id=?',(note['id'],))
                self.db.execute('DELETE FROM memory_chunks WHERE note_id=?',(note['id'],))
                if self.fts_available:
                    self.db.execute('DELETE FROM memory_fts WHERE note_id=?',(note['id'],))
                text=' '.join(plain_body(note['body']).split())
                for position in range(0,max(1,len(text)),self.CHUNK_SIZE-self.CHUNK_OVERLAP):
                    chunk=text[position:position+self.CHUNK_SIZE]
                    self.db.execute('INSERT INTO memory_chunks VALUES(?,?,?,?)',(note['id'],version_id,position,chunk))
                    if self.fts_available:
                        self.db.execute('INSERT INTO memory_fts(chunk,note_id,version_id,position) VALUES(?,?,?,?)',(chunk,note['id'],version_id,position))
                self.db.execute('INSERT OR REPLACE INTO memory_index_state VALUES(?,?,?)',(note['id'],version_id,self.INDEX_FORMAT))
            # Physical deletion cascades relational rows; FTS has no foreign keys.
            if self.fts_available:
                self.db.execute('DELETE FROM memory_fts WHERE note_id NOT IN (SELECT id FROM notes)')
        return {'indexed':len(rows),'changed':changed,'retrieval':self.retrieval_mode}

    @staticmethod
    def _score(query,title,chunk):
        terms=[_stem(word) for word in _words(query) if word not in _STOP][:24]
        if not terms:
            return 0.0
        title_words={_stem(word) for word in _words(title)}
        body_words={_stem(word) for word in _words(chunk)}
        score=0.0
        for term in set(terms):
            if term in title_words:
                score+=6
            if term in body_words:
                score+=3
            if term not in title_words|body_words:
                expanded=set().union(*(group for group in _SYNONYMS if term in group))
                if expanded & (title_words|body_words):
                    score+=1.5
                elif len(term)>=4:
                    similarity=max((SequenceMatcher(None,term,word).ratio() for word in title_words|body_words if len(word)>=4),default=0)
                    if similarity>=0.82:
                        score+=similarity
        digits=''.join(re.findall(r'\d',query))
        if len(digits)>=5 and digits in ''.join(re.findall(r'\d',title+' '+chunk)):
            score+=100
        return score

    @staticmethod
    def _literal_score(query,title,chunk):
        terms={_stem(word) for word in _words(query) if word not in _STOP}
        title_words={_stem(word) for word in _words(title)}
        chunk_words={_stem(word) for word in _words(chunk)}
        return sum(6*(term in title_words)+3*(term in chunk_words) for term in terms)

    def search(self,query,workspace_id=1,include_archive=True,include_trash=False,limit=8,
               embedding_provider=None,embedding_model='qwen3-embedding:0.6b',fuzzy_identifiers=False):
        workspace_id=self._workspace(workspace_id)
        if not isinstance(query,str) or not query.strip():
            return []
        limit=max(0,min(100,int(limit)))
        if not limit:
            return []
        note_snapshots={}
        self.sync(workspace_id,include_trash=include_trash,_snapshots=note_snapshots)
        # Scope and visibility are applied in SQL before scoring or returning data.
        rows=self.store.rows('''SELECT n.id,n.title,n.workspace_id,n.updated_at,n.deleted,n.archived,
                c.chunk,c.position,v.id AS version_id,v.revision,v.content_hash
            FROM notes n JOIN memory_chunks c ON c.note_id=n.id JOIN note_versions v ON v.id=c.version_id
            WHERE n.workspace_id=? AND (? OR n.archived=0) AND (? OR n.deleted=0)
                AND v.workspace_id=n.workspace_id''',(workspace_id,int(include_archive),int(include_trash)))
        hashes={identity:note['content_hash'] for identity,note in note_snapshots.items()}
        rows=[row for row in rows if hashes.get(row['id'])==row['content_hash']]
        requested_identifiers = _opaque_identifiers(query)
        literal=[row for row in rows if self._literal_score(query,row['title'],row['chunk'])>0
                 and (not requested_identifiers or requested_identifiers.intersection(
                     _opaque_identifiers(row['title']+' '+row['chunk'])))]
        stage='literal' if literal else 'fallback'
        if literal: rows=literal
        provider=self.embedding_provider if embedding_provider is None else embedding_provider
        dense=self._dense_scores(query,rows,provider,embedding_model) if provider and not literal and not requested_identifiers else {}
        dense_threshold=self._dense_threshold(embedding_model)
        found={}
        fuzzy_found={}
        # Compare with immutable versions inside SQLite after model I/O. The full
        # body was returned only once by sync; raw SQL edits (even with an unchanged
        # updated_at) and workspace moves are still detected without re-fetching it.
        current_notes={row['id']:row for row in self.store.rows('''
            SELECT n.id,v.content_hash FROM notes n
            JOIN memory_index_state i ON i.note_id=n.id
            JOIN note_versions v ON v.id=i.version_id
            WHERE n.workspace_id=? AND v.workspace_id=n.workspace_id
                AND (? OR n.deleted=0) AND (? OR n.archived=0)
                AND n.title=v.title AND n.body=v.body''',
            (workspace_id,int(include_trash),int(include_archive)))}
        bodies={}
        for row in rows:
            current=current_notes.get(row['id'])
            if current is None or current['content_hash']!=row['content_hash']:
                continue
            score=self._score(query,row['title'],row['chunk'])
            similarity=dense.get((row['version_id'],row['position']),0)
            evidence = _normal(row['title'] + ' ' + row['chunk'])
            approximate=[]
            if requested_identifiers and not requested_identifiers.intersection(_opaque_identifiers(evidence)):
                if not fuzzy_identifiers: continue
                from app.utils.fuzzy_identifiers import near_identifiers
                approximate=near_identifiers(requested_identifiers,_opaque_identifiers(evidence))
                if not approximate: continue
            if score <= 0 and _opaque_identifiers(row['chunk']) and not requested_identifiers:
                # Opaque tokens are not semantic evidence. Explicit keyword search
                # (e.g. title/contact) still finds notes containing these values.
                continue
            if not approximate and score<=0 and similarity<dense_threshold:
                continue
            result={'id':row['id'],'title':row['title'] or 'Без названия','body':bodies.setdefault(row['id'],None),
                    'search_stage':stage,'chunk':row['chunk'],'revision':row['revision'],'workspace_id':row['workspace_id'],
                    'state':'trash' if row['deleted'] else 'archive' if row['archived'] else 'active',
                    'updated_at':row['updated_at'],'score':round(score,4),'offset':row['position'],
                    'version_id':row['version_id'],'retrieval':'hybrid' if provider and not literal else self.retrieval_mode,
                    'dense_similarity':similarity}
            if bodies[row['id']] is None: bodies[row['id']]=' '.join(plain_body(note_snapshots[row['id']]['body']).split())
            result['body']=bodies[row['id']]
            if approximate:
                result.update(retrieval='fuzzy_identifier',identifier_matches=approximate)
                previous=fuzzy_found.get(row['id'])
                if previous is None or approximate[0]['distance']<previous['identifier_matches'][0]['distance']:
                    fuzzy_found[row['id']]=result
                continue
            # Keep the strongest relevant fragment for each note; final rankings use RRF.
            strength=score+similarity if score else similarity
            if row['id'] not in found or found[row['id']]['_strength']<strength:
                result['_strength']=strength
                found[row['id']]=result
        if not found and fuzzy_identifiers and fuzzy_found:
            selected=sorted(fuzzy_found.values(),key=lambda r:(r['identifier_matches'][0]['distance'],r['id']))[:limit]
            return self._merge_neighbors(selected)
        results=list(found.values())
        if provider:
            lexical=sorted((row for row in results if row['score']>0),key=lambda row:row['score'],reverse=True)
            semantic=sorted((row for row in results if row['dense_similarity']>=dense_threshold),key=lambda row:row['dense_similarity'],reverse=True)
            ranks={}
            for ranking in (lexical,semantic):
                for rank,row in enumerate(ranking,1):
                    ranks[row['id']]=ranks.get(row['id'],0)+1/(60+rank)
            for row in results:
                row['lexical_score']=row['score']
                row['score']=ranks[row['id']]
        for row in results:
            row.pop('_strength',None)
        selected=sorted(results,key=lambda row:(row['score'],row['updated_at'],row['id']),reverse=True)[:limit]
        return self._merge_neighbors(selected)

    def _merge_neighbors(self,results):
        # Excerpts remain exact slices of the verified normalized body. Adjacent
        # blocks add context without repeating their overlapping characters.
        stride=self.CHUNK_SIZE-self.CHUNK_OVERLAP
        for result in results:
            start=max(0,result['offset']-stride)
            end=min(len(result['body']),result['offset']+self.CHUNK_SIZE+stride)
            result['chunk']=result['body'][start:end]
            result['offset']=start
        return results

    @staticmethod
    def _embed(provider,texts,model):
        vectors=provider.embed(texts,model=model) if hasattr(provider,'embed') else provider(texts)
        if len(vectors)!=len(texts):
            raise ValueError('Модель вернула неверное число векторов.')
        result=[]
        for vector in vectors:
            values=[float(value) for value in vector]
            if not values or not all(math.isfinite(value) for value in values) or not any(values):
                raise ValueError('Модель вернула неверный вектор.')
            result.append(values)
        return result

    def _dense_scores(self,query,rows,provider,model):
        # No model is invoked while a SQLite write transaction is open.
        if not rows:
            return {}
        query_vector=self._embed(provider,[query],model)[0]
        vectors={}
        missing=[]
        for row in rows:
            text=row['title']+'\n'+row['chunk']
            digest=hashlib.sha256(text.encode('utf-8')).hexdigest()
            cached=self.store.rows('''SELECT vector FROM memory_embeddings
                WHERE version_id=? AND position=? AND model=? AND workspace_id=? AND text_hash=?''',
                                  (row['version_id'],row['position'],model,row['workspace_id'],digest))
            identity=(row['version_id'],row['position'])
            if cached:
                vectors[identity]=json.loads(cached[0]['vector'])
            else:
                missing.append((row,text,digest))
        for begin in range(0,len(missing),8):
            batch=missing[begin:begin+8]
            embedded=self._embed(provider,[item[1] for item in batch],model)
            with self.db:
                for (row,text,digest),vector in zip(batch,embedded):
                    # Recheck scope/content after model I/O. Moved/deleted or changed
                    # notes must not be written into the former workspace's cache.
                    active=self.store.rows('''SELECT 1 FROM notes n JOIN note_versions v ON v.id=?
                        WHERE n.id=? AND n.workspace_id=? AND v.workspace_id=n.workspace_id
                            AND n.title=v.title AND n.body=v.body''',
                        (row['version_id'],row['id'],row['workspace_id']))
                    if not active:
                        continue
                    self.db.execute('INSERT OR REPLACE INTO memory_embeddings VALUES(?,?,?,?,?,?,?)',
                                    (row['id'],row['version_id'],row['position'],row['workspace_id'],model,digest,json.dumps(vector)))
                    vectors[(row['version_id'],row['position'])]=vector
        scores={}
        query_norm=math.sqrt(sum(value*value for value in query_vector))
        for identity,vector in vectors.items():
            if len(vector)!=len(query_vector):
                raise ValueError('Размерность локальных векторов изменилась; выберите новую версию модели.')
            norm=math.sqrt(sum(value*value for value in vector))
            scores[identity]=sum(left*right for left,right in zip(query_vector,vector))/(query_norm*norm) if norm else 0
        return scores

    def semantic_search(self,query,embedder,workspace_id=1,include_archive=True,include_trash=False,limit=8,
                        model_version='qwen3-embedding:0.6b'):
        return self.search(query,workspace_id,include_archive,include_trash,limit,embedder,model_version)

    def resolve(self,note_id,revision,workspace_id=1,include_trash=False,include_archive=True):
        workspace_id=self._workspace(workspace_id)
        rows=self.store.rows('''SELECT n.id,n.title,n.body,n.workspace_id,n.deleted,n.archived,n.updated_at,
                v.id AS version_id,v.revision,v.content_hash,v.recorded_at
            FROM notes n JOIN note_versions v ON v.note_id=n.id
            WHERE n.id=? AND v.revision=? AND n.workspace_id=? AND v.workspace_id=?
                AND (? OR n.deleted=0) AND (? OR n.archived=0)''',
                             (note_id,revision,workspace_id,workspace_id,int(include_trash),int(include_archive)))
        if not rows or _hash(rows[0]['title'],rows[0]['body'])!=rows[0]['content_hash']:
            return None
        row=dict(rows[0])
        row['body']=' '.join(plain_body(row['body']).split())
        row['state']='trash' if row['deleted'] else 'archive' if row['archived'] else 'active'
        row['retrieval']=self.retrieval_mode
        return row
