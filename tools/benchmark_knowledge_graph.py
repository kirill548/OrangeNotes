"""Synthetic incremental-index benchmark; never touches the user database.

Run: python -m tools.benchmark_knowledge_graph --notes 550 --output work/v015/index-benchmark.json
"""
import argparse
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path
from app.database.store import Store
from app.services.knowledge_graph import KnowledgeGraph


def benchmark(notes=550, repetitions=20):
    if notes < 500 or repetitions < 1:
        raise ValueError('Use at least 500 notes and one repetition')
    with tempfile.TemporaryDirectory(prefix='orange-graph-benchmark-') as directory:
        store = Store(Path(directory)/'notes.sqlite3')
        try:
            with store.db:
                for i in range(notes):
                    store.db.execute('INSERT INTO notes(title,body,workspace_id,updated_at) VALUES(?,?,1,?)',
                        (f'Note {i}', f'<p>Project {i}: [[Note {(i+1)%notes}]] [[Note {(i+2)%notes}|next]]</p>', '2026-10-13'))
            graph = KnowledgeGraph(store.path)
            start = time.perf_counter(); graph.refresh(); initial_ms=(time.perf_counter()-start)*1000
            initial_stats = dict(graph.last_refresh_stats)
            durations=[]; counts=[]
            for i in range(repetitions):
                with store.db:
                    store.db.execute('UPDATE notes SET body=? WHERE id=1', (f'Edited {i}: [[Note 3]]',))
                start=time.perf_counter(); graph.refresh(); durations.append((time.perf_counter()-start)*1000)
                counts.append(graph.last_refresh_stats['rebuilt_sources'])
            return {'platform':platform.platform(),'python':platform.python_version(),'notes':notes,
                    'repetitions':repetitions,'initial_ms':initial_ms,'initial_stats':initial_stats,
                    'single_edit_ms':{'p50':statistics.median(durations),'p95':sorted(durations)[max(0,int(.95*len(durations))-1)],'max':max(durations)},
                    'single_edit_rebuilt_sources':counts,'foreign_key_errors':len(store.db.execute('PRAGMA foreign_key_check').fetchall())}
        finally:
            store.db.close()


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--notes',type=int,default=550)
    parser.add_argument('--repetitions',type=int,default=20); parser.add_argument('--output',type=Path)
    args=parser.parse_args(); result=benchmark(args.notes,args.repetitions)
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(text+'\n',encoding='utf-8')
    print(text)


if __name__=='__main__': main()
