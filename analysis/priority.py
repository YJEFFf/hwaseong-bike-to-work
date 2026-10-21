"""Policy comparisons use one explicit resource cap and simultaneous bundles."""
import numpy as np


def select_length_budget(ids, lengths, scores, budget, rule):
    if rule not in {"short", "reach", "bottom20"} or not np.isfinite(budget) or budget < 0:
        raise ValueError("invalid rule or length budget")
    ids = list(ids)
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate candidates")
    if any(not np.isfinite(lengths[c]) or lengths[c] <= 0 for c in ids):
        raise ValueError("positive finite link lengths required")
    if rule != "short" and any(not np.isfinite(scores[c]) or scores[c] < 0 for c in ids):
        raise ValueError("finite nonnegative individual scores required")
    # Deterministic, explicit tie-break. Do not exceed the common cap by
    # including all ties. Keep zero-score links: pairs may have positive synergy.
    ordered = sorted(ids, key=lambda c: ((0 if rule == "short" else -scores[c]), lengths[c], c))
    chosen, used = [], 0.
    for c in ordered:
        if used + lengths[c] <= budget + 1e-8:
            chosen.append(c)
            used += lengths[c]
    return chosen, used


def bundle_gain(network, links, cutoff, graph=None):
    before = network.approach_distances(graph=graph)
    after = network.approach_distances(graph=network.with_links(links, graph))
    if np.any((before <= cutoff) & (after > cutoff)):
        raise AssertionError("adding a bundle revoked an existing route")
    return (before > cutoff) & (after <= cutoff)
