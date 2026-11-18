import * as Q from './queries.js';

const SNAPSHOT_URL = '../data/kidneyloop/graph.json';
const worker = new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });
const pending = new Map();
let nextId = 0;

worker.onmessage = ({ data }) => {
  const { resolve, reject } = pending.get(data.id);
  pending.delete(data.id);
  if (data.error) reject(new Error(data.error));
  else resolve(data.result);
};

function call(message) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, ...message });
  });
}

const cypher = (statement, params) => call({ op: 'run', statement, params });
const rows = async (statement, params) => (await cypher(statement, params)).rows;

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  node.append(...children.flat().filter((c) => c !== null && c !== undefined));
  return node;
};

const state = { snapshot: null, run: null, exchanges: [], holder: new Map(), giver: new Map(), withdrawn: null };

async function load() {
  $('#status').textContent = 'Loading graph.json into Relate (WebAssembly)…';
  if (!state.snapshot) state.snapshot = await (await fetch(SNAPSHOT_URL)).json();
  const loaded = await call({ op: 'load', snapshot: state.snapshot });
  $('#status').textContent =
    `Relate in this tab holds ${loaded.nodes} nodes and ${loaded.relationships} relationships from data/kidneyloop/graph.json (loaded in ${loaded.ms} ms).`;
  state.withdrawn = null;
  await showRun();
}

async function showRun() {
  const [summary] = await rows(Q.SUMMARY);
  if (!summary) {
    $('#summary').replaceChildren(el('p', {}, 'No MatchRun in this store yet. Run `kx match` and reload.'));
    return;
  }
  state.run = summary.run;
  const seq = (id) => Number(id.split('-X')[1]);
  state.exchanges = (await rows(Q.RUN_EXCHANGES, { run: summary.run }))
    .sort((a, b) => b.patients.length - a.patients.length || seq(a.id) - seq(b.id));
  state.holder = new Map(state.exchanges.flatMap((e) => e.patients.map((p) => [p, e.id])));
  state.giver = new Map(state.exchanges.flatMap((e) => e.donors.map((d) => [d, e.id])));
  const cycles = state.exchanges.filter((e) => e.kind === 'cycle').length;
  const chains = state.exchanges.length - cycles;
  $('#summary').replaceChildren(
    stat(summary.transplants, 'transplants'),
    stat(summary.patients - state.holder.size, 'patients unmatched'),
    stat(cycles, 'cycles chosen'),
    stat(chains, 'chains chosen'),
    stat(summary.cycles.toLocaleString(), 'candidate cycles'),
    stat(summary.arcs.toLocaleString(), 'compatible arcs'),
  );
  $('#run-meta').textContent = `${summary.run} · ${summary.solver} · ${summary.objective}`;
  drawExchanges();
}

const stat = (value, label) => el('div', { class: 'stat' }, el('strong', {}, String(value)), el('span', {}, label));

function drawExchanges() {
  const broken = state.withdrawn ? new Set(state.withdrawn.broken) : new Set();
  $('#exchanges').replaceChildren(
    ...state.exchanges.map((e) =>
      el(
        'button',
        { class: `exchange ${broken.has(e.id) ? 'broken' : ''}`, onclick: () => explain(e.patients[0]), title: `Explain ${e.patients[0]}` },
        el('span', { class: 'ex-id' }, `${e.id} · ${e.kind} of ${e.patients.length}`),
        exchangeSvg(e),
      ),
    ),
  );
}

function exchangeSvg(e) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  const nodes = e.kind === 'chain' ? [e.donors[0], ...e.patients] : e.patients;
  const w = 220, h = 92, r = 15;
  const pos = nodes.map((_, i) => {
    if (e.kind === 'chain') return [r + 4 + (i * (w - 2 * r - 8)) / Math.max(nodes.length - 1, 1), h / 2];
    const a = (2 * Math.PI * i) / nodes.length - Math.PI / 2;
    return [w / 2 + 34 * Math.cos(a) * (nodes.length === 2 ? 1.6 : 1.3), h / 2 + 30 * Math.sin(a)];
  });
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', `${e.kind}: ${e.donors.map((d, i) => `${d} gives to ${e.patients[i]}`).join(', ')}`);
  const add = (tag, attrs, text) => {
    const n = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text) n.textContent = text;
    svg.append(n);
  };
  const edges = e.kind === 'chain'
    ? e.patients.map((_, i) => [i, i + 1])
    : e.patients.map((_, i) => [(i - 1 + nodes.length) % nodes.length, i]);
  for (const [a, b] of edges) {
    const [x1, y1] = pos[a], [x2, y2] = pos[b];
    const len = Math.hypot(x2 - x1, y2 - y1) || 1;
    const ux = (x2 - x1) / len, uy = (y2 - y1) / len;
    add('line', { x1: x1 + ux * r, y1: y1 + uy * r, x2: x2 - ux * (r + 4), y2: y2 - uy * (r + 4), class: 'edge', 'marker-end': 'url(#arrow)' });
  }
  nodes.forEach((id, i) => {
    add('circle', { cx: pos[i][0], cy: pos[i][1], r, class: id.startsWith('N') ? 'node altruist' : 'node' });
    add('text', { x: pos[i][0], y: pos[i][1] + 3.5, class: 'label' }, id.replace(/^P0*/, 'P').replace(/^N0*/, 'N'));
  });
  return svg;
}

async function explain(id) {
  id = id.trim().toUpperCase();
  $('#patient').value = id;
  const out = $('#why');
  const [profile] = await rows(Q.PROFILE, { id });
  if (!profile) {
    out.replaceChildren(el('p', { class: 'warn' }, `No patient ${id}.`));
    return;
  }
  const [abo] = await rows(Q.ABO_DONORS, { id });
  const antigens = await rows(Q.BLOCKING_ANTIGENS, { id });
  const [incoming] = await rows(Q.INCOMING, { id });
  const cycles = (await rows(Q.CYCLES_THROUGH, { id })).map((r) => r.patients);
  const lines = [
    `${id}: blood type ${profile.blood}, cPRA ${Math.round(profile.cpra * 100)}% of pool donors ruled out by antibodies.`,
  ];
  const ex = state.exchanges.find((e) => e.patients.includes(id));
  if (ex) {
    lines.push(`Matched in ${state.run} by ${ex.id} (${ex.kind} of ${ex.patients.length}): ${ex.donors.map((d, i) => `${d} → ${ex.patients[i]}`).join(', ')}.`);
  } else {
    lines.push(`Not matched in ${state.run}.`);
    lines.push(`${abo.n} donors have a blood type that can give to ${profile.blood}.`);
    if (antigens.length) lines.push(`Antibody hits among them: ${antigens.slice(0, 5).map((a) => `${a.antigen} (${a.donors})`).join(', ')}.`);
    lines.push(`${incoming.n} donors can give to ${id} (${incoming.altruistic ?? 0} altruistic).`);
    if (incoming.n === 0) {
      lines.push(abo.n === 0 ? 'Reason: no donor has a compatible blood type.' : 'Reason: every blood-compatible donor carries an antigen the patient has antibodies to.');
    } else if (profile.own_donor && profile.own_donor_can_give === 0) {
      lines.push(`Reason: own donor ${profile.own_donor} can give to nobody, so ${id} can only end a chain.`);
    } else {
      lines.push(`${cycles.length} cycles of up to 3 pairs pass through ${id}.`);
      const taken = [...new Set(cycles.flatMap((c) => c.slice(1)).filter((p) => state.holder.has(p)))];
      if (cycles.length) lines.push(`Reason: those cycles need patients the plan already serves, e.g. ${taken.slice(0, 5).map((p) => `${p} in ${state.holder.get(p)}`).join('; ')}.`);
    }
  }
  out.replaceChildren(...lines.map((l) => el('p', {}, l)));
}

async function withdraw(id) {
  id = id.trim().toUpperCase();
  const out = $('#whatif-out');
  const [{ removed }] = await rows(Q.WITHDRAW, { id });
  if (!removed) {
    out.replaceChildren(el('p', { class: 'warn' }, `No donor ${id} in this tab's graph.`));
    return;
  }
  const broken = state.exchanges.filter((e) => e.donors.includes(id));
  state.withdrawn = { id, broken: broken.map((e) => e.id) };
  const stranded = broken.flatMap((e) => strandedBy(e, id));
  out.replaceChildren(
    el('p', {}, broken.length
      ? `${id} withdrew: ${broken.map((e) => `${e.id} (${e.kind})`).join(', ')} can no longer run as planned. Patients who were waiting on ${id}'s kidney or on a donor after it: ${stranded.join(', ')}.`
      : `${id} withdrew: no chosen exchange used this donor.`),
    el('p', { class: 'muted' }, 'The browser has no solver. Run `kx whatif --withdraw ' + id + '` for the re-plan, which rolls back inside a transaction. Reload restores the graph here.'),
  );
  drawExchanges();
}

// A cycle only works if every donor gives, so every patient in it is affected.
// A chain still serves the patients before the withdrawn donor's position.
function strandedBy(exchange, donor) {
  if (exchange.kind === 'cycle') return exchange.patients;
  return exchange.patients.slice(exchange.donors.indexOf(donor));
}

async function runConsole() {
  const out = $('#console-out');
  try {
    const result = await cypher($('#statement').value);
    const cols = result.columns;
    out.replaceChildren(
      el('p', { class: 'muted' }, `${result.rows.length} rows${result.truncated ? ' (truncated)' : ''}`),
      el('table', {},
        el('thead', {}, el('tr', {}, cols.map((c) => el('th', {}, c)))),
        el('tbody', {}, result.rows.slice(0, 200).map((r) => el('tr', {}, cols.map((c) => el('td', {}, JSON.stringify(r[c])))))),
      ),
    );
  } catch (err) {
    out.replaceChildren(el('p', { class: 'warn' }, err.message));
  }
}

$('#why-form').addEventListener('submit', (ev) => { ev.preventDefault(); explain($('#patient').value); });
$('#whatif-form').addEventListener('submit', (ev) => { ev.preventDefault(); withdraw($('#donor').value); });
$('#reset').addEventListener('click', () => { $('#whatif-out').replaceChildren(); load(); });
$('#console-form').addEventListener('submit', (ev) => { ev.preventDefault(); runConsole(); });

load().catch((err) => { $('#status').textContent = `Could not load the graph: ${err.message}`; $('#status').classList.add('warn'); });
