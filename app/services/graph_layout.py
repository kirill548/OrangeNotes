"""Bounded, deterministic force layout with grid-aggregated repulsion.

At most 144 repulsion cells per node replace quadratic pair comparisons.
The caller runs this CPU work off the GUI thread and can cancel each iteration.
"""
import math


def force_layout(node_ids, edges, initial=None, iterations=45, cancelled=None):
    ids = list(dict.fromkeys(node_ids))
    if not ids:
        return {}
    initial = initial or {}
    extent = max(200.0, math.sqrt(len(ids)) * 55)
    positions = {}
    occupied = set()
    for index, key in enumerate(ids):
        angle = index * 2.399963229728653
        radius = math.sqrt((index + 1) / len(ids)) * extent * .7
        point = tuple(initial.get(key, (radius * math.cos(angle), radius * math.sin(angle))))
        if point in occupied:
            point = (point[0] + .1 * math.cos(angle), point[1] + .1 * math.sin(angle))
        occupied.add(point)
        positions[key] = point
    valid_edges = [(a, b) for a, b in edges if a in positions and b in positions and a != b]
    ideal = 80.0
    steps = min(100, max(0, iterations))
    for step in range(steps):
        if cancelled and cancelled():
            return positions
        cells = {}
        node_cells = {}
        for key, (x, y) in positions.items():
            cell = (min(11, max(0, int((x + extent) / (2 * extent) * 12))),
                    min(11, max(0, int((y + extent) / (2 * extent) * 12))))
            count, sx, sy = cells.get(cell, (0, 0., 0.))
            cells[cell] = (count + 1, sx + x, sy + y)
            node_cells[key] = cell
        forces = {}
        aggregates = list(cells.items())
        for key, (x, y) in positions.items():
            fx, fy = -x * .035, -y * .035
            for cell, (count, sx, sy) in aggregates:
                if cell == node_cells[key]:
                    count, sx, sy = count - 1, sx - x, sy - y
                if not count:
                    continue
                cx, cy = sx / count, sy / count
                dx, dy = x - cx, y - cy
                distance2 = max(100., dx * dx + dy * dy)
                force = ideal * ideal * count / distance2
                fx += dx * force
                fy += dy * force
            forces[key] = [fx, fy]
        for a, b in valid_edges:
            dx, dy = positions[b][0] - positions[a][0], positions[b][1] - positions[a][1]
            distance = math.hypot(dx, dy)
            factor = distance / ideal
            forces[a][0] += dx * factor
            forces[a][1] += dy * factor
            forces[b][0] -= dx * factor
            forces[b][1] -= dy * factor
        temperature = 24 * (1 - step / max(1, steps)) + .5
        for key, (fx, fy) in forces.items():
            magnitude = max(1., math.hypot(fx, fy))
            scale = min(temperature, magnitude) / magnitude
            x, y = positions[key]
            positions[key] = (max(-extent, min(extent, x + fx * scale)),
                              max(-extent, min(extent, y + fy * scale)))
    return positions
