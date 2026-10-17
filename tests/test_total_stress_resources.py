"""Deterministic lifetime assertions, plus bounded synthetic native counters."""
from tools.total_stress_resources import graph_churn_probe


def test_repeated_workspace_switch_and_deletion_releases_graph_workers():
    result = graph_churn_probe(cycles=20, nodes=250)
    assert result['traced_peak_bytes'] < 64 * 1024 ** 2
    for sample in result['samples']:
        assert sample['live_views'] == 0
        assert sample['live_child_threads'] == 0
    first, last = result['samples'][0], result['samples'][-1]
    assert last['traced_bytes'] - first['traced_bytes'] < 2 * 1024 ** 2
    if first['handles_or_fds'] is not None and last['handles_or_fds'] is not None:
        assert last['handles_or_fds'] - first['handles_or_fds'] <= 8
    if first['rss_bytes'] is not None and last['rss_bytes'] is not None:
        assert last['rss_bytes'] - first['rss_bytes'] < 64 * 1024 ** 2
