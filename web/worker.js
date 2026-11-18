// The engine lives in this worker so a long statement never freezes the page.
const engine = (file) => new URL(`./relate/${file}`, self.location.href).href;

let db;
const ready = (async () => {
  const relate = await import(engine('relate.js'));
  await relate.default();
  return relate;
})();

function run(statement, params) {
  const envelope = JSON.parse(db.execute(statement, params ? JSON.stringify(params) : null));
  if (envelope.error) throw new Error(envelope.error);
  return envelope;
}

// graph.json is the checkpoint the CLI wrote. The browser build cannot open a
// directory store, so the snapshot is replayed into an in-memory store, keyed by
// the snapshot's node ids.
function importSnapshot(snapshot) {
  const byLabel = new Map();
  const labelOf = new Map();
  for (const node of snapshot.nodes) {
    const label = node.labels[0];
    labelOf.set(node.id, label);
    if (!byLabel.has(label)) byLabel.set(label, []);
    byLabel.get(label).push({ k: node.id, props: node.properties });
  }
  for (const [label, rows] of byLabel) {
    run(`CREATE INDEX FOR (n:${label}) ON (n._k)`);
    run(`UNWIND $rows AS r CREATE (n:${label}) SET n = r.props, n._k = r.k`, { rows });
  }
  const byShape = new Map();
  for (const rel of snapshot.relationships) {
    const key = `${labelOf.get(rel.start_node)}|${rel.type}|${labelOf.get(rel.end_node)}`;
    if (!byShape.has(key)) byShape.set(key, []);
    byShape.get(key).push({ s: rel.start_node, e: rel.end_node, props: rel.properties });
  }
  for (const [key, rows] of byShape) {
    const [from, type, to] = key.split('|');
    run(
      `UNWIND $rows AS r MATCH (a:${from} {_k: r.s}), (b:${to} {_k: r.e}) CREATE (a)-[x:${type}]->(b) SET x = r.props`,
      { rows },
    );
  }
  return { nodes: snapshot.nodes.length, relationships: snapshot.relationships.length };
}

self.onmessage = async ({ data }) => {
  const { id, op, statement, params, snapshot } = data;
  try {
    const relate = await ready;
    if (op === 'load') {
      db = new relate.RelateDb();
      const started = performance.now();
      const counts = importSnapshot(snapshot);
      self.postMessage({ id, result: { ...counts, ms: Math.round(performance.now() - started) } });
    } else {
      self.postMessage({ id, result: run(statement, params) });
    }
  } catch (err) {
    self.postMessage({ id, error: String(err?.message ?? err) });
  }
};
