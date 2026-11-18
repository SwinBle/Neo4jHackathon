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

const state = { snapshot: null, run: null, exchanges: [], holder: new Map(), giver: new Map(), withdrawn: null, filter: 'all', selected: null };

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
    $('#legend').replaceChildren(el('p', {}, 'This store has no matching run yet. Run `kx match`, then reload the page.'));
    return;
  }
  state.run = summary.run;
  const seq = (id) => Number(id.split('-X')[1]);
  state.exchanges = (await rows(Q.RUN_EXCHANGES, { run: summary.run }))
    .sort((a, b) => b.patients.length - a.patients.length || seq(a.id) - seq(b.id));
  state.holder = new Map(state.exchanges.flatMap((e) => e.patients.map((p) => [p, e.id])));
  state.giver = new Map(state.exchanges.flatMap((e) => e.donors.map((d) => [d, e.id])));
  const loops = state.exchanges.filter((e) => e.kind === 'cycle');
  const chains = state.exchanges.filter((e) => e.kind === 'chain');
  const inLoops = loops.reduce((n, e) => n + e.patients.length, 0);
  const inChains = chains.reduce((n, e) => n + e.patients.length, 0);
  const unmatched = summary.patients - state.holder.size;
  $('#headline').textContent = `${summary.transplants} of ${summary.patients} patients get a kidney in this round`;
  $('#round-total').textContent = `Run ${summary.run}, chosen from ${summary.cycles.toLocaleString()} possible loops and ${summary.arcs.toLocaleString()} donor-to-patient matches`;
  const [cyc, chn, rest] = $('#bar').children;
  rest.style.flexGrow = summary.patients;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    cyc.style.flexGrow = inLoops;
    chn.style.flexGrow = inChains;
    rest.style.flexGrow = unmatched;
  }));
  $('#bar').setAttribute('aria-label', `${inLoops} patients in loops, ${inChains} in chains, ${unmatched} unmatched, of ${summary.patients}`);
  const key = (cls, n, text) => el('span', {}, el('i', { class: cls, style: `background: var(${cls})` }), el('span', { class: 'n' }, String(n)), ` ${text}`);
  $('#legend').replaceChildren(
    key('--served', inLoops, `in ${loops.length} loops`),
    key('--gift', inChains, `in ${chains.length} chains from altruistic donors`),
    key('--line', unmatched, 'not matched this round'),
  );
  $('#run-meta').textContent = `Solver: ${summary.solver}. Objective: ${summary.objective}.`;
  drawHero(loops);
  drawExchanges();
  await fillPatientPickers();
}

// The hero shows one real loop from this run, drawn arrow by arrow: the fastest way
// to see what a paired exchange is.
function drawHero(loops) {
  const loop = loops.find((e) => e.patients.length === 3) ?? loops[0];
  if (!loop) return;
  const svg = exchangeSvg(loop, new Map(), HERO);
  svg.classList.add('draw');
  [...svg.querySelectorAll('.edge')].forEach((edge, i) => { edge.style.animationDelay = `${300 + i * 650}ms`; });
  const steps = loop.donors.map((d, i) => `${d} gives to ${loop.patients[i]}`).join(', ');
  $('#hero-figure').replaceChildren(svg, el('figcaption', {}, `Exchange ${loop.id} from this run: ${steps}.`));
}

function freshen(node) {
  node.classList.remove('fresh');
  void node.offsetWidth;
  node.classList.add('fresh');
}

async function fillPatientPickers() {
  const patients = await rows(Q.PATIENT_INDEX);
  $('#patient-ids').replaceChildren(...patients.map((p) => el('option', { value: p.id })));
  const hardest = patients.filter((p) => !state.holder.has(p.id)).slice(0, 8);
  $('#hard-picks').replaceChildren(
    ...hardest.map((p) => el('button', { type: 'button', class: 'chip', onclick: () => explain(p.id), 'aria-label': `Explain ${p.id}, antibodies rule out ${Math.round(p.pool_pra * 100)} percent of donors` }, p.id, el('span', { class: 'pct' }, `${Math.round(p.pool_pra * 100)}%`))),
  );
}

function revealInspector() {
  if (matchMedia('(max-width: 980px)').matches) $('#why-h').scrollIntoView({ block: 'start' });
}

function drawExchanges() {
  const broken = state.withdrawn ? new Set(state.withdrawn.broken) : new Set();
  const shown = state.exchanges.filter((e) => state.filter === 'all' || e.kind === state.filter);
  $('#exchange-count').textContent = `${shown.length} of ${state.exchanges.length}`;
  $('#exchanges').replaceChildren(
    ...shown.map((e) =>
      el(
        'button',
        {
          class: `exchange ${broken.has(e.id) ? 'broken' : ''} ${state.selected === e.id ? 'selected' : ''}`,
          onclick: () => showExchange(e),
          'aria-label': `Show ${e.id}, ${e.kind} of ${e.patients.length}`,
          'aria-pressed': String(state.selected === e.id),
        },
        el('span', { class: 'ex-id' }, el('b', {}, e.id), `${e.kind === 'cycle' ? 'loop' : 'chain'} of ${e.patients.length}`),
        exchangeSvg(e),
      ),
    ),
  );
}

function showExchange(e) {
  const steps = e.donors.map((d, i) => el('li', {}, `${d} gives to `, patientButton(e.patients[i])));
  $('#why').replaceChildren(
    el('p', {}, `${e.id} is a ${e.kind === 'cycle' ? 'loop' : 'chain'} of ${e.patients.length} in ${state.run}. Select a patient to see why they were matched.`),
    figure(exchangeSvg(e), e.kind === 'chain' ? 'An altruistic donor (dashed) starts the chain.' : 'Each pair\'s donor gives to the next patient.'),
    el('ol', { class: 'steps' }, steps),
  );
  $('#why svg').classList.add('draw');
  freshen($('#why'));
  state.selected = e.id;
  drawExchanges();
  revealInspector();
}

const patientButton = (id) => el('button', { class: 'link', type: 'button', onclick: () => explain(id) }, id);
const figure = (svg, caption) => el('figure', {}, svg, el('figcaption', { class: 'muted' }, caption));

// marks: Map of node id to a CSS class ('focus' for the patient being explained,
// 'served' for a patient another exchange already serves).
const CARD = { w: 220, h: 92, r: 15, ringX: 44, ringY: 30, donorLabels: false };
const HERO = { w: 300, h: 230, r: 26, ringX: 92, ringY: 80, donorLabels: true };

function exchangeSvg(e, marks = new Map(), layout = CARD) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  const nodes = e.kind === 'chain' ? [e.donors[0], ...e.patients] : [...e.patients];
  const { w, h, r } = layout;
  const pos = nodes.map((_, i) => {
    if (e.kind === 'chain') return [r + 4 + (i * (w - 2 * r - 8)) / Math.max(nodes.length - 1, 1), h / 2];
    const a = (2 * Math.PI * i) / nodes.length - Math.PI / 2;
    const stretch = nodes.length === 2 ? 1.25 : 1;
    return [w / 2 + layout.ringX * stretch * Math.cos(a), h / 2 + layout.ringY * Math.sin(a) + (nodes.length === 3 ? layout.ringY * 0.18 : 0)];
  });
  if (layout === HERO) svg.classList.add('hero-svg');
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', e.donors
    ? `${e.kind}: ${e.donors.map((d, i) => `${d} gives to ${e.patients[i]}`).join(', ')}`
    : `${e.kind} through ${e.patients.join(', ')}`);
  const add = (tag, attrs, text) => {
    const n = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text) n.textContent = text;
    svg.append(n);
  };
  const edges = e.kind === 'chain'
    ? e.patients.map((_, i) => [i, i + 1])
    : e.patients.map((_, i) => [(i - 1 + nodes.length) % nodes.length, i]);
  edges.forEach(([a, b], k) => {
    const [x1, y1] = pos[a], [x2, y2] = pos[b];
    const len = Math.hypot(x2 - x1, y2 - y1) || 1;
    const ux = (x2 - x1) / len, uy = (y2 - y1) / len;
    add('line', { x1: x1 + ux * r, y1: y1 + uy * r, x2: x2 - ux * (r + 4), y2: y2 - uy * (r + 4), class: 'edge', 'marker-end': 'url(#arrow)' });
    if (layout.donorLabels && e.donors) {
      const giver = e.kind === 'chain' ? e.donors[k] : e.donors[b];
      const mx = (x1 + x2) / 2 - uy * 14, my = (y1 + y2) / 2 + ux * 14;
      add('text', { x: mx, y: my + 4, class: 'donor-label' }, giver.replace(/^D0*/, 'D'));
    }
  });
  nodes.forEach((id, i) => {
    add('circle', { cx: pos[i][0], cy: pos[i][1], r, class: `node ${id.startsWith('N') ? 'altruist' : ''} ${marks.get(id) ?? ''}` });
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
  const { lines, figures } = await whyLines(id, profile);
  out.replaceChildren(...lines.map((l) => el('p', {}, l)), ...figures);
  out.querySelectorAll('svg').forEach((svg) => svg.classList.add('draw'));
  freshen(out);
  revealInspector();
}

// Mirrors render() in src/kidneyloop/explain.py line for line, so `kx why` and the page agree.
// figures add the graph picture the CLI cannot show; they never change the text.
async function whyLines(id, profile) {
  const lines = [`${id}: blood type ${profile.blood}; antibodies rule out ${Math.round(profile.pool_pra * 100)}% of this pool's donors (pool PRA)`];
  const figures = [];
  const done = () => ({ lines, figures });
  const ex = state.exchanges.find((e) => e.patients.includes(id));
  if (ex) {
    lines.push(`Matched in ${state.run} by ${ex.id} (${ex.kind} of ${ex.patients.length}): ${ex.donors.map((d, i) => `${d} -> ${ex.patients[i]}`).join(', ')}`);
    figures.push(figure(exchangeSvg(ex, new Map([[id, 'focus']])), `${id} (highlighted) in ${ex.id}.`));
    return done();
  }
  const [abo] = await rows(Q.ABO_DONORS, { id });
  const antigens = await rows(Q.BLOCKING_ANTIGENS, { id });
  const [incoming] = await rows(Q.INCOMING, { id });
  lines.push(state.run ? `Not matched in ${state.run}.` : 'No matching run recorded yet.');
  lines.push(`${abo.n} donors have a blood type that can give to ${profile.blood}.`);
  if (antigens.length) lines.push(`Antibody hits among them: ${antigens.slice(0, 5).map((a) => `${a.antigen} (${a.donors} donors)`).join(', ')}.`);
  lines.push(`${incoming.n} donors can give to ${id} (${incoming.altruistic ?? 0} altruistic).`);
  if (incoming.n === 0) {
    lines.push(`Reason: ${abo.n === 0 ? 'no donor has a compatible blood type' : 'every blood-compatible donor carries an antigen the patient has antibodies to'}.`);
    return done();
  }
  if (profile.own_donor && profile.own_donor_can_give === 0) {
    lines.push(`Reason: own donor ${profile.own_donor} can give to nobody in the pool, so ${id} can only be the last patient of a chain.`);
    return done();
  }
  const altruists = (await rows(Q.ALTRUISTS_FOR, { id })).map((r) => r.id);
  if (altruists.length) {
    lines.push(`Altruistic donors who could start a chain here: ${altruists.map((n) => (state.giver.has(n) ? `${n} started ${state.giver.get(n)}` : `${n} unused`)).join('; ')}.`);
  }
  const cycles = (await rows(Q.CYCLES_THROUGH, { id })).map((r) => r.patients);
  lines.push(`${cycles.length} cycles of up to 3 pairs pass through ${id}.`);
  const takenBy = new Map();
  for (const c of cycles) for (const p of c.slice(1)) if (state.holder.has(p) && !takenBy.has(p)) takenBy.set(p, state.holder.get(p));
  const free = cycles.filter((c) => !c.slice(1).some((p) => takenBy.has(p)));
  if (cycles.length && !free.length) {
    lines.push(`Reason: every one of those cycles needs a patient the plan already serves, e.g. ${[...takenBy].slice(0, 5).map(([p, x]) => `${p} in ${x}`).join('; ')}.`);
  } else if (free.length) {
    lines.push(`Reason: none found. ${free.length} cycles need no served patient, e.g. ${free[0].join(' -> ')}; a run with a smaller cycle limit would skip them.`);
  } else {
    lines.push('Reason: no cycle closes back to this pair, and no chain reached it in the chosen plan.');
  }
  for (const c of cycles.slice(0, 2)) {
    const marks = new Map([[id, 'focus'], ...c.slice(1).filter((p) => takenBy.has(p)).map((p) => [p, 'served'])]);
    const blockers = c.slice(1).filter((p) => takenBy.has(p)).map((p) => `${p} is served by ${takenBy.get(p)}`);
    figures.push(figure(exchangeSvg({ kind: 'cycle', patients: c }, marks), blockers.length ? `A cycle that could serve ${id}, blocked: ${blockers.join('; ')}.` : `A cycle that could serve ${id}.`));
  }
  return done();
}

async function withdraw(id) {
  id = id.trim().toUpperCase();
  const out = $('#whatif-out');
  const [{ n }] = await rows(Q.DONOR_EXISTS, { id });
  if (!n) {
    out.replaceChildren(el('p', { class: 'warn' }, `No donor ${id} in this tab's graph.`));
    return;
  }
  const [{ leaving }] = await rows(Q.WITHDRAW, { id });
  const broken = state.exchanges.filter((e) => e.donors.includes(id) || (leaving && e.patients.includes(leaving)));
  state.withdrawn = { id, broken: broken.map((e) => e.id) };
  const stranded = broken.flatMap((e) => strandedBy(e, id)).filter((p) => p !== leaving);
  out.replaceChildren(
    leaving ? el('p', {}, `${leaving} leaves the exchange with their donor.`) : null,
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
  const at = exchange.donors.indexOf(donor);
  return at < 0 ? [] : exchange.patients.slice(at);
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
const EXAMPLES = [
  ['Antigens that block most patients', `MATCH (p:Patient)-[:HAS_ANTIBODY]->(a:Antigen)
RETURN a.name AS antigen, count(p) AS patients
ORDER BY patients DESC LIMIT 10`],
  ['Who can give to P071', `MATCH (d:Donor)-[:COMPATIBLE]->(:Patient {id: 'P071'})
OPTIONAL MATCH (d)-[:PAIRED_WITH]->(own:Patient)
RETURN d.id AS donor, own.id AS donor_came_with, d.altruistic AS altruistic`],
  ['Exchanges in the latest run', `MATCH (r:MatchRun)-[:SELECTED]->(e:Exchange)
RETURN r.id AS run, e.kind AS kind, e.size AS size, count(*) AS exchanges
ORDER BY run DESC, kind, size`],
  ['Blood-type rules', `MATCH (g:BloodType)-[:ABO_CAN_GIVE_TO]->(r:BloodType)
RETURN g.name AS donor_blood, collect(r.name) AS can_give_to`],
];

$('#statement').value = EXAMPLES[0][1];
$('#examples').replaceChildren(
  ...EXAMPLES.map(([label, statement]) =>
    el('button', { type: 'button', class: 'chip', onclick: () => { $('#statement').value = statement; runConsole(); } }, label),
  ),
);
for (const chip of document.querySelectorAll('[data-filter]')) {
  chip.addEventListener('click', () => {
    state.filter = chip.dataset.filter;
    for (const c of document.querySelectorAll('[data-filter]')) c.setAttribute('aria-pressed', String(c === chip));
    drawExchanges();
  });
}

load().catch((err) => { $('#status').textContent = `Could not load the graph: ${err.message}`; $('#status').classList.add('warn'); });
