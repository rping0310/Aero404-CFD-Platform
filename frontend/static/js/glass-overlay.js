import { LiquidGlass } from '../vendor/ybouane-liquidglass.js?v=perf6';

// Real scene siblings, in viewport coordinates. The interactive dock stays
// above an empty optical shell so labels are never rasterized or refracted.
const root = document.querySelector('.app');
const workspace = document.querySelector('.workspace');
const extraContent = document.querySelector('.workspace-content');
const pinnedDesktop = () => innerWidth > 760 && document.body.classList.contains('dock-pinned');
const sources = () => pinnedDesktop() ? [ambient] : [ambient, workspace, extraContent].filter(Boolean);
const ambient = document.querySelector('.ambient');
const dock = document.querySelector('.sidebar');
// Controls are not scene sources: never snapshot the dock back into its lens.
dock.setAttribute('data-glass-ignore', '');
root.prepend(ambient);
const lens = document.createElement('div');
lens.className = 'scene-dock-lens';
lens.setAttribute('aria-hidden', 'true');
lens.hidden = true;
root.append(lens);
const reduced = matchMedia('(prefers-reduced-transparency: reduce)');
let engine, stopped = false, pending = false, busy = false, timer;
let until = 0, lastConfig = '', idleTimer, lastCapture = 0, epoch = 0;
const stats = window.aero404GlassStats = { captures: 0, lastCaptureMs: 0, sourceCaptures: 0, ignoredUpdates: 0 };
const dirtySources = new Set([ambient, workspace, extraContent].filter(Boolean));
function selectSources() {
  for (const node of [workspace, extraContent].filter(Boolean)) {
    if (pinnedDesktop()) node.setAttribute('data-glass-ignore', '');
    else node.removeAttribute('data-glass-ignore');
  }
  engine?.syncSceneSources();
}
const enabled = () => !reduced.matches && !document.body.classList.contains('reduced-glass');
const visible = () => root.offsetWidth > 0 && (!document.body.classList.contains('dock-hidden') || innerWidth <= 760) && !document.hidden;

function align() {
  const r = dock.getBoundingClientRect();
  lens.style.setProperty('--dock-opacity', getComputedStyle(dock).opacity);
  Object.assign(lens.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
  const config = JSON.stringify({ cornerRadius: parseFloat(getComputedStyle(dock).borderTopLeftRadius) || 32 });
  if (config !== lastConfig) { lens.dataset.config = config; lastConfig = config; }
  engine?.markChanged();
}
function suspendLens() {
  epoch++;
  lens.hidden = true;
  engine?.pause();
  document.body.classList.remove('scene-glass-ready');
}
function follow() {
  // Let CSS animate native glass. Rasterization/shader work starts only once
  // the dock and content lane have settled; no per-frame layout reads.
  suspendLens();
  until = performance.now() + (document.body.classList.contains('no-motion') ? 0 : 550);
}
function invalidate() {
  pending = true;
  if (!enabled() || !visible()) {
    clearTimeout(timer); timer = null;
    clearTimeout(idleTimer);
    suspendLens();
    return;
  }
  // Debounce scrolling/DOM bursts; keep the last valid texture while updating.
  clearTimeout(timer);
  if (!busy) timer = setTimeout(refresh, Math.max(250, 1000 - (performance.now() - lastCapture)));
}
async function refresh() {
  timer = null;
  if (!engine || stopped || !enabled() || !visible()) return;
  // Do not start DOM rasterization in the middle of the dock slide animation.
  if (performance.now() < until) {
    timer = setTimeout(refresh, until - performance.now() + 80);
    return;
  }
  busy = true; pending = false;
  const capturedEpoch = epoch;
  const started = performance.now();
  engine.pause();
  try {
    selectSources();
    const changed = sources().filter(node => dirtySources.has(node));
    changed.forEach(node => dirtySources.delete(node));
    await engine.refreshScene(changed);
    stats.sourceCaptures += changed.length;
    stats.captures++;
    stats.lastCaptureMs = Math.round(performance.now() - started);
    if (!stopped && !pending && capturedEpoch === epoch && enabled() && visible()) {
      lens.hidden = false;
      align();
      engine.resume();
      // Give the shader a frame before replacing native backdrop glass.
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (!stopped && !pending && capturedEpoch === epoch && enabled() && visible()) document.body.classList.add('scene-glass-ready');
      }));
    }
  } catch (error) {
    console.warn('Scene capture unavailable; native glass remains active.', error);
  } finally {
    busy = false;
    lastCapture = performance.now();
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => { if (!busy) engine?.pause(); }, 100);
    if (pending && !stopped && enabled() && visible()) timer = setTimeout(refresh, 1000);
  }
}

align();
selectSources();
try {
  engine = await LiquidGlass.init({ root, glassElements: [lens], defaults: {
    floating: false, blurAmount: 0.045, refraction: 0.5,
    chromAberration: 0.012, edgeHighlight: 0.08, specular: 0.12,
    fresnel: 0.45, zRadius: 12, shadowOpacity: 0, tintStrength: 0
  }});
  root.style.removeProperty('user-select');
  root.style.removeProperty('-webkit-user-select');
  engine.pause();
  const content = new MutationObserver(records => {
    let relevant = false;
    for (const source of [ambient, workspace, extraContent].filter(Boolean)) {
      if (records.some(r => r.target === source || source.contains(r.target))) {
        dirtySources.add(source);
        if (sources().includes(source)) relevant = true;
      }
    }
    if (relevant) invalidate(); else stats.ignoredUpdates++;
  });
  content.observe(workspace, { childList: true, subtree: true, characterData: true, attributes: true });
  if (extraContent) content.observe(extraContent, { childList: true, subtree: true, characterData: true, attributes: true });
  const session = new MutationObserver(() => {
    if (!root.offsetWidth) {
      suspendLens();
      document.body.prepend(ambient);
    } else {
      if (ambient.parentElement !== root) root.prepend(ambient);
      follow(); invalidate();
    }
  });
  session.observe(root, { attributes: true, attributeFilter: ['style'] });
  content.observe(ambient, { subtree: true, attributes: true });
  const body = new MutationObserver(records => {
    // Ignore our own ready flag: otherwise capture would invalidate itself.
    const classes = s => (s || '').split(/\s+/).filter(c => c !== 'scene-glass-ready').sort().join(' ');
    if (records.some(r => classes(r.oldValue) !== classes(document.body.className))) {
      const appearance = value => classes(value).split(/\s+/).filter(c => !['dock-hidden','dock-pinned'].includes(c)).join(' ');
      if (records.some(r => appearance(r.oldValue) !== appearance(document.body.className))) {
        [ambient, workspace, extraContent].filter(Boolean).forEach(s => dirtySources.add(s));
      }
      // Invalidate on hide too: the old guard skipped cleanup and left the
      // fixed optical canvas painted at the last visible dock position.
      follow();
      invalidate();
    }
  });
  body.observe(document.body, { attributes: true, attributeFilter: ['class'], attributeOldValue: true });
  const contentChanged = () => {
    [workspace, extraContent].filter(Boolean).forEach(s => dirtySources.add(s));
    if (!pinnedDesktop()) invalidate(); else stats.ignoredUpdates++;
  };
  workspace.addEventListener('scroll', contentChanged, true);
  if (extraContent) extraContent.addEventListener('scroll', contentChanged, true);
  document.addEventListener('scene-drawn', contentChanged);
  window.addEventListener('resize', follow);
  window.addEventListener('resize', () => {
    [ambient, workspace, extraContent].filter(Boolean).forEach(s => dirtySources.add(s));
    invalidate();
  });
  document.addEventListener('visibilitychange', invalidate);
  const dockVisibility = () => { follow(); invalidate(); };
  document.addEventListener('dock-visibility', dockVisibility);
  reduced.addEventListener('change', invalidate);
  invalidate();
  window.addEventListener('pagehide', () => {
    stopped = true; clearTimeout(timer); clearTimeout(idleTimer);
    document.removeEventListener('dock-visibility', dockVisibility);
    content.disconnect(); body.disconnect(); session.disconnect(); engine.destroy(); lens.remove();
    document.body.classList.remove('scene-glass-ready');
  }, { once: true });
} catch (error) {
  lens.remove();
  console.warn('WebGL unavailable; native backdrop glass remains active.', error);
}
