"""Opt-in synthetic Core stress probe. Never opens a user database."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def worker(kind, directory, count):
    from app.database.store import Store, plain_body
    path = Path(directory) / (kind + '.sqlite3')
    start = time.perf_counter()
    store = Store(path)
    result = {'kind': kind}
    if kind == 'seeded':
        with store.db:
            store.db.executemany('INSERT INTO notes(title,body,updated_at) VALUES (?,?,?)', ((f'Synthetic {i}', f'<p>Текст {i} 🐝</p>', '2026-10-17T00:00:00') for i in range(count)))
        result['seed_method'] = 'raw SQLite executemany into actual Store schema, not Store.create_note benchmark'
        result['rows'] = store.rows('SELECT COUNT(*) FROM notes')[0][0]
        query = time.perf_counter()
        result['read_rows'] = len(store.rows('SELECT id,title,body FROM notes ORDER BY updated_at DESC'))
        result['read_all_seconds'] = time.perf_counter() - query
        update = time.perf_counter()
        for note in range(1, 11):
            store.save_note(note, f'Renamed {note}', '<p>Edited</p>', None, False, '')
        result['ten_real_api_renames_seconds'] = time.perf_counter()-update
        result['integrity'] = store.rows('PRAGMA integrity_check')[0][0]
        result['fk_errors'] = len(store.rows('PRAGMA foreign_key_check'))
    elif kind == 'notes':
        for i in range(count):
            note = store.create_note()
            store.save_note(note, f'Synthetic {i}', f'<p>Текст {i} 🐝 [[Target]]</p>', None, False, 'stress')
        result['rows'] = store.rows('SELECT COUNT(*) FROM notes')[0][0]
        result['fk_errors'] = len(store.rows('PRAGMA foreign_key_check'))
        result['integrity'] = store.rows('PRAGMA integrity_check')[0][0]
        query = time.perf_counter()
        result['read_rows'] = len(store.rows('SELECT id,title,body FROM notes ORDER BY updated_at DESC'))
        result['read_all_seconds'] = time.perf_counter() - query
    elif kind == 'large':
        body = 'x' * (101 * 1024 * 1024)
        note = store.create_note()
        store.save_note(note, 'Synthetic oversized', body, None, False, '')
        result['stored_bytes'] = store.rows('SELECT length(body) FROM notes WHERE id=?', (note,))[0][0]
        result['plain_bytes'] = len(plain_body(body))
        result['integrity'] = store.rows('PRAGMA integrity_check')[0][0]
    elif kind == 'parallel':
        ids = [store.create_note() for _ in range(4)]
        store.db.close()
        def write(index):
            local = Store(path)
            try:
                for i in range(count):
                    local.save_note(ids[index], '', f'<p>Writer {index} update {i}</p>', None, False, '')
                return count
            finally:
                local.db.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            result['writes'] = sum(pool.map(write, range(4)))
        store = Store(path)
        result['fk_errors'] = len(store.rows('PRAGMA foreign_key_check'))
        result['integrity'] = store.rows('PRAGMA integrity_check')[0][0]
    elif kind == 'markdown':
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QTextDocument
        app = QApplication.instance() or QApplication([])
        document = QTextDocument()
        markdown = '# Пример 🐝\n\n- One\n  - Two\n    - Three\n\n| A | B |\n|---|---|\n' + '| Ё & < > | **жирный** 🧑🏽‍💻 |\n' * 2000
        document.setMarkdown(markdown)
        document.setTextWidth(640)
        result.update(characters=document.characterCount(), height=document.size().height(), emoji_preserved='🐝' in document.toPlainText())
        document.toHtml()
        del app
    result['elapsed_seconds'] = time.perf_counter() - start
    if 'writes' in result:
        result['writes_per_second'] = result['writes'] / result['elapsed_seconds']
    result['database_bytes'] = sum(p.stat().st_size for p in Path(directory).glob('*') if p.is_file())
    store.db.close()
    return result


def run(output):
    results = []
    for kind, count in [('seeded', 50000), ('notes', 50000), ('large', 1), ('parallel', 250), ('markdown', 1)]:
        with tempfile.TemporaryDirectory(prefix='orange-core-stress-') as directory:
            started = time.perf_counter()
            try:
                proc = subprocess.run([sys.executable, __file__, '--worker', kind, '--directory', directory, '--count', str(count)], capture_output=True, text=True, encoding='utf-8', timeout=120)
                results.append(json.loads(proc.stdout) if proc.returncode == 0 else {'kind': kind, 'error': proc.stderr[-3000:], 'returncode': proc.returncode})
            except subprocess.TimeoutExpired:
                results.append({'kind': kind, 'timeout_seconds': time.perf_counter()-started})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', choices=['seeded', 'notes', 'large', 'parallel', 'markdown'])
    parser.add_argument('--directory')
    parser.add_argument('--count', type=int, default=10)
    parser.add_argument('--output', type=Path, default=ROOT / 'work/total-stress/core-report.json')
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker, args.directory, args.count), ensure_ascii=False))
    else:
        run(args.output)
