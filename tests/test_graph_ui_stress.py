"""Dense graph lifecycle tests; synthetic data only, no live profile."""
import math
import time
import gc
import json
import os
import sys
import tracemalloc

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtCore import QPointF, QPoint
from PySide6.QtWidgets import QApplication
from app.services.graph_layout import force_layout
from app.widgets.knowledge_graph import GraphView


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def dense_graph(count=2000, workspace=1):
    return {'workspace_id': workspace,
            'nodes': [{'id': i, 'title': f'Заметка {i}', 'workspace_id': workspace}
                      for i in range(1, count + 1)],
            'edges': [{'source_id': i, 'target_id': ((i + offset - 1) % count) + 1}
                      for i in range(1, count + 1) for offset in (1, 2, 17, 53)]}


def drain(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def wait_idle(app, view, seconds=10):
    end = time.monotonic() + seconds
    while view._workers and time.monotonic() < end:
        app.processEvents()
        time.sleep(.005)
    assert not view._workers
    drain(app)


def test_dense_rapid_filter_workspace_switch_keeps_last_generation(app):
    view = GraphView()
    view.resize(700, 500)
    view.show()
    original = dense_graph()
    try:
        view.set_graph(original, 1)
        stale = view._generation
        for iteration in range(12):
            subset = original['nodes'][iteration:iteration + 100]
            view.set_graph({'workspace_id': 1, 'nodes': subset,
                            'edges': original['edges'][:400]}, subset[0]['id'])
            view.hide()
            assert not view._timer.isActive()
            view.show()
            assert len(view._workers) <= 1
        final = dense_graph(150, 2)
        view.set_graph(final, 150)
        view._layout_ready(stale, {1: (float('nan'), float('nan'))})
        assert not view._targets
        wait_idle(app, view)
        assert set(view._targets) == set(range(1, 151))
        assert view._workspace == 2
        for _ in range(130):
            view._animate()
        assert all(math.isfinite(v) for point in view._positions.values() for v in point)
        assert not view._timer.isActive()
        assert view._node_items[150][0].brush().color().name() == '#ff890b'
        assert len(view.findChildren(QThread)) == 0
    finally:
        view.close()
        drain(app)


def test_empty_filter_restores_positions_and_wheel_zoom(app):
    view = GraphView()
    view.resize(700, 500)
    view.show()
    graph = dense_graph(120)
    try:
        view.set_graph(graph)
        view.stop_layout()
        drain(app)
        before = view._positions.copy()
        wheel = QWheelEvent(QPointF(100, 100), QPointF(100, 100), QPoint(),
                            QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                            Qt.NoScrollPhase, False)
        view.wheelEvent(wheel)
        transform, zoom = view.transform().m11(), view._zoom
        view.set_graph({'workspace_id': 1, 'nodes': [], 'edges': []})
        view.set_graph(graph)
        assert view.transform().m11() == transform
        assert view._zoom == zoom
        assert view._positions == before
    finally:
        view.close()
        drain(app)


def test_deferred_delete_joins_dense_worker_without_close_event(app):
    import shiboken6
    view = GraphView()
    view.set_graph(dense_graph())
    worker = view._workers[0]
    view.deleteLater()
    drain(app)
    assert not shiboken6.isValid(view)
    assert not shiboken6.isValid(worker)


def test_cancelled_dense_force_layout_stops_at_iteration_boundary():
    graph = dense_graph()
    calls = []
    def cancelled():
        calls.append(1)
        return len(calls) >= 2
    positions = force_layout(range(1, 2001),
                             [(e['source_id'], e['target_id']) for e in graph['edges']],
                             iterations=100, cancelled=cancelled)
    assert len(calls) == 2
    assert len(positions) == 2000
    assert all(math.isfinite(v) for point in positions.values() for v in point)


def test_dense_reader_and_layout_while_background_indexer_writes(app, tmp_path):
    from app.database.store import Store
    from app.services.knowledge_graph import KnowledgeGraphIndexer
    from app.widgets.knowledge_graph import KnowledgeWorker
    from PySide6.QtTest import QSignalSpy
    store = Store(tmp_path / 'dense-ui.sqlite3')
    with store.db:
        store.db.executemany('INSERT INTO notes(title,body,workspace_id,updated_at) VALUES(?,?,1,?)',
            [(f'Note {i}', f'[[Note {(i + 1) % 2000}]] [[Note {(i + 17) % 2000}]]',
              '2026-10-13') for i in range(2000)])
    indexer = KnowledgeGraphIndexer(store.path, interval=.01)
    indexer.start()
    view = GraphView()
    view.show()
    readers = []
    try:
        for cycle in range(5):
            with store.db:
                store.db.executemany('UPDATE notes SET body=? WHERE id=?',
                    [(f'Changed [[Note {(i + cycle) % 2000}]]', i + 1)
                     for i in range(cycle * 20, cycle * 20 + 20)])
            reader = KnowledgeWorker(store.path, cycle + 1, 1)
            readers.append(reader)
            ready, failed = QSignalSpy(reader.ready), QSignalSpy(reader.failed)
            reader.start()
            until = time.monotonic() + 10
            while reader.isRunning() and time.monotonic() < until:
                app.processEvents()
                time.sleep(.005)
            assert reader.wait(1000)
            drain(app)
            assert failed.count() == 0
            assert ready.count() == 1
            result = ready.at(0)[0]
            assert result['note_id'] == cycle + 1
            assert len(result['graph']['nodes']) == 2000
            view.set_graph(result['graph'], cycle + 1)
            assert len(view._workers) <= 1
            reader.deleteLater()
            drain(app)
        assert store.rows('PRAGMA foreign_key_check') == []
    finally:
        view.close()
        for reader in readers:
            import shiboken6
            if shiboken6.isValid(reader):
                reader.wait()
                reader.deleteLater()
        indexer.stop()
        assert not indexer.thread.is_alive()
        assert indexer.last_error is None
        store.db.close()
        drain(app)


def process_resources():
    """Native counters only; missing counters are explicit null values."""
    rss = handles = None
    reason = None
    try:
        if sys.platform == 'win32':
            import ctypes
            kernel = ctypes.WinDLL('kernel32')
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            class PMC(ctypes.Structure):
                _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [
                    (name, ctypes.c_size_t) for name in ('peak_rss', 'rss', 'pp', 'p', 'np', 'n', 'commit', 'peak_commit')]
            memory = ctypes.WinDLL('psapi').GetProcessMemoryInfo
            memory.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_ulong]
            sample = PMC(); sample.cb = ctypes.sizeof(sample)
            if not memory(kernel.GetCurrentProcess(), ctypes.byref(sample), sample.cb):
                raise OSError('GetProcessMemoryInfo failed')
            rss = sample.rss
            value = ctypes.c_ulong()
            kernel.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            if not kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(value)):
                raise OSError('GetProcessHandleCount failed')
            handles = value.value
        elif sys.platform.startswith('linux'):
            from pathlib import Path
            rss = int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
            handles = len(list(Path('/proc/self/fd').iterdir()))
        elif sys.platform == 'darwin':
            import subprocess
            from pathlib import Path
            rss = int(subprocess.check_output(['ps', '-o', 'rss=', '-p', str(os.getpid())], timeout=5).strip()) * 1024
            handles = len(list(Path('/dev/fd').iterdir()))
        else:
            reason = 'No native resource counter implemented for this platform'
    except (OSError, ValueError) as exc:
        reason = str(exc)
    return {'rss_bytes': rss, 'handles_or_fds': handles, 'unavailable_reason': reason}


def test_repeated_dense_lifecycle_resources_are_bounded(app):
    graph = dense_graph()
    import shiboken6
    def cycle():
        view = GraphView()
        view.resize(700, 500)
        view.show()
        view.set_graph(graph)
        workers = list(view._workers)
        view.hide(); view.show()
        view.set_graph({'workspace_id': 1, 'nodes': graph['nodes'][:100],
                        'edges': graph['edges'][:300]})
        view.deleteLater()
        drain(app)
        assert not shiboken6.isValid(view)
        assert all(not shiboken6.isValid(w) for w in workers)
    for _ in range(3):
        cycle()
    tracemalloc.start()
    samples = []
    try:
        for batch in range(3):
            for _ in range(10):
                cycle()
            gc.collect()
            sample = process_resources()
            sample.update(cycles=(batch + 1) * 10,
                          traced_current_bytes=tracemalloc.get_traced_memory()[0],
                          live_graph_views=sum(isinstance(w, GraphView) for w in app.allWidgets()),
                          child_qthreads=len(app.findChildren(QThread)),
                          all_valid_qt_wrappers=len(shiboken6.getAllValidWrappers()),
                          valid_qthread_wrappers=sum(isinstance(w, QThread)
                              for w in shiboken6.getAllValidWrappers()))
            samples.append(sample)
        for sample in samples:
            assert sample['live_graph_views'] == 0
            assert sample['child_qthreads'] == 0
        if all(s['handles_or_fds'] is not None for s in samples):
            assert samples[-1]['handles_or_fds'] - samples[0]['handles_or_fds'] <= 8
        if all(s['rss_bytes'] is not None for s in samples):
            assert samples[-1]['rss_bytes'] - samples[0]['rss_bytes'] < 64 * 1024 ** 2
        assert samples[-1]['traced_current_bytes'] - samples[0]['traced_current_bytes'] < 2 * 1024 ** 2
        print('UI_STRESS_METRICS=' + json.dumps({'platform': sys.platform, 'nodes': 2000,
              'edges': 8000, 'warmup_cycles': 3, 'samples': samples,
              'limits': {'handle_growth': 8, 'rss_growth_bytes': 64 * 1024 ** 2,
                         'traced_growth_bytes': 2 * 1024 ** 2},
              'scope': 'Bounded 30-cycle test; not proof of absence of all native leaks.'}))
    finally:
        tracemalloc.stop()


def test_dense_labels_are_allocated_only_for_active_note(app):
    view = GraphView()
    graph = dense_graph()
    try:
        view.set_graph(graph, 1)
        generation = view._generation
        assert sum(label is not None for _, label in view._node_items.values()) == 1
        view.set_graph(graph, 1500)
        assert view._generation == generation
        assert view._node_items[1][1] is None
        assert view._node_items[1500][1].data(0) == 1500
        assert sum(label is not None for _, label in view._node_items.values()) == 1
        view.set_graph(graph, None)
        assert all(label is None for _, label in view._node_items.values())
        view.set_graph(dense_graph(5), 1)
        assert all(label is not None for _, label in view._node_items.values())
    finally:
        view.close()
        view.deleteLater()
        drain(app)
