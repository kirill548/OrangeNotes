"""Opt-in >100 MiB synthetic SQLite backup; closes handles before cleanup."""
from contextlib import closing
from pathlib import Path
import json, sqlite3, sys, tempfile, time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.database.store import Store
from app.services.database_restore import validate_backup

def main():
    with tempfile.TemporaryDirectory(prefix='orange-large-backup-') as folder:
        path=Path(folder); store=Store(path/'source.sqlite3')
        try:
            note=store.create_note()
            store.save_note(note,'Synthetic >100MiB','x'*(101*1024*1024),None,False,'')
            started=time.perf_counter(); store.backup_to(path/'backup.sqlite3')
            elapsed=time.perf_counter()-started
            checked=validate_backup(path/'backup.sqlite3')
            size=(path/'backup.sqlite3').stat().st_size
            assert size>100*1024*1024 and checked['notes']==1
            with closing(sqlite3.connect(path/'backup.sqlite3')) as db:
                assert db.execute('SELECT length(body) FROM notes').fetchone()[0]==101*1024*1024
            result={'backup_bytes':size,'backup_seconds':elapsed,'validation':'PASS','body_bytes':101*1024*1024,'scope':'actual Store.backup_to / validate_backup; synthetic disk file, not generic text import'}
        finally:
            store.db.close()
    result['temporary_files_removed']=True
    output=ROOT/'work/total-stress/large-backup.json';output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
