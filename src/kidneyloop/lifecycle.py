"""What happens to a planned exchange after `kx match`: offers, crossmatch and surgery.

Each Exchange carries its current status (none means proposed) and an append-only
list of StatusEvent nodes, so every change can be audited in Cypher.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import relate

from .runs import HELD_STATUSES

Status = Literal[
    "proposed", "accepted", "crossmatch_passed", "scheduled", "transplanted", "declined", "crossmatch_failed", "cancelled"
]

TRANSITIONS: dict[Status, tuple[Status, ...]] = {
    "proposed": ("accepted", "declined", "cancelled"),
    "accepted": ("crossmatch_passed", "crossmatch_failed", "cancelled"),
    "crossmatch_passed": ("scheduled", "cancelled"),
    "scheduled": ("transplanted", "cancelled"),
    "transplanted": (),
    "declined": (),
    "crossmatch_failed": (),
    "cancelled": (),
}
STATUSES: tuple[Status, ...] = tuple(TRANSITIONS)
FAILED: tuple[Status, ...] = ("declined", "crossmatch_failed", "cancelled")

EXCHANGE = """
MATCH (r:MatchRun)-[:SELECTED]->(e:Exchange {id: $id})
OPTIONAL MATCH (e)-[:HAD_STATUS]->(s:StatusEvent)
WITH r, e, count(s) AS events
MATCH (m:MatchRun)
RETURN coalesce(e.status, 'proposed') AS status, events, r.id AS run, r.seq AS seq, max(m.seq) AS last
"""
# The patients' own donors count too: they leave or return with their patients,
# as runs.HELD treats them.
MEMBERS = """
MATCH (e:Exchange {id: $id})-[i:INCLUDES]->(x)
OPTIONAL MATCH (own:Donor)-[:PAIRED_WITH]->(x)
RETURN i.role AS role, x.id AS id, own.id AS own ORDER BY i.order, i.role
"""
RECORD = """
MATCH (e:Exchange {id: $id})
SET e.status = $status
CREATE (e)-[:HAD_STATUS]->(:StatusEvent {seq: $seq, status: $status, previous: $previous, at: $at, note: $note})
"""
LIST = """
MATCH (r:MatchRun)-[:SELECTED]->(e:Exchange)
WHERE r.id = $run OR e.status IS NOT NULL
RETURN r.id AS run, e.id AS id, e.kind AS kind, e.size AS size, coalesce(e.status, 'proposed') AS status
ORDER BY r.seq, e.id
"""


class TransitionError(ValueError):
    pass


@dataclass(frozen=True)
class Change:
    exchange: str
    previous: Status
    status: Status
    recorded: bool
    patients: tuple[str, ...]
    donors: tuple[str, ...]

    def summary(self) -> str:
        if not self.recorded:
            return f"{self.exchange} is already {self.status}; nothing recorded."
        people = ", ".join(self.patients + self.donors)
        line = f"{self.exchange}: {self.previous} -> {self.status}."
        if self.status in FAILED:
            return f"{line} Back in the pool for the next round: {people}."
        if self.status in HELD_STATUSES:
            verb = "leave the pool" if self.status == "transplanted" else "stay out of the next round"
            return f"{line} {people} {verb}."
        return line


def set_status(conn: relate.Connection, exchange: str, status: str, note: str = "", at: datetime | None = None) -> Change:
    """Move an exchange to a new status. Repeating the current status records nothing."""
    if status not in TRANSITIONS:
        raise TransitionError(f"unknown status {status!r}; use one of {', '.join(STATUSES)}")
    rows = list(conn.execute(EXCHANGE, {"id": exchange}))
    if not rows:
        raise KeyError(f"no exchange {exchange}")
    previous: Status = rows[0]["status"]
    members = list(conn.execute(MEMBERS, {"id": exchange}))
    patients = tuple(r["id"] for r in members if r["role"] == "patient")
    donors = tuple(dict.fromkeys([r["id"] for r in members if r["role"] == "donor"] + [r["own"] for r in members if r["own"]]))
    if status == previous:
        return Change(exchange, previous, previous, False, patients, donors)
    if status not in TRANSITIONS[previous]:
        allowed = ", ".join(TRANSITIONS[previous]) or "nothing; it is final"
        raise TransitionError(f"{exchange} is {previous}; it can move to {allowed}, not {status}")
    # A later run replanned the same pool, so this offer's people may be planned again
    # there; only the latest run's offers can be accepted.
    if previous == "proposed" and status == "accepted" and rows[0]["seq"] != rows[0]["last"]:
        raise TransitionError(f"{exchange} is a proposal of {rows[0]['run']}, which a later run replaced; accept an exchange of the latest run")
    stamp = (at or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    conn.execute(
        RECORD,
        {"id": exchange, "status": status, "seq": rows[0]["events"] + 1, "previous": previous, "at": stamp, "note": note},
    )
    return Change(exchange, previous, status, True, patients, donors)


def statuses(conn: relate.Connection, run: str) -> list[dict]:
    """The latest run's exchanges plus any exchange of an earlier run that has a status."""
    return [dict(r) for r in conn.execute(LIST, {"run": run})]
