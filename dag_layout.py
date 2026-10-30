"""Deterministic layered layout for native top-to-bottom DAG views."""
from collections import deque


def layers_for(node_ids, edges):
    """Return longest-path ranks; reject missing endpoints and directed cycles."""
    ids = list(node_ids)
    if len(ids) != len(set(ids)):
        raise ValueError("步骤 ID 重复")
    outgoing = {key: [] for key in ids}
    incoming = {key: 0 for key in ids}
    unique = set()
    for source, target in edges:
        if source not in outgoing or target not in outgoing:
            raise ValueError("前置步骤不存在")
        if (source, target) in unique:
            continue
        unique.add((source, target))
        outgoing[source].append(target)
        incoming[target] += 1
    ready = deque(key for key in ids if not incoming[key])
    ranks = dict.fromkeys(ids, 0)
    visited = 0
    while ready:
        source = ready.popleft()
        visited += 1
        for target in outgoing[source]:
            ranks[target] = max(ranks[target], ranks[source] + 1)
            incoming[target] -= 1
            if not incoming[target]:
                ready.append(target)
    if visited != len(ids):
        raise ValueError("步骤依赖形成循环，请调整前置步骤")
    layers = [[] for _ in range(max(ranks.values(), default=-1) + 1)]
    for key in ids:
        layers[ranks[key]].append(key)
    return layers


def layout_dag(node_ids, edges, width=208, height=88, x_gap=36, y_gap=64):
    """Arrange peers horizontally and every dependency strictly downward."""
    ids, edges = list(node_ids), list(dict.fromkeys(edges))
    layers = layers_for(ids, edges)
    parents = {key: [] for key in ids}
    for source, target in edges:
        parents[target].append(source)
    order = {key: index for layer in layers for index, key in enumerate(layer)}
    # Stable barycentric sweeps reduce crossed branches without random movement.
    for layer in layers[1:]:
        layer.sort(key=lambda key: sum(order[p] for p in parents[key]) / len(parents[key]) if parents[key] else order[key])
        for index, key in enumerate(layer):
            order[key] = index
    canvas_width = max((len(layer) * (width + x_gap) - x_gap for layer in layers), default=width)
    positions = {}
    for rank, layer in enumerate(layers):
        left = (canvas_width - (len(layer) * (width + x_gap) - x_gap)) / 2
        for index, key in enumerate(layer):
            positions[key] = (left + index * (width + x_gap), rank * (height + y_gap))
    return positions, layers
