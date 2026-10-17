"""Synthetic-only crash fixture. Never accepts an existing database."""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database.store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--phase', choices=('uncommitted', 'committed', 'writing'), required=True)
    args = parser.parse_args()
    path = Path(args.database)
    if path.exists():
        raise SystemExit('Refusing an existing database')
    store = Store(path)
    with store.db:
        store.db.execute("CREATE TABLE crash_probe(id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        store.db.execute("INSERT INTO crash_probe VALUES(0,'durable baseline')")
    store.db.execute('PRAGMA synchronous=FULL')
    store.db.execute('PRAGMA cache_size=16')
    store.db.execute('BEGIN IMMEDIATE')
    for index in range(1, 201):
        store.db.execute('INSERT INTO crash_probe VALUES(?,?)', (index, 'synthetic-' + 'x' * 8192))
        if args.phase == 'writing':
            if index == 10:
                print('READY', flush=True)
            time.sleep(.002)
    if args.phase == 'committed':
        store.db.commit()
    if args.phase != 'writing':
        print('READY', flush=True)
    time.sleep(50)


if __name__ == '__main__':
    main()
