import os
import shutil
import subprocess
from pathlib import Path

import relate

from .datasets import ImportedPool
from .model import ABO_CAN_GIVE_TO, ANTIGENS, BLOOD_TYPES, Pool

SCHEMA = [
    "CREATE CONSTRAINT patient_id IF NOT EXISTS FOR (p:Patient) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT donor_id IF NOT EXISTS FOR (d:Donor) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT antigen_name IF NOT EXISTS FOR (a:Antigen) REQUIRE a.name IS UNIQUE",
    "CREATE CONSTRAINT blood_name IF NOT EXISTS FOR (b:BloodType) REQUIRE b.name IS UNIQUE",
]

LOAD_REFERENCE = """
UNWIND $blood AS name CREATE (:BloodType {name: name})
"""
LOAD_ABO = """
UNWIND $rules AS rule
MATCH (g:BloodType {name: rule.from}), (r:BloodType {name: rule.to})
CREATE (g)-[:ABO_CAN_GIVE_TO]->(r)
"""
LOAD_ANTIGENS = "UNWIND $antigens AS name CREATE (:Antigen {name: name})"
LOAD_PATIENTS = """
UNWIND $patients AS row
MATCH (b:BloodType {name: row.blood})
CREATE (p:Patient {id: row.id, wait_days: row.wait_days})-[:HAS_BLOOD]->(b)
WITH p, row
UNWIND row.antibodies AS ab
MATCH (a:Antigen {name: ab})
CREATE (p)-[:HAS_ANTIBODY]->(a)
"""
LOAD_DONORS = """
UNWIND $donors AS row
MATCH (b:BloodType {name: row.blood})
CREATE (d:Donor {id: row.id, altruistic: row.altruistic})-[:HAS_BLOOD]->(b)
WITH d, row
UNWIND row.antigens AS ag
MATCH (a:Antigen {name: ag})
CREATE (d)-[:HAS_ANTIGEN]->(a)
"""
LOAD_PAIRS = """
UNWIND $pairs AS row
MATCH (d:Donor {id: row.donor}), (p:Patient {id: row.patient})
CREATE (d)-[:PAIRED_WITH]->(p)
"""

DERIVE_COMPATIBLE = """
MATCH (d:Donor)-[:HAS_BLOOD]->(:BloodType)-[:ABO_CAN_GIVE_TO]->(:BloodType)<-[:HAS_BLOOD]-(p:Patient)
WHERE NOT (d)-[:PAIRED_WITH]->(p)
  AND NOT EXISTS { (p)-[:HAS_ANTIBODY]->(:Antigen)<-[:HAS_ANTIGEN]-(d) }
CREATE (d)-[:COMPATIBLE]->(p)
"""

# Pool PRA: the share of donors in this pool whose antigens the patient has antibodies to.
# It is not the clinical cPRA, which is computed against a reference donor population.
DERIVE_POOL_PRA = """
MATCH (any:Donor) WITH count(any) AS total
MATCH (p:Patient)
OPTIONAL MATCH (p)-[:HAS_ANTIBODY]->(:Antigen)<-[:HAS_ANTIGEN]-(d:Donor)
WITH p, total, count(DISTINCT d) AS blocked
SET p.pool_pra = toFloat(blocked) / total
"""


LOAD_ARCS = """
UNWIND $arcs AS arc
MATCH (d:Donor {id: arc.donor}), (p:Patient {id: arc.patient})
CREATE (d)-[:COMPATIBLE]->(p)
"""
SET_SOURCE_PRA = """
UNWIND $rows AS row
MATCH (p:Patient {id: row.id})
SET p.source_pra = row.pra
"""

# An imported pool has no HLA typing, only the dataset's arcs. Its pool PRA counts the
# blood-compatible donors the dataset still rules out, which is the share the
# antibodies would explain in a pool KidneyLoop generated.
DERIVE_POOL_PRA_FROM_ARCS = """
MATCH (any:Donor) WITH count(any) AS total
MATCH (p:Patient)-[:HAS_BLOOD]->(pb:BloodType)
OPTIONAL MATCH (d:Donor)-[:HAS_BLOOD]->(:BloodType)-[:ABO_CAN_GIVE_TO]->(pb)
WHERE NOT (d)-[:PAIRED_WITH]->(p) AND NOT (d)-[:COMPATIBLE]->(p)
WITH p, total, count(DISTINCT d) AS blocked
SET p.pool_pra = toFloat(blocked) / total
"""


def relate_cli() -> str:
    found = shutil.which("relate")
    if found:
        return found
    default = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "relate" / "relate.exe"
    if default.exists():
        return str(default)
    raise RuntimeError(
        "The relate CLI is needed to create a JSON store. Install it from "
        "https://relatedb.ai/docs/get-started/install"
    )


def create_json_store(path: Path) -> None:
    """Create an empty directory store with the json codec.

    The Python client has no option for the codec, and the codec is fixed when the
    store is created, so the CLI makes the empty store and Python writes into it.
    """
    cli = relate_cli()
    if path.exists():
        if not (path / "manifest.json").exists() or not (path / ".cypherlite.lock").exists():
            raise FileExistsError(f"{path} exists and is not a Relate store; refusing to replace it")
        shutil.rmtree(path)
    made = subprocess.run(
        [cli, "--database", str(path), "--snapshot-format", "json", "--mode", "envelope", "RETURN 1"],
        capture_output=True,
        text=True,
    )
    if made.returncode != 0:
        raise RuntimeError(f"relate could not create {path}: {made.stderr.strip() or made.stdout.strip()}")


def load_pool(conn: relate.Connection, pool: Pool) -> None:
    for statement in SCHEMA:
        conn.execute(statement)
    conn.execute(LOAD_REFERENCE, {"blood": list(BLOOD_TYPES)})
    conn.execute(
        LOAD_ABO,
        {"rules": [{"from": g, "to": r} for g, targets in ABO_CAN_GIVE_TO.items() for r in targets]},
    )
    conn.execute(LOAD_ANTIGENS, {"antigens": list(ANTIGENS)})
    conn.execute(
        LOAD_PATIENTS,
        {
            "patients": [
                {"id": p.id, "blood": p.blood_type, "wait_days": p.wait_days, "antibodies": sorted(p.antibodies)}
                for p in pool.patients
            ]
        },
    )
    conn.execute(
        LOAD_DONORS,
        {
            "donors": [
                {"id": d.id, "blood": d.blood_type, "altruistic": d.paired_with is None, "antigens": sorted(d.antigens)}
                for d in pool.donors
            ]
        },
    )
    conn.execute(
        LOAD_PAIRS,
        {"pairs": [{"donor": d.id, "patient": d.paired_with} for d in pool.donors if d.paired_with]},
    )


def derive(conn: relate.Connection) -> None:
    conn.execute(DERIVE_COMPATIBLE)
    conn.execute(DERIVE_POOL_PRA)


def load_imported(conn: relate.Connection, imported: ImportedPool) -> None:
    """Write a benchmark pool with the dataset's own compatibility arcs."""
    load_pool(conn, imported.pool)
    conn.execute(LOAD_ARCS, {"arcs": [{"donor": d, "patient": p} for d, p in imported.arcs]})
    conn.execute(SET_SOURCE_PRA, {"rows": [{"id": k, "pra": v} for k, v in imported.source_pra.items()]})
    conn.execute(DERIVE_POOL_PRA_FROM_ARCS)
