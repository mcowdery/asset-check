#!/usr/bin/env node
// asset-check: review sheets for 3D models and animations, made in headless Blender, for a person or an AI agent to look at
// instead of guessing. See README.md.
//
//   asset-check sheet <file> [--out dir] [--anim clip] [--clip names|all|none] [--frames n] [--size px]
//                            [--engine eevee|cycles] [--front auto|-y|+y|-x|+x] [--model|--no-model] [--no-anims] [--list]
//                            [--checks-only|--no-checks] [--verbose]
//   asset-check check <file> [the same]   the checks alone, no pictures (sheet --checks-only)
//   asset-check compare <a> <b> [the same] [--anim-b clip]   both, their sheets side by side (a on the left), and what
//                            differs: sizes, proportions, faults found in only one. Before and after a fix, or a new
//                            figure against an approved one. --anim-b plays another clip file on b (two versions of a clip)
//   asset-check blender      which Blender it would use
//
// Blender is found from BLENDER, then the PATH, then the usual install folders (the newest version). Output goes to --out, or
// to ./asset-check/<file name>/. Prints the sheets it wrote and the faults the checks found; exits 1 if Blender failed, 2 if
// a check found a fault (compare: a fault b has that a hasn't).
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { basename, dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SHEET_PY = join(HERE, '..', 'blender', 'sheet.py');

function findBlender() {
  if (process.env.BLENDER) return process.env.BLENDER;
  const which = spawnSync(process.platform === 'win32' ? 'where' : 'which', ['blender'], { encoding: 'utf8' });
  if (which.status === 0 && which.stdout.trim()) return which.stdout.trim().split(/\r?\n/)[0];
  const byVersion = (dir, exe) => {
    if (!existsSync(dir)) return null;
    const found = readdirSync(dir)
      .filter((d) => /^Blender/i.test(d) && existsSync(join(dir, d, exe)))
      .sort((a, b) => b.localeCompare(a, undefined, { numeric: true }));
    return found.length ? join(dir, found[0], exe) : null;
  };
  const candidates = [
    byVersion(join(process.env.ProgramFiles ?? 'C:/Program Files', 'Blender Foundation'), 'blender.exe'),
    '/Applications/Blender.app/Contents/MacOS/Blender',
    '/usr/bin/blender',
    '/usr/local/bin/blender',
    '/snap/bin/blender',
  ];
  return candidates.find((c) => c && existsSync(c)) ?? null;
}

function parse(argv) {
  const opts = { _: [] };
  const bare = new Set(['model', 'no-model', 'no-anims', 'no-checks', 'checks-only', 'no-plant', 'list', 'verbose']);
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) opts._.push(a);
    else if (bare.has(a.slice(2))) opts[a.slice(2)] = true;
    else opts[a.slice(2)] = argv[++i];
  }
  return opts;
}

function usage(code = 0) {
  console.log(readFileSync(fileURLToPath(import.meta.url), 'utf8').split('\n').slice(1, 16).map((l) => l.replace(/^\/\/ ?/, '')).join('\n'));
  process.exit(code);
}

const [cmd, ...rest] = process.argv.slice(2);
if (!cmd || cmd === '--help' || cmd === '-h') usage();

const blender = findBlender();
if (cmd === 'blender') {
  console.log(blender ?? 'no Blender found: set BLENDER to its executable');
  process.exit(blender ? 0 : 1);
}
if (!['sheet', 'check', 'compare'].includes(cmd)) usage(1);

const opts = parse(rest);
if (cmd === 'check') opts['checks-only'] = true;
const files = opts._.slice(0, cmd === 'compare' ? 2 : 1);
if (files.length < (cmd === 'compare' ? 2 : 1)) usage(1);
for (const f of files) {
  if (!existsSync(f)) {
    console.error(`asset-check: no such file: ${f}`);
    process.exit(1);
  }
}
if (!blender) {
  console.error('asset-check: no Blender found: set BLENDER to its executable');
  process.exit(1);
}

/** Runs Blender on a script, printing its own lines (those starting with `tag`); resolves when it's done, exits on failure. */
function blenderRun(script, args, tag) {
  return new Promise((done) => {
    const child = spawn(blender, ['-b', '--factory-startup', '--python-exit-code', '1', '--python', script, '--', ...args], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const log = [];
    const take = (chunk) => {
      for (const line of chunk.toString().split(/\r?\n/)) {
        if (!line) continue;
        log.push(line);
        if (opts.verbose) console.log(line);
        else if (line.startsWith(tag)) console.log(line.slice(tag.length));
      }
    };
    child.stdout.on('data', take);
    child.stderr.on('data', take);
    child.on('close', (code) => {
      if (code !== 0) {
        if (!opts.verbose) console.error(log.filter((l) => !/^(Fra:|\d\d:\d\d:\d\d \| INFO)/.test(l)).slice(-25).join('\n'));
        console.error(`asset-check: Blender exited with ${code}`);
        process.exit(1);
      }
      done();
    });
  });
}

/** One file through sheet.py into `out`; returns its report (null for --list). */
async function sheet(file, out, extra = {}) {
  const o = { ...opts, ...extra };
  const pass = ['--out', out];
  for (const k of ['anim', 'clip', 'frames', 'size', 'engine', 'front', 'samples', 'proportions', 'label']) if (o[k] != null) pass.push(`--${k}`, String(o[k]));
  for (const k of ['model', 'no-model', 'no-anims', 'no-checks', 'checks-only', 'no-plant', 'list']) if (o[k]) pass.push(`--${k}`);
  await blenderRun(SHEET_PY, [resolve(file), ...pass], 'SHEET ');
  const reportPath = join(out, 'report.json');
  if (o.list || !existsSync(reportPath)) return null;
  const report = JSON.parse(readFileSync(reportPath, 'utf8'));
  writeFileSync(join(out, 'index.html'), page(report));
  return report;
}

const faultsOf = (r) => [
  ...(r.model?.checks?.faults ?? []).map((f) => `model: ${f}`),
  ...r.clips.flatMap((c) => (c.checks?.faults ?? []).map((f) => `${c.name}: ${f}`)),
];

if (cmd !== 'compare') {
  const file = files[0];
  const out = resolve(opts.out ?? join('asset-check', basename(file, extname(file))));
  const report = await sheet(file, out);
  if (report) {
    console.log(`\n${join(out, 'index.html')}`);
    if (faultsOf(report).length) process.exitCode = 2;
  }
} else {
  const [fa, fb] = files;
  const name = (f) => basename(f, extname(f));
  const out = resolve(opts.out ?? join('asset-check', `${name(fa)}--${name(fb)}`));
  const same = resolve(fa) === resolve(fb);
  const la = same ? `A ${basename(opts.anim ?? fa)}` : `A ${basename(fa)}`;
  const lb = same ? `B ${basename(opts['anim-b'] ?? opts.anim ?? fb)}` : `B ${basename(fb)}`;
  console.log(`== ${la}`);
  const ra = await sheet(fa, join(out, 'a'), { label: la });
  console.log(`== ${lb}`);
  const rb = await sheet(fb, join(out, 'b'), { label: lb, anim: opts['anim-b'] ?? opts.anim });
  const pairs = ra.sheets.filter((s) => rb.sheets.includes(s));
  if (pairs.length) {
    await blenderRun(
      join(HERE, '..', 'blender', 'pair.py'),
      pairs.flatMap((s) => [join(out, `pair_${s}`), join(out, 'a', s), join(out, 'b', s)]),
      'PAIR ',
    );
  }
  const diff = compareReports(ra, rb);
  writeFileSync(join(out, 'compare.json'), JSON.stringify({ a: fa, b: fb, sheets: pairs.map((s) => `pair_${s}`), ...diff }, null, 2));
  writeFileSync(join(out, 'index.html'), comparePage(fa, fb, la, lb, pairs, diff));
  console.log('\nWhat differs (a → b):');
  for (const line of diff.lines) console.log(`  ${line}`);
  if (diff.onlyB.length) console.log(`\nFound in b only:\n${diff.onlyB.map((f) => `  ${f}`).join('\n')}`);
  if (diff.onlyA.length) console.log(`\nFound in a only (gone in b):\n${diff.onlyA.map((f) => `  ${f}`).join('\n')}`);
  console.log(`\n${join(out, 'index.html')}`);
  if (diff.onlyB.length) process.exitCode = 2;
}

/** What differs between two runs: sizes and proportions (changes over 1%), clips in one only, faults in one only. */
function compareReports(a, b) {
  const lines = [];
  const ma = a.model ?? {}, mb = b.model ?? {};
  const num = (label, x, y, fmt = (v) => v) => {
    if (x == null || y == null) return;
    const change = x ? (y - x) / Math.abs(x) : y ? 1 : 0;
    if (Math.abs(change) > 0.01) lines.push(`${label}: ${fmt(x)} → ${fmt(y)} (${change > 0 ? '+' : ''}${(change * 100).toFixed(1)}%)`);
  };
  num('height', ma.height, mb.height);
  num('triangles', ma.triangles, mb.triangles);
  num('joints', ma.joints, mb.joints);
  const pa = ma.checks?.proportions ?? {}, pb = mb.checks?.proportions ?? {};
  for (const k of new Set([...Object.keys(pa), ...Object.keys(pb)])) num(k.replace(/_/g, ' '), pa[k], pb[k], (v) => `${(v * 100).toFixed(1)}%`);
  const ca = new Map(a.clips.map((c) => [c.name, c])), cb = new Map(b.clips.map((c) => [c.name, c]));
  for (const [n, c] of ca) {
    const d = cb.get(n);
    if (!d) {
      lines.push(`clip ${n}: in a only`);
      continue;
    }
    num(`${n} duration`, c.duration, d.duration);
    num(`${n} pace`, c.checks?.pace, d.checks?.pace);
    num(`${n} loop gap`, c.checks?.loop_gap, d.checks?.loop_gap);
  }
  for (const n of cb.keys()) if (!ca.has(n)) lines.push(`clip ${n}: in b only`);
  if (!lines.length) lines.push('nothing measured differs by more than 1%');
  // a fault's measurements move when anything does: match faults by their words and counts (a piece's vertices, its
  // height to the percent), with measurements, angles and frame counts taken out
  const key = (f) =>
    f
      .replace(/\(\d+ of \d+ frames\)|\d+ frames/g, '# frames')
      .replace(/-?\d+°/g, '#°')
      .replace(/-?\d+\.\d+(e-?\d+)?/g, '#');
  const fa = faultsOf(a), fb = faultsOf(b);
  const ka = new Set(fa.map(key)), kb = new Set(fb.map(key));
  return { lines, onlyA: fa.filter((f) => !kb.has(key(f))), onlyB: fb.filter((f) => !ka.has(key(f))), both: fb.filter((f) => ka.has(key(f))) };
}

function comparePage(fa, fb, la, lb, pairs, diff) {
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const list = (title, items, cls) => (items.length ? `<h2>${title}</h2><ul class="${cls}">${items.map((f) => `<li>${esc(f)}</li>`).join('')}</ul>` : '');
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Compare · asset-check</title>
<style>
:root { color-scheme: dark; --bg: #121315; --fg: #e4e2de; --dim: #8d8b87; --warn: #e0a85c; }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg); font: 14px/1.5 system-ui, sans-serif; }
h1 { font-size: 18px; margin: 0 0 8px; } h2 { font-size: 15px; margin: 22px 0 6px; }
.dim { color: var(--dim); } .warn { color: var(--warn); } .ok { color: #8fc79a; } ul { padding-left: 20px; margin: 0; }
img { display: block; max-width: 100%; margin: 0 0 10px; border-radius: 3px; }
</style></head><body>
<h1>${esc(la)} <span class="dim">against</span> ${esc(lb)}</h1>
<p class="dim">a: ${esc(resolve(fa))}<br>b: ${esc(resolve(fb))} · <a href="a/index.html">a's page</a> · <a href="b/index.html">b's page</a></p>
<h2>What differs (a → b)</h2><ul>${diff.lines.map((l) => `<li>${esc(l)}</li>`).join('')}</ul>
${list('Found in b only', diff.onlyB, 'warn')}
${list('Found in a only (gone in b)', diff.onlyA, 'ok')}
${list('Found in both', diff.both, 'dim')}
${pairs.map((s) => `<h2>${esc(s)}</h2><a href="pair_${esc(s)}"><img src="pair_${esc(s)}" alt="${esc(s)}, a left, b right"></a>`).join('\n')}
</body></html>
`;
}

function modelChecks(k, esc) {
  if (!k) return '';
  const props = Object.entries(k.proportions ?? {});
  return `<h2>The model at rest</h2>
${k.faults.length ? `<ul class="faults">${k.faults.map((f) => `<li>${esc(f)}</li>`).join('')}</ul>` : '<p class="ok">No faults found.</p>'}
${props.length ? `<p class="facts">${props.map(([n, v]) => `${esc(n.replace(/_/g, ' '))} ${(v * 100).toFixed(1)}%`).join(' · ')} of the height</p>` : ''}`;
}

/** A small line chart as inline SVG: each series is [label, values (null for a gap), colour]. */
function chart(title, times, series, unit, marks = []) {
  const W = 640, Ht = 150, L = 44, R = 10, T = 22, B = 22;
  const vals = series.flatMap(([, v]) => v).filter((x) => x != null);
  if (!vals.length || times.length < 2) return '';
  const lo = Math.min(0, ...vals, ...marks);
  let hi = Math.max(...vals, ...marks);
  if (hi - lo < 1e-9) hi = lo + 1;
  const x = (i) => L + ((W - L - R) * (times[i] - times[0])) / (times.at(-1) - times[0]);
  const y = (v) => T + ((Ht - T - B) * (hi - v)) / (hi - lo);
  const path = (v) => v.map((p, i) => (p == null ? '' : `${i && v[i - 1] != null ? 'L' : 'M'}${x(i).toFixed(1)},${y(p).toFixed(1)}`)).join('');
  const fmt = (v) => (Math.abs(v) >= 10 ? v.toFixed(0) : v.toPrecision(2));
  return `<svg class="chart" viewBox="0 0 ${W} ${Ht}" role="img" aria-label="${title}">
<text x="${L}" y="14" class="t">${title}</text>
${series.map(([label, , colour], k) => `<text x="${W - R - 40 * (series.length - k)}" y="14" fill="${colour}">${label}</text>`).join('')}
<line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" class="axis"/>
${marks.map((mk) => `<line x1="${L}" x2="${W - R}" y1="${y(mk)}" y2="${y(mk)}" class="mark"/>`).join('')}
<text x="${L - 4}" y="${y(hi) + 4}" class="n" text-anchor="end">${fmt(hi)}${unit}</text>
<text x="${L - 4}" y="${y(lo) + 4}" class="n" text-anchor="end">${fmt(lo)}${unit}</text>
<text x="${W - R}" y="${Ht - 6}" class="n" text-anchor="end">${times.at(-1).toFixed(2)} s</text>
${series.map(([, v, colour]) => `<path d="${path(v)}" stroke="${colour}" fill="none" stroke-width="1.6"/>`).join('')}
</svg>`;
}

function checksHtml(c, height) {
  const k = c.checks;
  if (!k) return '';
  const esc = (s) => String(s).replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[ch]);
  const left = '#7fb2e5', right = '#e5a07f';
  const colour = (side) => (side === 'l' ? left : right);
  const parts = [
    k.faults.length ? `<ul class="faults">${k.faults.map((f) => `<li>${esc(f)}</li>`).join('')}</ul>` : '<p class="ok">No faults found.</p>',
    `<p class="facts">pace ${k.pace ?? 0} · lowest point ${k.lowest} · loop gap ${k.loop_gap}` +
      (k.stretch?.worst ? ` · worst stretch ${(k.stretch.worst * 100).toFixed(1)}% (${esc(k.stretch.bone)})` : '') + '</p>',
  ];
  if (k.feet) {
    const feet = Object.entries(k.feet);
    parts.push(chart('feet: height of the lowest point (dashed: down below this)', k.times, feet.map(([s, f]) => [s, f.height, colour(s)]), '', [0.02 * height]));
    parts.push(chart('feet: slide while down, off the pace', k.times, feet.map(([s, f]) => [s, f.slide_speed, colour(s)]), '/s'));
  }
  for (const kind of ['knee', 'elbow']) {
    const hs = Object.entries(k.hinges ?? {}).filter(([n]) => n.startsWith(kind));
    if (hs.length) parts.push(chart(`${kind}s: bend (below 0 is backwards)`, k.times, hs.map(([n, h]) => [n.slice(-1), h.bend, colour(n.slice(-1))]), '°'));
  }
  return parts.join('\n');
}

function page(r) {
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const m = r.model ?? {};
  const facts = [
    ['file', r.file],
    ['height', m.height],
    ['triangles', m.triangles ?? '(stick figure)'],
    ['rig', m.rig ? `${m.rig}, ${m.joints} joints` : 'none'],
    ['facing', `${(m.front ?? []).join(', ')} (${m.front_from})`],
    ['clips in file', (m.clips_in_file ?? []).length],
    ['Blender', `${r.blender}, ${r.seconds} s`],
  ];
  const modelSheets = r.sheets.filter((s) => s.startsWith('model_'));
  const clips = r.clips
    .map(
      (c) => `<h2>${esc(c.name)}</h2>
<p class="facts">${c.duration} s · travels ${c.travel} · camera ${c.camera} · lowest point ${c.lowest_point_above_ground} above the ground</p>
${checksHtml(c, m.height)}
${c.sheets.map((s) => `<a href="${esc(s)}"><img src="${esc(s)}" alt="${esc(s)}"></a>`).join('\n')}`,
    )
    .join('\n');
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(basename(r.file))} · asset-check</title>
<style>
:root { color-scheme: dark; --bg: #121315; --fg: #e4e2de; --dim: #8d8b87; --warn: #e0a85c; }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg); font: 14px/1.5 system-ui, sans-serif; }
h1 { font-size: 18px; margin: 0 0 8px; } h2 { font-size: 15px; margin: 28px 0 6px; }
table { border-collapse: collapse; margin-bottom: 12px; } td { padding: 2px 14px 2px 0; vertical-align: top; }
td:first-child { color: var(--dim); } .facts { color: var(--dim); margin: 0 0 8px; }
.warn, .faults { color: var(--warn); } .ok { color: #8fc79a; } .faults { margin: 0 0 8px; padding-left: 20px; }
.chart { display: block; width: 100%; max-width: 640px; margin: 0 0 6px; font: 11px system-ui, sans-serif; }
.chart .t { fill: var(--fg); } .chart .n { fill: var(--dim); } .chart .axis { stroke: #444; }
.chart .mark { stroke: #6a5a3a; stroke-dasharray: 3 3; }
img { display: block; max-width: 100%; margin: 0 0 10px; border-radius: 3px; }
</style></head><body>
<h1>${esc(basename(r.file))}</h1>
<table>${facts.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('')}</table>
${r.warnings.map((w) => `<p class="warn">${esc(w)}</p>`).join('\n')}
${modelChecks(m.checks, esc)}
${modelSheets.map((s) => `<a href="${esc(s)}"><img src="${esc(s)}" alt="${esc(s)}"></a>`).join('\n')}
${clips}
</body></html>
`;
}
