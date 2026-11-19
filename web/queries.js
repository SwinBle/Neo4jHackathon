// The same Cypher the Python CLI runs (src/kidneyloop), so the page and `kx` agree.

export const SUMMARY = `
MATCH (r:MatchRun) WITH r ORDER BY r.seq DESC LIMIT 1
MATCH (p:Patient) WITH r, count(p) AS patients
RETURN r.id AS run, r.transplants AS transplants, r.candidate_cycles AS cycles,
       r.candidate_arcs AS arcs, r.solver AS solver, r.objective AS objective, patients`;

export const RUN_EXCHANGES = `
MATCH (:MatchRun {id: $run})-[:SELECTED]->(e:Exchange)-[i:INCLUDES]->(x)
WITH e, i.order AS step, i.role AS role, x.id AS who
ORDER BY e.id, step
WITH e, collect(CASE WHEN role = 'donor' THEN who END) AS donors,
        collect(CASE WHEN role = 'patient' THEN who END) AS patients
RETURN e.id AS id, e.kind AS kind, donors, patients
ORDER BY id`;

export const PROFILE = `
MATCH (p:Patient {id: $id})-[:HAS_BLOOD]->(pb:BloodType)
OPTIONAL MATCH (own:Donor)-[:PAIRED_WITH]->(p)
OPTIONAL MATCH (own)-[:COMPATIBLE]->(target:Patient)
RETURN pb.name AS blood, p.pool_pra AS pool_pra, own.id AS own_donor, count(target) AS own_donor_can_give`;

export const ABO_DONORS = `
MATCH (p:Patient {id: $id})-[:HAS_BLOOD]->(pb:BloodType)
MATCH (d:Donor)-[:HAS_BLOOD]->(:BloodType)-[:ABO_CAN_GIVE_TO]->(pb)
WHERE NOT (d)-[:PAIRED_WITH]->(p)
RETURN count(d) AS n`;

export const BLOCKING_ANTIGENS = `
MATCH (p:Patient {id: $id})-[:HAS_BLOOD]->(pb:BloodType)
MATCH (p)-[:HAS_ANTIBODY]->(a:Antigen)<-[:HAS_ANTIGEN]-(d:Donor)-[:HAS_BLOOD]->(:BloodType)-[:ABO_CAN_GIVE_TO]->(pb)
WHERE NOT (d)-[:PAIRED_WITH]->(p)
RETURN a.name AS antigen, count(DISTINCT d) AS donors
ORDER BY donors DESC, antigen`;

export const INCOMING = `
MATCH (d:Donor)-[:COMPATIBLE]->(:Patient {id: $id})
RETURN count(d) AS n, sum(CASE WHEN d.altruistic THEN 1 ELSE 0 END) AS altruistic`;

export const CYCLES_THROUGH = `
MATCH (p:Patient {id: $id})<-[:PAIRED_WITH]-(:Donor)-[:COMPATIBLE]->(p2:Patient)<-[:PAIRED_WITH]-(:Donor)-[:COMPATIBLE]->(p)
RETURN [p.id, p2.id] AS patients
UNION ALL
MATCH (p:Patient {id: $id})<-[:PAIRED_WITH]-(:Donor)-[:COMPATIBLE]->(p2:Patient)<-[:PAIRED_WITH]-(:Donor)-[:COMPATIBLE]->(p3:Patient)<-[:PAIRED_WITH]-(:Donor)-[:COMPATIBLE]->(p)
WHERE p2 <> p3 AND p2 <> p AND p3 <> p
RETURN [p.id, p2.id, p3.id] AS patients`;

export const PATIENT_INDEX = `MATCH (p:Patient) RETURN p.id AS id, p.pool_pra AS pool_pra ORDER BY p.pool_pra DESC, id`;

export const ALTRUISTS_FOR = `MATCH (n:Donor {altruistic: true})-[:COMPATIBLE]->(:Patient {id: $id}) RETURN n.id AS id ORDER BY id`;

export const DATASET = `MATCH (ds:Dataset) RETURN ds.name AS name`;

export const DONOR_EXISTS = `MATCH (d:Donor {id: $id}) RETURN count(d) AS n`;

export const WITHDRAW = `
MATCH (d:Donor {id: $id})
OPTIONAL MATCH (d)-[:PAIRED_WITH]->(p:Patient)
WITH d, p, p.id AS leaving
DETACH DELETE d, p
RETURN leaving`;
