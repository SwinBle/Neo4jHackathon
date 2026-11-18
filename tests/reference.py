"""A slow, obviously-correct matcher used only to check the real one on small pools."""

from kidneyloop.exchanges import Exchange
from kidneyloop.solver import MatchInputs


def all_exchanges(inputs: MatchInputs, max_cycle: int, max_chain: int) -> list[Exchange]:
    gives = {}
    for a in inputs.arcs:
        gives.setdefault(a.donor, []).append(a.patient)
    out: list[Exchange] = []

    def extend_cycle(path: list[str]) -> None:
        last_donor = inputs.donor_of[path[-1]]
        for nxt in gives.get(last_donor, []):
            if nxt == path[0] and len(path) >= 2:
                donors = tuple(inputs.donor_of[p] for p in path[-1:] + path[:-1])
                out.append(Exchange("cycle", donors, tuple(path)))
            elif nxt in inputs.donor_of and nxt not in path and nxt > path[0] and len(path) < max_cycle:
                extend_cycle(path + [nxt])

    for start in sorted(inputs.donor_of):
        extend_cycle([start])

    def extend_chain(donors: list[str], patients: list[str]) -> None:
        out.append(Exchange("chain", tuple(donors), tuple(patients)))
        if len(patients) >= max_chain:
            return
        donor = inputs.donor_of.get(patients[-1])
        for nxt in gives.get(donor, []) if donor else []:
            if nxt not in patients:
                extend_chain(donors + [donor], patients + [nxt])

    altruists = sorted({a.donor for a in inputs.arcs if a.altruistic}) if max_chain >= 1 else []
    for n in altruists:
        for p in gives.get(n, []):
            extend_chain([n], [p])
    return out


def best_value(inputs: MatchInputs, max_cycle: int, max_chain: int) -> float:
    exchanges = all_exchanges(inputs, max_cycle, max_chain)
    values = [sum(inputs.weight[p] for p in e.patients) for e in exchanges]
    best = 0.0

    def search(i: int, used: frozenset[str], total: float) -> None:
        nonlocal best
        best = max(best, total)
        for j in range(i, len(exchanges)):
            e = exchanges[j]
            people = set(e.patients) | set(e.donors)
            if used.isdisjoint(people):
                search(j + 1, used | people, total + values[j])

    search(0, frozenset(), 0.0)
    return best
