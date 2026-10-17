"""Bounded synthetic resource probe; never opens a user database."""
import argparse
import gc
import importlib.util
import json
from pathlib import Path
import sys
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def graph_churn_probe(cycles=30, nodes=1000):
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
    import shiboken6
    from app.widgets.knowledge_graph import GraphView
    spec = importlib.util.spec_from_file_location(
        '_graph_stress_helpers', ROOT / 'tests/test_graph_ui_stress.py')
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    app = QApplication.instance() or QApplication([])
    graph = helpers.dense_graph(nodes)

    def cycle():
        view = GraphView()
        view.set_graph(graph)
        workers = list(view._workers)
        view.set_graph({'workspace_id': 2, 'nodes': [], 'edges': []})
        view.deleteLater()
        helpers.drain(app)
        assert not shiboken6.isValid(view)
        assert all(not shiboken6.isValid(worker) for worker in workers)

    for _ in range(3):
        cycle()
    samples = []
    start, cpu = time.perf_counter(), time.process_time()
    tracemalloc.start()
    try:
        for index in range(cycles):
            cycle()
            if (index + 1) % 10 == 0 or index + 1 == cycles:
                gc.collect()
                sample = helpers.process_resources()
                sample.update(cycles=index + 1,
                              traced_bytes=tracemalloc.get_traced_memory()[0],
                              live_views=sum(isinstance(w, GraphView) for w in app.allWidgets()),
                              live_child_threads=len(app.findChildren(QThread)))
                samples.append(sample)
    finally:
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    return {'nodes': nodes, 'edges': len(graph['edges']), 'cycles': cycles,
            'warmup_cycles': 3, 'wall_seconds': time.perf_counter() - start,
            'cpu_seconds': time.process_time() - cpu,
            'traced_peak_bytes': peak, 'samples': samples,
            'scope': 'Qt offscreen synthetic lifecycle; not a device soak or proof of zero native leaks'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = graph_churn_probe()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
