from dataclasses import dataclass

import relate

from .exchanges import Exchange
from .runs import latest_run_id, run_exchanges

PROFILE = """
MATCH (p:Patient {id: $id})-[:HAS_BLOOD]->(pb:BloodType)
OPTIONAL MATCH (own:Donor)-[:PAIRED_WITH]->(p)
OPTIONAL MATCH (own)-[:COMPATIBLE]->(target:Patient)
RETURN pb.name AS blood, p.cpra AS cpra, own.id AS own_donor, count(target) AS own_donor_can_give
"""
ABO_DONORS = """
MATCH (p:Patient {id: $id})-[:HAS_BLOOD]->(pb:BloodType)
MATCH (d:Donor)-[:HAS_BLOOD]->(:BloodType)-[:ABO_CAN_GIVE_TO]->(pb)
WHERE NOT (d)-[:PAIRED_WITH]->(p)
RETURN count(d) AS n
"""
BLOCKING_ANTIGENS = """
MATCH (p:Patient {id: $id})-[:HAS_BLOOD]->(pb:BloodType)
MATCH (p)-[:HAS_ANTIBODY]->(a:Antigen)<-[:HAS_ANTIGEN]-(d:Donor)-[:HAS_BLOOD]->(:BloodType)-[:ABO_CAN_GIVE_TO]->(pb)
WHERE NOT (d)-[:PAIRED_WITH]->(p)
RETURN a.name AS antigen, count(DISTINCT d) AS donors
ORDER BY donors DESC, antigen
"""
ALTRUISTS_FOR = "MATCH (n:Donor {altruistic: true})-[:COMPATIBLE]->(:Patient {id: $id}) RETURN n.id AS id ORDER BY id"
INCOMING ="MATCH (d:Donor)-[:COMPATIBLE]->(:Patient {id: $id}) RETURN count(d) AS n, sum(CASE WHEN d.altruistic THEN 1 ELSE 0 END) AS altruistic"
CYCLES_THROUGH = """
MATCH (p:Patient {id: $id})<-[:PAIRED_WITH]-(:Donor)-[:COMPATIBLE]->(p2:Patient)<-[:PAIRED_WITH]-(:Donor)-[:COMPATIBLE]->(p)
RETURN [p.id, p2.id] AS patients
UNION ALL
MATCH (p:Patient {id: $id})<-[:PAIRED_WITH]-(:Donor)-[:COMPATIBLE]->(p2:Patient)<-[:PAIRED_WITH]-(:Donor)-[:COMPATIBLE]->(p3:Patient)<-[:PAIRED_WITH]-(:Donor)-[:COMPATIBLE]->(p)
WHERE p2 <> p3 AND p2 <> p AND p3 <> p
RETURN [p.id, p2.id, p3.id] AS patients
"""


@dataclass(frozen=True)
class Why:
    patient: str
    blood: str
    cpra: float
    run: str | None
    matched_in: tuple[str, Exchange] | None
    abo_donors: int
    blocking_antigens: tuple[tuple[str, int], ...]
    incoming: int
    incoming_altruistic: int
    own_donor: str | None
    own_donor_can_give: int
    candidate_cycles: tuple[tuple[str, ...], ...]
    taken_by: dict[str, str]
    altruists_used: dict[str, str | None]


def explain(conn: relate.Connection, patient_id: str) -> Why:
    params = {"id": patient_id}
    rows = list(conn.execute(PROFILE, params))
    if not rows:
        raise KeyError(f"no patient {patient_id}")
    profile = rows[0]
    run = latest_run_id(conn)
    exchanges = run_exchanges(conn, run) if run else []
    holder = {p: ex_id for ex_id, e in exchanges for p in e.patients}
    giver = {d: ex_id for ex_id, e in exchanges for d in e.donors}
    matched = next(((ex_id, e) for ex_id, e in exchanges if patient_id in e.patients), None)
    incoming = conn.execute(INCOMING, params).single()
    cycles = tuple(tuple(r["patients"]) for r in conn.execute(CYCLES_THROUGH, params))
    return Why(
        patient=patient_id,
        blood=profile["blood"],
        cpra=profile["cpra"],
        run=run,
        matched_in=matched,
        abo_donors=conn.execute(ABO_DONORS, params).single()["n"],
        blocking_antigens=tuple((r["antigen"], r["donors"]) for r in conn.execute(BLOCKING_ANTIGENS, params)),
        incoming=incoming["n"],
        incoming_altruistic=incoming["altruistic"] or 0,
        own_donor=profile["own_donor"],
        own_donor_can_give=profile["own_donor_can_give"],
        candidate_cycles=cycles,
        taken_by={p: holder[p] for c in cycles for p in c[1:] if p in holder},
        altruists_used={r["id"]: giver.get(r["id"]) for r in conn.execute(ALTRUISTS_FOR, params)},
    )


def render(why: Why) -> list[str]:
    lines = [f"{why.patient}: blood type {why.blood}, cPRA {why.cpra:.0%} of pool donors ruled out by antibodies"]
    if why.matched_in:
        ex_id, e = why.matched_in
        steps = ", ".join(f"{d} -> {p}" for d, p in zip(e.donors, e.patients))
        lines.append(f"Matched in {why.run} by {ex_id} ({e.kind} of {e.size}): {steps}")
        return lines
    lines.append(f"Not matched in {why.run}." if why.run else "No matching run recorded yet.")
    lines.append(f"{why.abo_donors} donors have a blood type that can give to {why.blood}.")
    if why.blocking_antigens:
        top = ", ".join(f"{a} ({n} donors)" for a, n in why.blocking_antigens[:5])
        lines.append(f"Antibody hits among them: {top}.")
    lines.append(f"{why.incoming} donors can give to {why.patient} ({why.incoming_altruistic} altruistic).")
    if why.incoming == 0:
        reason = "no donor has a compatible blood type" if why.abo_donors == 0 else "every blood-compatible donor carries an antigen the patient has antibodies to"
        lines.append(f"Reason: {reason}.")
        return lines
    if why.own_donor and why.own_donor_can_give == 0:
        lines.append(
            f"Reason: own donor {why.own_donor} can give to nobody in the pool, so {why.patient} can only be the last patient of a chain."
        )
        return lines
    if why.altruists_used:
        uses = "; ".join(f"{n} started {x}" if x else f"{n} unused" for n, x in why.altruists_used.items())
        lines.append(f"Altruistic donors who could start a chain here: {uses}.")
    lines.append(f"{len(why.candidate_cycles)} cycles of up to 3 pairs pass through {why.patient}.")
    free = [c for c in why.candidate_cycles if not any(p in why.taken_by for p in c[1:])]
    if why.candidate_cycles and not free:
        taken = "; ".join(f"{p} in {x}" for p, x in list(why.taken_by.items())[:5])
        lines.append(f"Reason: every one of those cycles needs a patient the plan already serves, e.g. {taken}.")
    elif free:
        lines.append(
            f"Reason: none found. {len(free)} cycles need no served patient, e.g. {' -> '.join(free[0])}; "
            "a run with a smaller cycle limit would skip them."
        )
    else:
        lines.append("Reason: no cycle closes back to this pair, and no chain reached it in the chosen plan.")
    return lines
