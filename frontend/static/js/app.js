// Aero404 CFD Platform — front-end logic (Alpine.js)
//
// This revision:
//   - Case List: also include Aero_balance_* keys (not just mean_*)
//   - Job Session: Type / CLA / Z Balance columns, date shows HH:MM (24h),
//     admin can delete/clear FAILED records, Note column (short notes shown
//     inline, long notes need a click to expand; editable after creation)
//   - Plots: per-pane independent "Visible Series" checklist placed below
//     each plot (no more shared mini info tables under Plots)
//   - 3D: STL parsing moved to a Web Worker so switching tabs never freezes
//     the page while a large model is loading; sizing now tracks the
//     container with a ResizeObserver so it always fills the available
//     space, including when a Sub pane is added/removed
//   - Slices: play buttons are icon-only; mini Parameters/Results tables
//     kept (own scroll area, doesn't shrink the image)
//   - All interface text translated to English

function platform() {
  return {
    // ---------------- basic state ----------------
    user: null,
    tab: 'jobs',
    tabs: [
      { id: 'cases', icon: '📋', label: 'Case List' },
      { id: 'slices', icon: '🖼️', label: 'Slices' },
      { id: 'plots', icon: '📈', label: 'Plots' },
      { id: '3d', icon: '🧊', label: '3D' },
      { id: 'jobs', icon: '🗂️', label: 'Job Session' },
      { id: 'settings', icon: '⚙️', label: 'Settings' },
      { id: 'about', icon: 'ℹ️', label: 'About' },
    ],
    error: '',
    message: '',
    loginForm: { name: '', password: '' },
    pollTimer: null,
    simTypeLabels: { 1: 'Straight', 2: 'Yaw', 3: 'Cornering' },

    // ---------------- Job Session ----------------
    jobs: [],
    servers: [],
    addOpen: false,
    waitingOpen: false,
    noteModalOpen: false,
    noteModalJob: null,
    noteModalText: '',
    // LOGKILL_PATCH: log viewer
    logOpen: false, logJob: null, logText: '', logError: '',
    logLines: 1000, logAuto: true, logTimer: null,
    logPage: 0, logCur: 1, logPages: 1, logTotal: 0,   // logPage 0 = follow the last page
    newJob: { case_name: '', simulation_type: 1, server_name: '', mode: 'half', parameters: {}, file: null, force: false, note: '' },
    jobPageSize: 30,
    jobPage: 1,

    // ---------------- Case List / Main-Sub ----------------
    cases: [],
    resultKeys: [],
    selected: [],
    mainCase: '',
    subCase: '',
    sortKey: 'name',
    sortDir: 1,
    caseFolderFilter: 'Straight',

    // ---------------- Slices (Main / Sub independent state) ----------------
    sliceTypesMain: [], sliceTypesSub: [],
    sliceTypeMain: '', sliceTypeSub: '',
    sliceFilesMain: [], sliceFilesSub: [],
    sliceIndexMain: 0, sliceIndexSub: 0,
    zoomMain: 1, zoomSub: 1,
    panXMain: 0, panYMain: 0, panXSub: 0, panYSub: 0,
    isPanning: false, panRole: null, panOrigin: [0, 0],
    playDirectionMain: 0, playDirectionSub: 0,   // 0=paused 1=forward -1=reverse
    playTimerMain: null, playTimerSub: null,
    playSpeedMain: 1, playSpeedSub: 1,
    syncSliceType: true,
    syncSlicePosition: true,
    syncZoom: false,
    paramsMain: {}, resultMain: {},
    paramsSub: {}, resultSub: {},

    // ---------------- Plots ----------------
    csvFiles: [],
    selectedCsv: '',
    plotSeriesNamesMain: [], plotSeriesNamesSub: [],
    plotVisibleSeriesMain: {}, plotVisibleSeriesSub: {},

    // ---------------- 3D ----------------
    syncCamera: false,
    threeScenes: { main: null, sub: null },
    stlLoading: { main: false, sub: false },
    stlError: { main: '', sub: '' },
    stlGeometry: { main: null, sub: null },
    stlEpoch: { main: 0, sub: 0 },
    stlRenderedFor: { main: null, sub: null },
    _stlWorker: null,
    _stlRequestId: 0,
    _stlPending: {},

    // ---------------- Settings ----------------
    defaultsText: '', defaultsMtime: null,
    loginLog: [],

    // ================================================================
    // shared API helper
    // ================================================================
    async api(url, options = {}) {
      const r = await fetch(url, { credentials: 'same-origin', ...options });
      if (!r.ok) {
        let detail = {};
        try { detail = await r.json(); } catch (e) { /* ignore */ }
        throw new Error(detail.detail || `${r.status} ${r.statusText}`);
      }
      const type = r.headers.get('content-type') || '';
      return type.includes('json') ? r.json() : r;
    },

    // lets the same function operate on either the Main or Sub pane's
    // fields, e.g. this._k('sliceType','main') -> 'sliceTypeMain'
    _k(base, role) {
      return base + (role === 'main' ? 'Main' : 'Sub');
    },
    _other(role) {
      return role === 'main' ? 'sub' : 'main';
    },

    // ================================================================
    // lifecycle / auth
    // ================================================================
    async init() {
      try {
        this.user = await this.api('/api/me');
        await this.refreshAll();
        this.pollTimer = setInterval(() => this.refreshJobs(), 5000);
      } catch (e) {
        this.user = null;
      }
      window.addEventListener('keydown', (e) => this.onKeydown(e));
    },

    async login() {
      this.error = '';
      try {
        this.user = await this.api('/api/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.loginForm),
        });
        await this.refreshAll();
        this.pollTimer = setInterval(() => this.refreshJobs(), 5000);
      } catch (e) {
        this.error = e.message;
      }
    },

    async logout() {
      await this.api('/api/logout', { method: 'POST' });
      clearInterval(this.pollTimer);
      this.user = null;
    },

    async refreshAll() {
      await Promise.all([this.refreshJobs(), this.loadCases(), this.loadSettings()]);
    },

    async selectTab(id) {
      this.tab = id;
      if (id === 'jobs') await this.refreshJobs();
      if (id === 'cases') await this.loadCases();
      if (id === 'settings') await this.loadSettings();
      if (id === 'plots') await this.loadCsvFiles();
      if (id === 'slices' && this.sliceTypesMain.length === 0 && this.sliceTypesSub.length === 0) {
        await this.loadSlicesForBothPanes();
      }
      if (id === '3d') {
        // The container only has a real, laid-out size once this section is
        // actually visible, so this is the right moment to attach the
        // renderer for any geometry that already finished loading in the
        // background while the user was on a different tab.
        for (const role of ['main', 'sub']) {
          const cached = this.stlGeometry[role];
          if (cached && !cached.error && this.stlRenderedFor[role] !== cached.caseName) {
            this.renderStlFromCache(role);
          }
        }
      }
    },

    flash(text) {
      this.message = text;
      setTimeout(() => { this.message = ''; }, 3500);
    },

    // ================================================================
    // Case List
    // ================================================================
    async loadCases() {
      this.cases = await this.api('/api/viewer/cases');
      // Only keep mean_* fields (e.g. mean_FW_DF) and the Aero_balance_*
      // fields, in the order they first appear — this preserves whatever
      // order Result.dat itself was written in. Anything else (older,
      // unrelated, or unprefixed report names) is left out so a future
      // report format change doesn't need a front-end code change.
      const keys = [];
      for (const c of this.cases) {
        for (const k of Object.keys(c.result || {})) {
          if (!k.startsWith('mean_') && !k.startsWith('Aero_balance')) continue;
          if (!keys.includes(k)) keys.push(k);
        }
      }
      this.resultKeys = keys;
    },

    humanizeKey(key) {
      return key.replace(/^mean_/, '').replace(/_/g, ' ');
    },

    formatResultValue(v) {
      if (v === undefined || v === null || v === '') return '—';
      const n = Number(v);
      return Number.isFinite(n) ? n.toFixed(4) : String(v);
    },

    foldersAvailable() {
      return [...new Set(this.cases.map((c) => c.folder))].sort();
    },

    filteredCases() {
      if (this.caseFolderFilter === 'all') return this.cases;
      return this.cases.filter((c) => c.folder === this.caseFolderFilter);
    },

    sortBy(key) {
      if (this.sortKey === key) {
        this.sortDir *= -1;
      } else {
        this.sortKey = key;
        this.sortDir = 1;
      }
    },

    sortArrow(key) {
      if (this.sortKey !== key) return '';
      return this.sortDir === 1 ? '▲' : '▼';
    },

    sortedCases() {
      const key = this.sortKey;
      const dir = this.sortDir;
      const value = (c) => {
        if (key === 'name') return c.name;
        if (key === 'folder') return c.folder;
        return c.result ? c.result[key] : undefined;
      };
      return [...this.filteredCases()].sort((a, b) => {
        const av = value(a); const bv = value(b);
        const an = Number(av); const bn = Number(bv);
        let cmp;
        if (Number.isFinite(an) && Number.isFinite(bn)) {
          cmp = an - bn;
        } else {
          cmp = String(av ?? '').localeCompare(String(bv ?? ''));
        }
        return cmp * dir;
      });
    },

    toggleSelect(name) {
      const i = this.selected.indexOf(name);
      if (i === -1) this.selected.push(name); else this.selected.splice(i, 1);
    },

    loadAsMain() {
      if (!this.selected.length) return;
      this.mainCase = this.selected[0];
      this.selected = [];
      this.resetViewerState();
    },

    loadAsSub() {
      if (!this.selected.length) return;
      const pick = this.selected.find((n) => n !== this.mainCase) || this.selected[0];
      this.subCase = pick;
      this.selected = [];
      this.resetViewerState();
    },

    resetViewerState() {
      this.sliceFilesMain = []; this.sliceFilesSub = [];
      this.sliceTypesMain = []; this.sliceTypesSub = [];
      this.sliceIndexMain = 0; this.sliceIndexSub = 0;
      this.playDirectionMain = 0; this.playDirectionSub = 0;
      clearInterval(this.playTimerMain); clearInterval(this.playTimerSub);
      this.paramsMain = {}; this.resultMain = {};
      this.paramsSub = {}; this.resultSub = {};
      this.plotSeriesNamesMain = []; this.plotSeriesNamesSub = [];
      this.csvFiles = [];
      this.selectedCsv = '';
      if (this.tab === 'plots') this.loadCsvFiles();
      if (this.tab === 'slices') this.loadSlicesForBothPanes();
      // Kick off the 3D fetch immediately on Load as Main/Sub — don't make
      // the user go to the 3D tab first before anything starts loading.
      this.fetchStl('main');
      if (this.subCase) this.fetchStl('sub'); else this.clearStlPane('sub');
    },

    // ================================================================
    // Job Session
    // ================================================================
    async refreshJobs() {
      if (!this.user) return;
      try {
        [this.jobs, this.servers] = await Promise.all([this.api('/api/jobs'), this.api('/api/servers')]);
      } catch (e) {
        if (e.message === 'Please log in') this.user = null;
      }
    },

    jobsSorted() {
      const priority = { SOLVING: 0, WAITING: 1, DONE: 2, FAILED: 3 };
      return [...this.jobs].sort((a, b) => {
        const p = (priority[a.status] ?? 9) - (priority[b.status] ?? 9);
        if (p !== 0) return p;
        return String(b.created_at).localeCompare(String(a.created_at));
      });
    },

    jobsPaged() {
      const all = this.jobsSorted();
      if (this.jobPageSize === 'all') return all;
      const start = (this.jobPage - 1) * this.jobPageSize;
      return all.slice(start, start + this.jobPageSize);
    },

    jobTotalPages() {
      if (this.jobPageSize === 'all') return 1;
      return Math.max(1, Math.ceil(this.jobs.length / this.jobPageSize));
    },

    changeJobPageSize(size) {
      this.jobPageSize = size === 'all' ? 'all' : Number(size);
      this.jobPage = 1;
    },

    goToJobPage(delta) {
      const total = this.jobTotalPages();
      this.jobPage = Math.min(total, Math.max(1, this.jobPage + delta));
    },

    typeLabel(job) {
      return this.simTypeLabels[job.simulation_type] || '—';
    },

    // CLA / Z Balance are CFD results, which only exist once a case has
    // actually finished running (Result.dat written); look them up from
    // whatever the Case List already fetched, matched by case name.
    caseResultValue(caseName, key) {
      const c = this.cases.find((x) => x.name === caseName);
      return c && c.result ? c.result[key] : undefined;
    },

    formatDate(iso) {
      if (!iso) return '—';
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return '—';
      const yyyy = d.getFullYear();
      const mm = String(d.getMonth() + 1).padStart(2, '0');
      const dd = String(d.getDate()).padStart(2, '0');
      const hh = String(d.getHours()).padStart(2, '0');
      const mi = String(d.getMinutes()).padStart(2, '0');
      return `${yyyy}/${mm}/${dd} ${hh}:${mi}`;
    },

    formatRuntime(v) {
      if (v == null) return '—';
      const s = Math.floor(v);
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      return `${h ? `${h}h ` : ''}${m}m ${s % 60}s`;
    },

    noteIsShort(note) {
      return !!note && note.length <= 32;
    },

    // LOGKILL_PATCH: log viewer
    openLog(job) {
      this.logJob = job;
      this.logText = '';
      this.logError = '';
      this.logAuto = (job.status === 'SOLVING');
      this.logPage = 0; this.logCur = 1; this.logPages = 1; this.logTotal = 0;
      this.logOpen = true;
      this.refreshLog(true);
      clearInterval(this.logTimer);
      this.logTimer = setInterval(() => {
        if (this.logOpen && this.logAuto && this.logPage === 0) this.refreshLog();
      }, 3000);
    },

    closeLog() {
      this.logOpen = false;
      clearInterval(this.logTimer);
      this.logTimer = null;
    },

    // pos: true/'bottom' = jump to bottom, 'top' = jump to top, undefined = stay (follow only if already at bottom)
    async refreshLog(pos) {
      if (!this.logJob) return;
      const box = this.$refs.logBox;
      const atBottom = pos === 'top' ? false
        : (!box || pos === true || pos === 'bottom' || (box.scrollHeight - box.scrollTop - box.clientHeight < 40));
      try {
        const r = await this.api(`/api/jobs/${this.logJob.id}/log?lines=${this.logLines}&page=${this.logPage}`);
        this.logText = r.text;
        this.logCur = r.page; this.logPages = r.pages; this.logTotal = r.total;
        this.logError = '';
        if (r.status !== 'SOLVING') this.logAuto = false;   // finished: stop polling
        this.$nextTick(() => {
          const b = this.$refs.logBox; if (!b) return;
          if (pos === 'top') b.scrollTop = 0;
          else if (atBottom) b.scrollTop = b.scrollHeight;
        });
      } catch (e) {
        this.logError = e.message;
      }
    },

    // p: 1..logPages = that page, 0 = last page (live, auto-refresh works there)
    logGo(p) {
      const goingBack = p > 0 && p === this.logCur - 1;
      if (p > 0 && p >= this.logPages) p = 0;
      this.logPage = p < 0 ? 1 : p;
      this.refreshLog(goingBack || this.logPage === 0 ? 'bottom' : 'top');
    },

    openNoteModal(job) {
      this.noteModalJob = job;
      this.noteModalText = job.note || '';
      this.noteModalOpen = true;
    },

    async saveNote() {
      if (!this.noteModalJob) return;
      try {
        await this.api(`/api/jobs/${this.noteModalJob.id}/note`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ note: this.noteModalText }),
        });
        this.noteModalOpen = false;
        await this.refreshJobs();
      } catch (e) {
        this.flash(e.message);
      }
    },

    async openAdd() {
      const d = await this.api('/api/settings/case_parameter_default');
      this.newJob = {
        case_name: '', simulation_type: 1,
        server_name: this.servers[0]?.name || '',
        mode: 'half',
        parameters: { ...d.content, simulation_type: '1' },
        file: null, force: false, note: '',
      };
      this.error = '';
      this.addOpen = true;
    },

    async createJob() {
      this.error = '';
      this.newJob.parameters.simulation_type = String(this.newJob.simulation_type);
      const f = new FormData();
      for (const k of ['case_name', 'simulation_type', 'server_name', 'mode']) f.append(k, this.newJob[k]);
      f.append('parameters', JSON.stringify(this.newJob.parameters));
      f.append('force', this.newJob.force ? 'true' : 'false');
      f.append('note', this.newJob.note || '');
      if (this.newJob.file) f.append('upload', this.newJob.file);
      try {
        const job = await this.api('/api/jobs', { method: 'POST', body: f });
        await this.refreshJobs();
        // HTTP 200 only means the job record was created — it does not
        // mean starccm+ actually started successfully. If the SSH/start
        // step failed, the backend marks the job FAILED with a reason,
        // and that must not be reported as a plain success.
        if (job.status === 'FAILED') {
          this.addOpen = false;
          this.flash(`⚠️ Case created, but failed to start: ${job.error_message || 'unknown reason, check the Job Session table'}`);
        } else {
          this.addOpen = false;
          this.flash('Case created and queued');
        }
      } catch (e) {
        this.error = e.message;
      }
    },

    async killJob(id) {
      if (!confirm('Terminate this job?')) return;
      try {
        await this.api(`/api/jobs/${id}`, { method: 'DELETE' });
        await this.refreshJobs();
      } catch (e) {
        this.flash(e.message);
      }
    },

    async deleteJobRecord(id) {
      if (!confirm('Permanently delete this job record? This cannot be undone.')) return;
      try {
        await this.api(`/api/jobs/${id}/record`, { method: 'DELETE' });
        await this.refreshJobs();
      } catch (e) {
        this.flash(e.message);
      }
    },

    async clearFailedJobs() {
      if (!confirm('Clear all FAILED job records? This cannot be undone.')) return;
      try {
        const r = await this.api('/api/jobs/failed', { method: 'DELETE' });
        this.flash(`Cleared ${r.removed} FAILED record(s)`);
        await this.refreshJobs();
      } catch (e) {
        this.flash(e.message);
      }
    },

    openWaitingList() {
      this.waitingOpen = true;
    },

    waitingByServer() {
      const groups = {};
      for (const j of this.jobs) {
        if (j.status !== 'WAITING') continue;
        (groups[j.server_name] ||= []).push(j);
      }
      for (const name in groups) {
        groups[name].sort((a, b) => a.queue_position - b.queue_position);
      }
      return groups;
    },

    async jumpQueue(job) {
      const groups = this.waitingByServer();
      const ids = (groups[job.server_name] || []).map((j) => j.id);
      const reordered = [job.id, ...ids.filter((id) => id !== job.id)];
      try {
        await this.api('/api/jobs/reorder', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ server_name: job.server_name, job_ids: reordered }),
        });
        await this.refreshJobs();
      } catch (e) {
        this.flash(e.message);
      }
    },

    // ================================================================
    // Slices
    // ================================================================
    async loadSlicesForBothPanes() {
      await this.loadSliceTypes('main');
      if (this.subCase) await this.loadSliceTypes('sub');
      await this.loadSliceFiles('main');
      if (this.subCase) await this.loadSliceFiles('sub');
      await this.loadMiniInfo('main');
      if (this.subCase) await this.loadMiniInfo('sub');
    },

    async loadMiniInfo(role) {
      const caseName = role === 'main' ? this.mainCase : this.subCase;
      const paramsKey = this._k('params', role);
      const resultKey = this._k('result', role);
      if (!caseName) { this[paramsKey] = {}; this[resultKey] = {}; return; }
      try { this[paramsKey] = await this.api(`/api/viewer/case/${encodeURIComponent(caseName)}/parameters`); }
      catch (e) { this[paramsKey] = {}; }
      try { this[resultKey] = await this.api(`/api/viewer/case/${encodeURIComponent(caseName)}/result`); }
      catch (e) { this[resultKey] = {}; }
    },

    async loadSliceTypes(role) {
      const caseName = role === 'main' ? this.mainCase : this.subCase;
      const typesKey = this._k('sliceTypes', role);
      if (!caseName) { this[typesKey] = []; return; }
      try {
        this[typesKey] = await this.api(`/api/viewer/case/${encodeURIComponent(caseName)}/slice_types`);
      } catch (e) {
        this[typesKey] = [];
      }
      const typeKey = this._k('sliceType', role);
      if (!this[typesKey].includes(this[typeKey])) {
        this[typeKey] = this[typesKey][0] || '';
      }
    },

    async onSliceTypeChange(role) {
      if (this.syncSliceType) {
        const other = this._other(role);
        this[this._k('sliceType', other)] = this[this._k('sliceType', role)];
        await this.loadSliceFiles(other);
      }
      await this.loadSliceFiles(role);
    },

    async loadSliceFiles(role) {
      const caseName = role === 'main' ? this.mainCase : this.subCase;
      const sliceType = this[this._k('sliceType', role)];
      const filesKey = this._k('sliceFiles', role);
      if (!caseName || !sliceType) { this[filesKey] = []; return; }
      try {
        this[filesKey] = await this.api(
          `/api/viewer/case/${encodeURIComponent(caseName)}/slices/${encodeURIComponent(sliceType)}`
        );
      } catch (e) {
        this[filesKey] = [];
      }
      this[this._k('sliceIndex', role)] = 0;
    },

    sliceUrl(role) {
      const files = this[this._k('sliceFiles', role)];
      const caseName = role === 'main' ? this.mainCase : this.subCase;
      const sliceType = this[this._k('sliceType', role)];
      if (!caseName || !files.length) return '';
      const idx = Math.min(this[this._k('sliceIndex', role)], files.length - 1);
      return `/api/viewer/case/${encodeURIComponent(caseName)}/slice/${encodeURIComponent(sliceType)}/${idx}`;
    },

    maxFrameCount(role) {
      return this[this._k('sliceFiles', role)].length;
    },

    setFrame(role, value) {
      const max = this.maxFrameCount(role);
      if (!max) return;
      const idx = Math.max(0, Math.min(max - 1, Math.round(Number(value))));
      this[this._k('sliceIndex', role)] = idx;
      if (this.syncSlicePosition) {
        const other = this._other(role);
        const otherMax = this.maxFrameCount(other);
        if (otherMax) this[this._k('sliceIndex', other)] = Math.min(idx, otherMax - 1);
      }
    },

    stepFrame(role, delta) {
      const max = this.maxFrameCount(role);
      if (!max) return;
      const key = this._k('sliceIndex', role);
      const next = (this[key] + delta + max) % max;
      this.setFrame(role, next);
    },

    togglePlay(role, direction) {
      if (!this.maxFrameCount(role)) return;
      const dirKey = this._k('playDirection', role);
      const timerKey = this._k('playTimer', role);
      clearInterval(this[timerKey]);
      if (this[dirKey] === direction) {
        this[dirKey] = 0;
        return;
      }
      this[dirKey] = direction;
      const speed = this[this._k('playSpeed', role)];
      this[timerKey] = setInterval(() => this.stepFrame(role, direction), 250 / speed);
    },

    onKeydown(e) {
      if (this.tab !== 'slices') return;
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
      if (e.key === 'j' || e.key === 'J') this.stepFrame('main', -1);
      else if (e.key === 'l' || e.key === 'L') this.stepFrame('main', 1);
      else if (e.code === 'Space') { e.preventDefault(); this.togglePlay('main', 1); }
    },

    // Cursor-anchored zoom: pan' = cursorOffset*(1-k) + k*pan, where
    // k = newZoom/oldZoom. This keeps the point under the cursor fixed on
    // screen across the zoom change (verified independently with a small
    // math test before wiring this up).
    zoomWheel(role, e) {
      const rect = e.currentTarget.getBoundingClientRect();
      const cursorX = e.clientX - rect.left - rect.width / 2;
      const cursorY = e.clientY - rect.top - rect.height / 2;
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      this._zoomAt(role, cursorX, cursorY, factor, rect.width, rect.height);
    },

    zoomButton(role, factor) {
      const el = document.getElementById(`slice-viewport-${role}`);
      const w = el ? el.clientWidth : 800;
      const h = el ? el.clientHeight : 450;
      this._zoomAt(role, 0, 0, factor, w, h);
    },

    _zoomAt(role, cursorX, cursorY, factor, containerW, containerH) {
      const zoomKey = this._k('zoom', role);
      const panXKey = this._k('panX', role);
      const panYKey = this._k('panY', role);
      const oldZoom = this[zoomKey];
      const newZoom = Math.min(5, Math.max(1, oldZoom * factor));
      const k = newZoom / oldZoom;
      let panX = cursorX * (1 - k) + k * this[panXKey];
      let panY = cursorY * (1 - k) + k * this[panYKey];
      const clamped = this._clampPan(panX, panY, newZoom, containerW, containerH);
      this[zoomKey] = newZoom;
      this[panXKey] = clamped.panX;
      this[panYKey] = clamped.panY;
      if (this.syncZoom) {
        const other = this._other(role);
        this[this._k('zoom', other)] = newZoom;
        this[this._k('panX', other)] = clamped.panX;
        this[this._k('panY', other)] = clamped.panY;
      }
    },

    _clampPan(panX, panY, zoom, containerW, containerH) {
      const maxX = Math.max(0, (zoom - 1) * containerW / 2);
      const maxY = Math.max(0, (zoom - 1) * containerH / 2);
      return {
        panX: Math.min(maxX, Math.max(-maxX, panX)),
        panY: Math.min(maxY, Math.max(-maxY, panY)),
      };
    },

    panStart(role, e) {
      this.isPanning = true;
      this.panRole = role;
      this.panOrigin = [e.clientX - this[this._k('panX', role)], e.clientY - this[this._k('panY', role)]];
    },

    panMove(e) {
      if (!this.isPanning) return;
      const role = this.panRole;
      const rect = e.currentTarget.getBoundingClientRect();
      let panX = e.clientX - this.panOrigin[0];
      let panY = e.clientY - this.panOrigin[1];
      const zoom = this[this._k('zoom', role)];
      const clamped = this._clampPan(panX, panY, zoom, rect.width, rect.height);
      this[this._k('panX', role)] = clamped.panX;
      this[this._k('panY', role)] = clamped.panY;
      if (this.syncZoom) {
        const other = this._other(role);
        this[this._k('panX', other)] = clamped.panX;
        this[this._k('panY', other)] = clamped.panY;
      }
    },

    panEnd() {
      this.isPanning = false;
      this.panRole = null;
    },

    resetView(role) {
      this[this._k('zoom', role)] = 1;
      this[this._k('panX', role)] = 0;
      this[this._k('panY', role)] = 0;
      if (this.syncZoom) {
        const other = this._other(role);
        this[this._k('zoom', other)] = 1;
        this[this._k('panX', other)] = 0;
        this[this._k('panY', other)] = 0;
      }
    },

    sliceImgStyle(role) {
      const zoom = this[this._k('zoom', role)];
      const panX = this[this._k('panX', role)];
      const panY = this[this._k('panY', role)];
      return `transform: translate(${panX}px, ${panY}px) scale(${zoom});`;
    },

    // ================================================================
    // Plots
    // ================================================================
    async loadCsvFiles() {
      const fetchFor = async (caseName) => {
        if (!caseName) return [];
        try { return await this.api(`/api/viewer/case/${encodeURIComponent(caseName)}/csv`); }
        catch (e) { return []; }
      };
      const [mainFiles, subFiles] = await Promise.all([fetchFor(this.mainCase), fetchFor(this.subCase)]);
      const union = [...new Set([...mainFiles, ...subFiles])].sort();
      this.csvFiles = union;
      if (!this.selectedCsv || !union.includes(this.selectedCsv)) {
        this.selectedCsv = union[0] || '';
      }
      if (this.selectedCsv) await this.drawPlots();
    },

    // Any column whose name contains "iteration" is dropped from the
    // traces (it's already the x-axis). This matters for files that have
    // one "<Monitor> Iteration" column per monitor (e.g. DF files with
    // several monitors) — not just column 0.
    _plottableColumns(columns) {
      return columns
        .map((name, i) => ({ name, i }))
        .filter(({ name, i }) => i > 0 && !/iteration/i.test(name));
    },

    async drawPlots() {
      if (!this.selectedCsv) return;
      await this.drawOnePlot('main', 'plot-main', this.mainCase);
      await this.drawOnePlot('sub', 'plot-sub', this.subCase);
    },

    async drawOnePlot(role, divId, caseName) {
      const host = document.getElementById(divId);
      const namesKey = this._k('plotSeriesNames', role);
      if (!host) return;
      if (!caseName) { host.innerHTML = ''; this[namesKey] = []; return; }
      try {
        const d = await this.api(`/api/viewer/case/${encodeURIComponent(caseName)}/csv/${encodeURIComponent(this.selectedCsv)}`);
        const cols = this._plottableColumns(d.columns);
        const visKey = this._k('plotVisibleSeries', role);
        this[namesKey] = cols.map((c) => c.name);
        for (const name of this[namesKey]) {
          if (!(name in this[visKey])) this[visKey][name] = true;
        }
        const x = d.rows.map((r) => r[0]);
        const traces = cols.map(({ name, i }) => ({
          x, y: d.rows.map((r) => r[i]), name, type: 'scatter', mode: 'lines',
          visible: this[visKey][name] === false ? 'legendonly' : true,
        }));
        Plotly.newPlot(divId, traces, {
          paper_bgcolor: '#ffffff', plot_bgcolor: '#ffffff',
          font: { color: '#1f2530' },
          xaxis: { title: d.columns[0], gridcolor: '#e5e7eb' },
          yaxis: { type: d.log_scale ? 'log' : 'linear', gridcolor: '#e5e7eb' },
          margin: { t: 20, r: 12, b: 40, l: 55 },
          showlegend: false,
        }, { responsive: true, displayModeBar: false });
      } catch (e) {
        host.innerHTML = `<div class="slice-empty">Could not find ${this.selectedCsv} (${e.message})</div>`;
        this[namesKey] = [];
      }
    },

    toggleSeriesVisible(role, name) {
      const visKey = this._k('plotVisibleSeries', role);
      this[visKey][name] = !this[visKey][name];
      this.drawOnePlot(role, role === 'main' ? 'plot-main' : 'plot-sub', role === 'main' ? this.mainCase : this.subCase);
    },

    // ================================================================
    // 3D
    // ================================================================
    // STL parsing runs in a Web Worker (frontend/static/js/stl-worker.js)
    // so a multi-million-triangle model never blocks the main thread —
    // the tab bar and everything else stays responsive while it loads.
    _getStlWorker() {
      if (!this._stlWorker) {
        this._stlWorker = new Worker('/static/js/stl-worker.js');
        this._stlWorker.onmessage = (e) => {
          const { id, positions, normals, error } = e.data;
          const pending = this._stlPending[id];
          if (!pending) return;
          delete this._stlPending[id];
          if (error) pending.reject(new Error(error));
          else pending.resolve({ positions, normals });
        };
      }
      return this._stlWorker;
    },

    _parseStlInWorker(buffer) {
      return new Promise((resolve, reject) => {
        const worker = this._getStlWorker();
        const id = ++this._stlRequestId;
        this._stlPending[id] = { resolve, reject };
        worker.postMessage({ id, buffer }, [buffer]);
      });
    },

    clearStlPane(role) {
      this.stlEpoch[role] += 1;
      this.stlGeometry[role] = null;
      this.stlRenderedFor[role] = null;
      this.stlLoading[role] = false;
      this.stlError[role] = '';
      const slot = document.getElementById(role === 'main' ? 'stl-main-canvas' : 'stl-sub-canvas');
      if (slot) slot.innerHTML = '';
    },

    async fetchStl(role) {
      const caseName = role === 'main' ? this.mainCase : this.subCase;
      const myEpoch = ++this.stlEpoch[role];
      this.stlGeometry[role] = null;
      this.stlError[role] = '';

      if (!caseName) {
        this.stlLoading[role] = false;
        if (this.stlEpoch[role] === myEpoch) this.clearStlPane(role);
        return;
      }

      this.stlLoading[role] = true;

      let buffer;
      try {
        const resp = await fetch(`/api/viewer/case/${encodeURIComponent(caseName)}/stl`, { credentials: 'same-origin' });
        if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
        buffer = await resp.arrayBuffer();
      } catch (e) {
        // Switching case again while this fetch was still in flight makes
        // this result stale — drop it rather than overwrite what the user
        // is now actually looking at.
        if (this.stlEpoch[role] !== myEpoch) return;
        this.stlLoading[role] = false;
        this.stlGeometry[role] = { error: e.message, caseName };
        this.stlError[role] = `Could not find q-criterion.stl (${e.message})`;
        return;
      }

      if (this.stlEpoch[role] !== myEpoch) return;

      let positions, normals;
      try {
        const parsed = await this._parseStlInWorker(buffer);
        positions = parsed.positions;
        normals = parsed.normals;
      } catch (e) {
        if (this.stlEpoch[role] !== myEpoch) return;
        this.stlLoading[role] = false;
        this.stlGeometry[role] = { error: e.message, caseName };
        this.stlError[role] = `Could not parse STL (${e.message})`;
        return;
      }

      if (this.stlEpoch[role] !== myEpoch) return;

      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
      geometry.center();
      geometry.computeBoundingSphere();

      if (this.stlEpoch[role] !== myEpoch) return;

      this.stlGeometry[role] = { geometry, caseName };
      this.stlLoading[role] = false;

      if (this.tab === '3d') this.renderStlFromCache(role);
    },

    renderStlFromCache(role) {
      const cached = this.stlGeometry[role];
      const slotId = role === 'main' ? 'stl-main-canvas' : 'stl-sub-canvas';
      const slot = document.getElementById(slotId);
      if (!slot || !cached || cached.error) return;

      slot.innerHTML = '';
      const epoch = this.stlEpoch[role];
      this.threeScenes[role] = { epoch };

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 10000);
      const renderer = new THREE.WebGLRenderer({ antialias: true });
      slot.appendChild(renderer.domElement);
      scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.7));

      const radius = cached.geometry.boundingSphere.radius || 1;
      const material = new THREE.MeshStandardMaterial({ color: 0x2f6fed, roughness: 0.55, metalness: 0.15 });
      const mesh = new THREE.Mesh(cached.geometry, material);
      scene.add(mesh);
      camera.position.z = radius * 3;

      // Track the container's real size continuously (instead of reading
      // it once) so the canvas always fills the pane — including the very
      // first layout pass and whenever Sub is toggled on/off and changes
      // Main's available width.
      const applySize = () => {
        const w = slot.clientWidth || 1;
        const h = slot.clientHeight || 1;
        renderer.setSize(w, h);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      };
      applySize();
      const resizeObserver = new ResizeObserver(applySize);
      resizeObserver.observe(slot);

      let dragging = false;
      let last = [0, 0];
      renderer.domElement.onpointerdown = (e) => { dragging = true; last = [e.clientX, e.clientY]; };
      window.addEventListener('pointerup', () => { dragging = false; });
      renderer.domElement.onpointermove = (e) => {
        if (!dragging) return;
        mesh.rotation.y += (e.clientX - last[0]) * 0.01;
        mesh.rotation.x += (e.clientY - last[1]) * 0.01;
        last = [e.clientX, e.clientY];
        this._syncCameraIfNeeded(role);
      };
      renderer.domElement.onwheel = (e) => {
        e.preventDefault();
        camera.position.z = Math.max(radius * 0.5, Math.min(radius * 8, camera.position.z + e.deltaY * 0.002 * radius));
        this._syncCameraIfNeeded(role);
      };

      this.threeScenes[role] = { epoch, scene, camera, renderer, mesh, radius, initialZ: radius * 3, resizeObserver };
      this.stlRenderedFor[role] = cached.caseName;

      const loop = () => {
        if (this.threeScenes[role]?.epoch !== epoch) {
          resizeObserver.disconnect();
          return;
        }
        requestAnimationFrame(loop);
        renderer.render(scene, camera);
      };
      loop();
    },

    // Full sync: copy the source pane's absolute rotation and (scaled)
    // camera distance onto the other pane, rather than applying the same
    // delta to both — deltas drift apart if the two panes ever start from
    // different states, a full copy never can.
    _syncCameraIfNeeded(role) {
      if (!this.syncCamera) return;
      const src = this.threeScenes[role];
      const dst = this.threeScenes[this._other(role)];
      if (!src || !src.mesh || !dst || !dst.mesh) return;
      dst.mesh.rotation.x = src.mesh.rotation.x;
      dst.mesh.rotation.y = src.mesh.rotation.y;
      const ratio = src.camera.position.z / (src.radius || 1);
      dst.camera.position.z = ratio * (dst.radius || 1);
    },

    resetStlView(role) {
      const s = this.threeScenes[role];
      if (!s || !s.mesh) return;
      s.mesh.rotation.set(0, 0, 0);
      s.camera.position.z = s.initialZ;
      if (this.syncCamera) this._syncCameraIfNeeded(role);
    },

    // ================================================================
    // Settings
    // ================================================================
    async loadSettings() {
      if (!this.user) return;
      const b = await this.api('/api/settings/case_parameter_default');
      this.defaultsText = JSON.stringify(b.content, null, 2);
      this.defaultsMtime = b.mtime;
      if (this.user.role === 'admin') await this.loadLoginLog();
    },

    async loadLoginLog() {
      try { this.loginLog = await this.api('/api/login-log'); }
      catch (e) { this.loginLog = []; }
    },

    async saveSettings() {
      this.error = '';
      try {
        const defaults = JSON.parse(this.defaultsText);
        await this.api('/api/settings/case_parameter_default', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: defaults, expected_mtime: this.defaultsMtime }),
        });
        this.flash('Settings saved');
        await this.loadSettings();
      } catch (e) {
        this.flash(e.message);
      }
    },
  };
}
