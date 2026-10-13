"""Workspace-scoped wiki links; SQLite snapshots keep indexing deterministic."""
import html
import re
import sqlite3
import threading
from contextlib import contextmanager
from html.parser import HTMLParser

_PATTERN = re.compile(r'(?<![\\\[])\[\[([^\[\]\n]+?)\]\](?!\])')

def key(title):
    return ' '.join(title.split()).casefold()

class _Visible(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[]; self.hidden=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','head','code','pre'): self.hidden+=1
        elif not self.hidden and tag in ('p','br','div','li'): self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','head','code','pre') and self.hidden: self.hidden-=1
    def handle_data(self,data):
        if not self.hidden: self.parts.append(data)

def visible_text(body):
    p=_Visible(); p.feed(body or ''); text=''.join(p.parts)
    text=re.sub(r'```.*?```|`[^`\n]*`', '', text, flags=re.S)
    return text

def parse_wiki_links(body):
    text=visible_text(body)
    result=[]
    for match in _PATTERN.finditer(text):
        target, sep, alias=match.group(1).partition('|')
        target=target.strip(); alias=alias.strip()
        if not target or (sep and not alias): continue
        result.append({'target_title':target,'link_text':alias if sep else target,
                       'context_snippet':text[max(0,match.start()-80):match.end()+80].strip()})
    return result

def link_trigger_sql():
    """Exact trigger definitions are shared with the backup SQL allowlist."""
    result = {}
    for name, event, rows in (
        ('links_insert', 'INSERT', ('NEW',)),
        ('links_update', 'UPDATE OF title,body,workspace_id,deleted,archived', ('OLD', 'NEW')),
        ('links_delete', 'DELETE', ('OLD',)),
    ):
        statements = [f'INSERT OR IGNORE INTO note_links_dirty(note_id) VALUES({rows[-1]}.id);']
        for row in rows:
            condition = ' WHERE OLD.title != NEW.title OR OLD.workspace_id != NEW.workspace_id OR OLD.deleted != NEW.deleted' if name == 'links_update' else ''
            statements.append(f'INSERT OR IGNORE INTO note_links_title_dirty(workspace_id,title) SELECT {row}.workspace_id,{row}.title'+condition+';')
        result[name] = f'CREATE TRIGGER {name} AFTER {event} ON notes BEGIN '+ ' '.join(statements)+' END'
    return result


def migrate(db):
    legacy = db.execute("SELECT 1 FROM sqlite_master WHERE name='note_links_dirty'").fetchone()
    incremental = legacy and 'note_id' in {r[1] for r in db.execute('PRAGMA table_info(note_links_dirty)')}
    db.executescript("""
    CREATE TABLE IF NOT EXISTS note_links (
      id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
      target_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
      target_title TEXT NOT NULL, link_text TEXT NOT NULL, context_snippet TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS note_links_source ON note_links(source_id);
    CREATE INDEX IF NOT EXISTS note_links_target ON note_links(target_id);
    """)
    if not incremental:
        for name in link_trigger_sql(): db.execute('DROP TRIGGER IF EXISTS '+name)
        db.execute('DROP TABLE IF EXISTS note_links_dirty')
    if 'target_key' not in {r[1] for r in db.execute('PRAGMA table_info(note_links)')}:
        db.execute("ALTER TABLE note_links ADD COLUMN target_key TEXT NOT NULL DEFAULT ''")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS note_links_dirty(note_id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS note_links_title_dirty(workspace_id INTEGER NOT NULL,title TEXT NOT NULL,PRIMARY KEY(workspace_id,title));
    CREATE TABLE IF NOT EXISTS note_links_titles(note_id INTEGER PRIMARY KEY,workspace_id INTEGER NOT NULL,title_key TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS note_links_titles_lookup ON note_links_titles(workspace_id,title_key);
    CREATE INDEX IF NOT EXISTS note_links_key ON note_links(target_key,source_id);
    """)
    if not incremental:
        db.execute('INSERT OR IGNORE INTO note_links_dirty SELECT id FROM notes')
        db.execute('INSERT OR IGNORE INTO note_links_title_dirty SELECT workspace_id,title FROM notes')
        for sql in link_trigger_sql().values(): db.execute(sql)

def rename_references(db, note_id, old_title, new_title, workspace_id):
    if not old_title.strip() or key(old_title)==key(new_title): return
    matches=[row for row in db.execute('SELECT id,title FROM notes WHERE workspace_id=? AND deleted=0',(workspace_id,)) if key(row[1])==key(old_title)]
    if len(matches)!=1: return
    # Do not create ambiguous destinations when another note already owns new title.
    others=db.execute('SELECT title FROM notes WHERE workspace_id=? AND deleted=0 AND id!=?',(workspace_id,note_id)).fetchall()
    if any(key(row[0])==key(new_title) for row in others): return
    for row in db.execute('SELECT id,body FROM notes WHERE workspace_id=? AND deleted=0',(workspace_id,)).fetchall():
        updated=rewrite_wiki_titles(row[1],old_title,new_title)
        if updated!=row[1]: db.execute('UPDATE notes SET body=? WHERE id=?',(updated,row[0]))
    return True

def rewrite_wiki_titles(body,old_title,new_title):
    # HTML tokens stay byte-identical; only visible text fragments are rewritten.
    hidden=0; pieces=[]
    for part in re.split(r'(<[^>]*>)',body):
        if part.startswith('<'):
            closing=re.match(r'</(code|pre|script|style)\b',part,re.I)
            opening=re.match(r'<(code|pre|script|style)\b',part,re.I)
            if closing: hidden=max(0,hidden-1)
            elif opening: hidden+=1
            pieces.append(part); continue
        if hidden: pieces.append(part); continue
        def replace(m):
            target,sep,alias=m.group(1).partition('|')
            if key(html.unescape(target))!=key(old_title): return m.group(0)
            return '[['+html.escape(new_title,quote=False)+(('|'+alias) if sep else '')+']]'
        # Inline/fenced Markdown code remains untouched.
        chunks=re.split(r'(```.*?```|`[^`\n]*`)',part,flags=re.S)
        pieces.append(''.join(chunk if chunk.startswith('`') else _PATTERN.sub(replace,chunk) for chunk in chunks))
    updated=''.join(pieces)
    return updated

class KnowledgeGraph:
    def __init__(self,database_path): self.path=str(database_path)
    @contextmanager
    def _connect(self):
        db=sqlite3.connect(self.path,timeout=5); db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db: yield db
        finally: db.close()
    def close(self):
        pass
    def refresh(self):
        # Each write lock covers at most 128 parsed bodies. Coalesced title changes
        # invalidate only sources referencing their normalized old/new names.
        rebuilt = 0; batches = 0
        while True:
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                changed = [r[0] for r in db.execute('SELECT note_id FROM note_links_dirty')]
                title_changes = db.execute('SELECT workspace_id,title FROM note_links_title_dirty').fetchall()
                if title_changes:
                    # Update title lookup before resolving any affected sources.
                    for note_id in changed:
                        db.execute('DELETE FROM note_links_titles WHERE note_id=?', (note_id,))
                        row = db.execute('SELECT workspace_id,title,deleted FROM notes WHERE id=?', (note_id,)).fetchone()
                        if row and not row['deleted']:
                            db.execute('INSERT INTO note_links_titles VALUES(?,?,?)', (note_id,row['workspace_id'],key(row['title'])))
                    for scope, title in {(r[0],key(r[1])) for r in title_changes}:
                        db.execute('INSERT OR IGNORE INTO note_links_dirty SELECT DISTINCT l.source_id FROM note_links l JOIN notes n ON n.id=l.source_id WHERE n.workspace_id=? AND l.target_key=?', (scope,title))
                    db.execute('DELETE FROM note_links_title_dirty')
                batch = [r[0] for r in db.execute('SELECT note_id FROM note_links_dirty ORDER BY note_id LIMIT 128')]
                if not batch: break
                for note_id in batch:
                    note = db.execute('SELECT body,workspace_id FROM notes WHERE id=?',(note_id,)).fetchone()
                    db.execute('DELETE FROM note_links WHERE source_id=?',(note_id,))
                    if note:
                        for link in parse_wiki_links(note['body']):
                            target_key = key(link['target_title'])
                            candidates = db.execute('SELECT note_id FROM note_links_titles WHERE workspace_id=? AND title_key=? LIMIT 2',(note['workspace_id'],target_key)).fetchall()
                            target = candidates[0][0] if len(candidates)==1 else None
                            db.execute('INSERT INTO note_links(source_id,target_id,target_title,link_text,context_snippet,target_key) VALUES(?,?,?,?,?,?)', (note_id,target,link['target_title'],link['link_text'],link['context_snippet'],target_key))
                    db.execute('DELETE FROM note_links_dirty WHERE note_id=?',(note_id,))
                rebuilt += len(batch); batches += 1
        self.last_refresh_stats = {'rebuilt_sources': rebuilt, 'batches': batches}
    def _notes(self,workspace_id,include_archive=True,include_trash=False):
        self.refresh()
        with self._connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM notes WHERE workspace_id=?',(workspace_id,)) if (include_archive or not row['archived']) and (include_trash or not row['deleted'])]
    def graph(self,workspace_id,include_archive=True,include_trash=False):
        nodes=self._notes(workspace_id,include_archive,include_trash); ids={n['id'] for n in nodes}
        with self._connect() as db: edges=[dict(row) for row in db.execute('SELECT * FROM note_links') if row['source_id'] in ids and row['target_id'] in ids]
        return {'nodes':nodes,'edges':edges}
    def _related(self,note_id,workspace_id,back,include_archive=True,include_trash=False):
        self.refresh()
        with self._connect() as db:
            seed=db.execute('SELECT archived,deleted FROM notes WHERE id=? AND workspace_id=?',(note_id,workspace_id)).fetchone()
            if not seed or (seed['archived'] and not include_archive) or (seed['deleted'] and not include_trash): return []
            related_column='source_id' if back else 'target_id'
            seed_column='target_id' if back else 'source_id'
            rows=db.execute(f"SELECT n.*,l.context_snippet,l.link_text FROM note_links l JOIN notes n ON n.id=l.{related_column} WHERE l.{seed_column}=? AND n.workspace_id=? AND (? OR n.archived=0) AND (? OR n.deleted=0) ORDER BY l.id",(note_id,workspace_id,int(include_archive),int(include_trash)))
            result=[]; seen=set()
            for row in rows:
                if row['id'] not in seen: result.append(dict(row)); seen.add(row['id'])
            return result
    def backlinks(self,note_id,workspace_id,include_archive=True,include_trash=False): return self._related(note_id,workspace_id,True,include_archive,include_trash)
    def linked_notes(self,note_id,workspace_id,include_archive=True,include_trash=False): return self._related(note_id,workspace_id,False,include_archive,include_trash)
    def unlinked_mentions(self,note_id,workspace_id):
        nodes=self._notes(workspace_id); current=next((n for n in nodes if n['id']==note_id),None)
        if not current or not current['title'].strip(): return []
        linked={n['id'] for n in self.backlinks(note_id,workspace_id)}
        pattern=re.compile(r'(?<!\w)'+re.escape(current['title'])+r'(?!\w)',re.I)
        result=[]
        for n in nodes:
            if n['id']==note_id or n['id'] in linked: continue
            text=_PATTERN.sub('',visible_text(n['body'])); match=pattern.search(text)
            if match: result.append(dict(n,context_snippet=text[max(0,match.start()-80):match.end()+80]))
        return result
    def context(self,note_id,workspace_id,depth=2,limit=20):
        if type(depth) is not int or not 0<=depth<=2 or type(limit) is not int or not 0<=limit<=100: raise ValueError('Invalid graph bounds')
        g=self.graph(workspace_id); nodes={n['id']:n for n in g['nodes']}
        if note_id not in nodes: return []
        seen={note_id}; frontier=[note_id]; result=[]
        for _ in range(depth):
            next_frontier=[]
            for current in frontier:
                for e in g['edges']:
                    if current not in (e['source_id'],e['target_id']): continue
                    other=e['target_id'] if e['source_id']==current else e['source_id']
                    if other not in seen and len(result)<limit:
                        seen.add(other); next_frontier.append(other); result.append(nodes[other])
            frontier=next_frontier
        return result

class KnowledgeGraphIndexer:
    def __init__(self,path,interval=0.5):
        self.graph=KnowledgeGraph(path); self.interval=interval; self._stop=threading.Event(); self.thread=None; self.last_error=None
    def start(self):
        if self.thread and self.thread.is_alive(): return
        self._stop.clear(); self.thread=threading.Thread(target=self._run,daemon=True,name='note-links-indexer'); self.thread.start()
    def _run(self):
        while not self._stop.is_set():
            try: self.graph.refresh(); self.last_error=None
            except sqlite3.Error as exc: self.last_error=str(exc)
            self._stop.wait(self.interval)
    def stop(self):
        self._stop.set()
        if self.thread: self.thread.join(timeout=6)
