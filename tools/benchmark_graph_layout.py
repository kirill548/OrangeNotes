"""Reproducible CPU-only synthetic layout benchmark; no user notes or GUI."""
import argparse
import json
import platform
import statistics
import time
from pathlib import Path

from app.services.graph_layout import force_layout


def benchmark(count, repeats=3, iterations=45):
    nodes = list(range(count))
    edges = [(i, (i+1) % count) for i in nodes]
    edges += [(i, (i+17) % count) for i in nodes]
    samples = []
    positions = {}
    for _ in range(repeats):
        started = time.perf_counter()
        positions = force_layout(nodes, edges, iterations=iterations)
        samples.append((time.perf_counter()-started)*1000)
    return {'nodes':count, 'edges':len(edges), 'iterations':iterations,
            'samples_ms':samples, 'median_ms':statistics.median(samples),
            'max_ms':max(samples), 'position_count':len(positions)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nodes', type=int, nargs='+', default=[500,1000])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--iterations', type=int, default=45)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not all(1 <= n <= 10000 for n in args.nodes) or not 1 <= args.repeats <= 10 or not 1 <= args.iterations <= 100:
        parser.error('Use 1..10000 nodes, 1..10 repeats and 1..100 iterations.')
    result = {'platform':platform.platform(), 'python':platform.python_version(),
              'workload':'synthetic ring with additional forward edges',
              'results':[benchmark(n,args.repeats,args.iterations) for n in args.nodes]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()
