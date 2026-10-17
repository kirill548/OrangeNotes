"""Small deterministic regressions; opt-in bulk probe lives in tools/."""
import sqlite3

import pytest

from app.database.store import Store, plain_body
from app.services.knowledge_graph import parse_wiki_links


def test_core_unicode_and_sql_payload_roundtrip(tmp_path):
    store = Store(tmp_path / 'synthetic.sqlite3')
    try:
        note = store.create_note()
        title = "'); DROP TABLE notes; -- 🐝"
        body = '<p>Ёж 🧑🏽‍💻 &amp; [[Цель|Переход]]</p>'
        store.save_note(note, title, body, None, True, 'метка, метка')
        row = store.rows('SELECT title,body FROM notes WHERE id=?', (note,))[0]
        assert tuple(row) == (title, body)
        assert len(store.rows('SELECT * FROM note_tags')) == 1
        assert parse_wiki_links(body)[0]['target_title'] == 'Цель'
        assert '🧑🏽‍💻' in plain_body(body)
    finally:
        store.db.close()


def test_core_failed_foreign_key_save_is_atomic(tmp_path):
    store = Store(tmp_path / 'synthetic.sqlite3')
    try:
        note = store.create_note()
        store.save_note(note, 'before', '<p>before</p>', None, False, 'original')
        with pytest.raises(sqlite3.IntegrityError):
            store.save_note(note, 'after', '<p>after</p>', 9999999, False, 'replaced')
        row = store.rows('SELECT title,body FROM notes WHERE id=?', (note,))[0]
        assert tuple(row) == ('before', '<p>before</p>')
        assert store.rows('SELECT name FROM tags')[0][0] == 'original'
        assert not store.rows('PRAGMA foreign_key_check')
    finally:
        store.db.close()


def test_core_invalid_unicode_does_not_partial_save(tmp_path):
    store = Store(tmp_path / 'synthetic.sqlite3')
    try:
        note = store.create_note()
        with pytest.raises(ValueError, match='недопустимые символы'):
            store.save_note(note, 'change', '\ud800', None, False, 'new')
        assert store.rows('SELECT title,body FROM notes')[0][:] == ('', '')
        assert not store.rows('SELECT * FROM tags')
    finally:
        store.db.close()


def test_core_rename_skips_unlinked_bodies_and_preserves_links(tmp_path, monkeypatch):
    import app.services.knowledge_graph as graph
    store = Store(tmp_path / 'synthetic.sqlite3')
    try:
        target = store.create_note()
        store.save_note(target, 'Original', '<p>unlinked</p>', None, False, '')
        linked = store.create_note()
        store.save_note(linked, 'Source', '<p>[[Original|Alias]]</p>', None, False, '')
        unrelated = store.create_note()
        store.save_note(unrelated, 'Other', '<p>large unrelated text</p>', None, False, '')
        calls = []
        rewrite = graph.rewrite_wiki_titles
        def spy(body, old, new):
            calls.append(body)
            return rewrite(body, old, new)
        monkeypatch.setattr(graph, 'rewrite_wiki_titles', spy)
        store.save_note(target, 'Renamed', '<p>unlinked</p>', None, False, '')
        assert '<p>large unrelated text</p>' not in calls
        assert store.rows('SELECT body FROM notes WHERE id=?', (linked,))[0][0] == '<p>[[Renamed|Alias]]</p>'
    finally:
        store.db.close()
