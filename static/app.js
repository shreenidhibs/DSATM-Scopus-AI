const $ = id => document.getElementById(id);

let facultyMeta = [];
let selectedFaculty = '';
let selectedLiveAuthorId = '';
let currentPublications = [];
let summaryRows = [];
let lastDashboard = null;
let currentDataSource = 'excel';

const esc = (s = '') =>
  String(s).replace(/[&<>'"]/g, c => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;'
  }[c]));

const initials = (n = '') =>
  n.trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map(x => x[0])
    .join('')
    .toUpperCase() || 'FC';

const toast = msg => {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');

  clearTimeout(window.__toast);

  window.__toast = setTimeout(
    () => t.classList.remove('show'),
    3000
  );
};



/* ==========================================================
   AUTO LOAD MASTER DATASET
========================================================== */

async function bootstrapMasterDataset() {
  try {
    const r = await fetch(`/api/bootstrap?_=${Date.now()}`, { cache: 'no-store' });
    const d = await r.json();

    if (!r.ok || !d.loaded) {
      return false;
    }

    facultyMeta = d.faculty_meta || [];
    $('facultyMeta').textContent = `${d.faculty_count || facultyMeta.length} faculty available`;

    if ($('institutionKeyword') && d.institution_keyword) {
      $('institutionKeyword').value = d.institution_keyword;
    }

    fillDepartments(d.departments || []);
    applyFacultyFilters();
    setMode('excel');

    if ($('refreshScopusExcelBtn')) {
      $('refreshScopusExcelBtn').disabled = false;
    }

    return true;
  } catch (e) {
    console.error('Master bootstrap failed:', e);
    return false;
  }
}

/* ==========================================================
   DATA SOURCE MODE
========================================================== */

function setMode(mode) {

  currentDataSource = mode;

  $('modeLiveBtn').classList.toggle(
    'active',
    mode === 'live'
  );

  $('modeExcelBtn').classList.toggle(
    'active',
    mode === 'excel'
  );

  $('liveSourcePanel').classList.toggle(
    'hidden',
    mode !== 'live'
  );

  $('excelSourcePanel').classList.toggle(
    'hidden',
    mode !== 'excel'
  );

  $('facultySearch').disabled =
    mode !== 'excel' || !facultyMeta.length;

  $('departmentFilter').disabled =
    mode !== 'excel' || !facultyMeta.length;

  if ($('searchBy')) {
    $('searchBy').disabled =
      mode !== 'excel' || !facultyMeta.length;
  }

  const summaryEnabled =
    mode === 'excel' &&
    facultyMeta.length > 0;

  $('summaryBtn').disabled =
    !summaryEnabled;

  if ($('sideSummaryBtn')) {
    $('sideSummaryBtn').disabled =
      !summaryEnabled;
  }

  if ($('homeSummaryBtn')) {
    $('homeSummaryBtn').disabled =
      !summaryEnabled;
  }

  const dashboardEnabled =
    mode === 'excel' &&
    facultyMeta.length > 0;

  if ($('openDashboardBtn')) {
    $('openDashboardBtn').disabled =
      !dashboardEnabled;
  }
}


$('modeLiveBtn').addEventListener(
  'click',
  () => setMode('live')
);

$('modeExcelBtn').addEventListener(
  'click',
  () => setMode('excel')
);


/* ==========================================================
   SCOPUS API STATUS
========================================================== */

async function checkScopusApi() {

  $('apiStatusDot').className =
    'api-dot checking';

  $('apiStatusText').textContent =
    'Checking Scopus API connection…';

  try {

    const r =
      await fetch('/api/scopus/test');

    const d =
      await r.json();

    if (!r.ok || !d.success) {
      throw new Error(
        d.detail ||
        d.error ||
        'Connection failed'
      );
    }

    $('apiStatusDot').className =
      'api-dot online';

    $('apiStatusText').textContent =
      'Scopus API connected';

  } catch (e) {

    $('apiStatusDot').className =
      'api-dot offline';

    $('apiStatusText').textContent =
      'Scopus API unavailable';

    toast(e.message);
  }
}


$('recheckApiBtn').addEventListener(
  'click',
  checkScopusApi
);

checkScopusApi();


/* ==========================================================
   LIVE SCOPUS SEARCH
========================================================== */

let liveSearchMode = 'name';

$('liveByNameBtn').addEventListener('click', () => setLiveSearchMode('name'));
$('liveByIdBtn').addEventListener('click', () => setLiveSearchMode('id'));
$('liveSearchBtn').addEventListener('click', searchLiveScopus);
$('liveAuthorId').addEventListener('keydown', e => { if (e.key === 'Enter') searchLiveScopus(); });
$('liveAuthorName').addEventListener('keydown', e => { if (e.key === 'Enter') searchLiveScopus(); });

function setLiveSearchMode(mode) {
  liveSearchMode = mode;
  $('liveByNameBtn').classList.toggle('active', mode === 'name');
  $('liveByIdBtn').classList.toggle('active', mode === 'id');
  $('liveNamePanel').classList.toggle('hidden', mode !== 'name');
  $('liveIdPanel').classList.toggle('hidden', mode !== 'id');
  $('liveAuthorMatches').classList.add('hidden');
  $('liveAuthorMatches').innerHTML = '';
  $('liveStatus').innerHTML = '<i></i><span>' + (mode === 'name' ? 'Enter faculty name to find matching Scopus authors' : 'Enter the numeric Scopus Author ID') + '</span>';
}

async function searchLiveScopus() {
  if (liveSearchMode === 'name') {
    return searchLiveScopusByName();
  }
  const authorId = $('liveAuthorId').value.trim().replace(/\s+/g, '');
  if (!/^\d+$/.test(authorId)) {
    toast('Enter a valid numeric Scopus Author ID.');
    return;
  }
  return loadLiveScopusAuthor(authorId);
}

function setAuthorDirectoryProgressVisible(visible) {
  const box = $('authorDirectoryProgress');
  if (!box) return;
  box.classList.toggle('hidden', !visible);
}

function renderAuthorDirectoryProgress(p) {
  setAuthorDirectoryProgressVisible(true);

  const percent = Math.max(0, Math.min(100, Number(p.percent || 0)));
  const current = Number(p.current || 0);
  const total = Number(p.total || 0);
  const authors = Number(p.authors_found || p.cached_authors || 0);

  $('authorDirectoryProgressPercent').textContent = `${percent}%`;
  $('authorDirectoryProgressBar').style.width = `${percent}%`;
  $('authorDirectoryProgressMessage').textContent =
    p.message || 'Building DSATM Author Directory...';

  $('authorDirectoryProgressMeta').textContent =
    `Publications: ${current} / ${total || '—'} • Authors found: ${authors}`;

  if (p.stage === 'complete') {
    $('authorDirectoryProgressTitle').textContent = 'DSATM Author Directory Ready';
  } else if (p.stage === 'error') {
    $('authorDirectoryProgressTitle').textContent = 'Author Directory Build Failed';
  } else {
    $('authorDirectoryProgressTitle').textContent = 'Building DSATM Author Directory...';
  }
}

async function waitForAuthorDirectoryBuild() {
  while (true) {
    const r = await fetch('/api/scopus/author-directory-progress');
    let p = {};

    try {
      p = await r.json();
    } catch (_) {
      throw new Error('Unable to read author-directory build progress.');
    }

    if (!r.ok) {
      throw new Error(p.detail || p.error || 'Unable to read author-directory build progress.');
    }

    renderAuthorDirectoryProgress(p);

    if (p.stage === 'error' || p.error) {
      throw new Error(p.error || p.message || 'Unable to build DSATM Author Directory.');
    }

    if (p.directory_ready && !p.running) {
      return p;
    }

    await new Promise(resolve => setTimeout(resolve, 1200));
  }
}

async function ensureAuthorDirectoryReady() {
  const statusResponse = await fetch('/api/scopus/author-directory-status');

  let status = {};

  try {
    status = await statusResponse.json();
  } catch (_) {
    throw new Error('Unable to check DSATM Author Directory.');
  }

  if (!statusResponse.ok) {
    throw new Error(
      status.detail ||
      status.error ||
      'Unable to check DSATM Author Directory.'
    );
  }

  if (!status.directory_ready) {
    throw new Error(
      'DSATM_Author_Directory.xlsx is missing from the project root. ' +
      'Copy the supplied author directory Excel beside app.py and restart the server.'
    );
  }

  setAuthorDirectoryProgressVisible(false);

  return status;
}

async function searchLiveScopusByName() {
  const name = $('liveAuthorName').value.trim().replace(/\s+/g, ' ');

  if (name.length < 2) {
    toast('Enter at least 2 characters of the faculty name.');
    return;
  }

  $('liveSearchBtn').disabled = true;
  $('liveSearchBtn').innerHTML =
    '<span>Checking faculty directory…</span><b>•••</b>';

  $('liveStatus').classList.remove('loaded');
  $('liveAuthorMatches').classList.add('hidden');
  $('liveAuthorMatches').innerHTML = '';

  try {
    await ensureAuthorDirectoryReady();

    $('liveSearchBtn').innerHTML =
      '<span>Searching faculty directory…</span><b>•••</b>';

    $('liveStatus').innerHTML =
      '<i></i><span>Searching DSATM Author Directory...</span>';

    const institution =
      $('institutionKeyword')?.value?.trim() ||
      'Dayananda Sagar Academy of Technology and Management';

    const r = await fetch(
      `/api/scopus/author-search?name=${encodeURIComponent(name)}&institution=${encodeURIComponent(institution)}`
    );

    let d = {};

    try {
      d = await r.json();
    } catch (_) {
      throw new Error('The server returned an invalid response.');
    }

    if (!r.ok || d.success === false) {
      throw new Error(
        d.detail ||
        d.error ||
        'Unable to search DSATM Author Directory.'
      );
    }

    const candidates = d.candidates || [];

    if (!candidates.length) {
      $('liveStatus').innerHTML =
        '<i></i><span>No matching DSATM author found. Try another spelling or use Scopus Author ID.</span>';
      return;
    }

    $('liveAuthorMatches').innerHTML = candidates.map(c => `
      <button type="button"
              class="live-author-card"
              data-author-id="${esc(c.author_id)}">
        <span>
          <strong>
            ${esc(c.name)}
            ${c.excel_match ? '<span class="excel-match-badge">DSATM directory</span>' : ''}
          </strong>
          <small>
            ${esc(c.affiliation || 'Dayananda Sagar Academy of Technology and Management')}
            ${c.city ? ' · ' + esc(c.city) : ''}
            ${c.country ? ' · ' + esc(c.country) : ''}
          </small>
        </span>
        <span class="author-id">ID ${esc(c.author_id)}</span>
      </button>
    `).join('');

    $('liveAuthorMatches').classList.remove('hidden');

    $('liveAuthorMatches')
      .querySelectorAll('.live-author-card')
      .forEach(btn => {
        btn.addEventListener(
          'click',
          () => loadLiveScopusAuthor(btn.dataset.authorId)
        );
      });

    $('liveStatus').innerHTML =
      `<i></i><span>${candidates.length} matching DSATM author${candidates.length === 1 ? '' : 's'} found. Select the correct profile.</span>`;

  } catch (e) {
    $('liveStatus').innerHTML =
      `<i></i><span>${esc(e.message)}</span>`;
    toast(e.message);
  } finally {
    $('liveSearchBtn').disabled = false;
    $('liveSearchBtn').innerHTML =
      '<span>Search Live Scopus</span><b>→</b>';
  }
}

async function loadLiveScopusAuthor(authorId) {
  $('liveSearchBtn').disabled = true;
  $('liveSearchBtn').innerHTML = '<span>Fetching live Scopus data…</span><b>•••</b>';
  $('liveStatus').classList.remove('loaded');
  $('liveStatus').innerHTML = '<i></i><span>Connecting to Elsevier Scopus…</span>';
  try {
    const r = await fetch(`/api/scopus/author/${encodeURIComponent(authorId)}`);
    const d = await r.json();
    if (!r.ok || d.success === false) throw new Error(d.detail || d.error || 'Unable to retrieve Scopus profile.');
    selectedLiveAuthorId = authorId;
    selectedFaculty = d.faculty_name || d.faculty || d.indexed_name || `Scopus Author ${authorId}`;
    d.faculty = selectedFaculty;
    d.scopus_author_id = d.scopus_author_id || d.author_id || authorId;
    d.department = d.department || d.affiliation || 'Affiliation not available';
    if (d.kpis && d.scopus_h_index !== undefined && d.scopus_h_index !== null && d.scopus_h_index !== '') d.kpis.h_index = d.scopus_h_index;
    lastDashboard = d;
    $('liveStatus').classList.add('loaded');
    const total = d.total_publications_scopus ?? d.total_publications ?? d.kpis?.publications ?? 0;
    const returned = d.returned_publications ?? d.publications?.length ?? 0;
    const note = d.truncated ? `Showing ${returned} of ${total} publications` : `${total} publications retrieved`;
    $('liveStatus').innerHTML = `<i></i><span><strong>Live Scopus connected</strong><br>${note}</span>`;
    $('liveAuthorMatches').classList.add('hidden');
    renderDashboard(d, 'live');
    toast('Live Scopus profile loaded successfully.');
  } catch (e) {
    $('liveStatus').innerHTML = `<i></i><span>${esc(e.message)}</span>`;
    toast(e.message);
  } finally {
    $('liveSearchBtn').disabled = false;
    $('liveSearchBtn').innerHTML = '<span>Search Live Scopus</span><b>→</b>';
  }
}

setLiveSearchMode('name');


/* ==========================================================
   EXCEL DRAG / DROP
========================================================== */

const drop =
  document.querySelector(
    '.home-file-box'
  );


if (drop) {

  [
    'dragenter',
    'dragover'
  ].forEach(ev =>

    drop.addEventListener(
      ev,
      e => {

        e.preventDefault();

        drop.classList.add(
          'dragover'
        );
      }
    )
  );


  [
    'dragleave',
    'drop'
  ].forEach(ev =>

    drop.addEventListener(
      ev,
      e => {

        e.preventDefault();

        drop.classList.remove(
          'dragover'
        );
      }
    )
  );


  drop.addEventListener(
    'drop',
    e => {

      const f =
        e.dataTransfer
          .files?.[0];

      if (f) {

        const dt =
          new DataTransfer();

        dt.items.add(f);

        $('excelFile').files =
          dt.files;

        setSelectedFile(f);
      }
    }
  );
}


$('excelFile').addEventListener(
  'change',
  e => {

    const f =
      e.target.files[0];

    if (f) {
      setSelectedFile(f);
    }
  }
);


function setSelectedFile(f) {

  $('uploadStatus')
    .classList
    .remove('loaded');


  $('uploadStatus').innerHTML =
    `<i></i>
         <span>
            Selected: ${esc(f.name)}
         </span>`;


  if ($('fileLabel')) {

    $('fileLabel').textContent =
      f.name;
  }
}


/* ==========================================================
   EXCEL UPLOAD
========================================================== */

$('uploadBtn').addEventListener(
  'click',
  async () => {

    const file =
      $('excelFile')
        .files[0];


    if (!file) {

      toast(
        'Choose a Scopus Excel file first.'
      );

      return;
    }


    const fd =
      new FormData();


    fd.append(
      'file',
      file
    );


    fd.append(
      'institution_keyword',
      $('institutionKeyword')
        .value
        .trim()
    );


    $('uploadBtn').disabled =
      true;


    $('uploadBtn').innerHTML =
      '<span>Analyzing dataset…</span><b>•••</b>';


    try {

      const r =
        await fetch(
          '/api/upload',
          {
            method: 'POST',
            body: fd
          }
        );


      const d =
        await r.json();


      if (!r.ok) {

        throw new Error(
          d.detail ||
          'Unable to process Excel file.'
        );
      }


      facultyMeta =
        d.faculty_meta ||
        d.faculty.map(
          f => ({
            faculty: f,
            department:
              'Other / Not Detected'
          })
        );


      $('uploadStatus')
        .classList
        .add('loaded');


      $('uploadStatus').innerHTML =
        `<i></i>
                 <span>
                    <strong>
                        ${esc(d.filename)}
                    </strong>
                    <br>
                    ${d.rows} records ·
                    ${d.faculty_count}
                    faculty detected
                 </span>`;


      $('facultySearch').disabled =
        false;

      if ($('refreshScopusExcelBtn')) {
        $('refreshScopusExcelBtn').disabled = false;
      }

      $('departmentFilter').disabled =
        false;


      if ($('searchBy')) {

        $('searchBy').disabled =
          false;
      }


      $('summaryBtn').disabled =
        false;


      if ($('sideSummaryBtn')) {

        $('sideSummaryBtn').disabled =
          false;
      }


      if ($('openDashboardBtn')) {

        $('openDashboardBtn').disabled =
          false;
      }


      if ($('homeSummaryBtn')) {

        $('homeSummaryBtn').disabled =
          false;
      }


      $('facultyMeta').textContent =
        `${d.faculty_count} faculty available`;


      fillDepartments(
        d.departments || []
      );


      applyFacultyFilters();


      toast(
        'Excel dataset processed successfully. Select a faculty member.'
      );


    } catch (e) {

      toast(
        e.message
      );


      $('uploadStatus').innerHTML =
        `<i></i>
                 <span>
                    ${esc(e.message)}
                 </span>`;


    } finally {

      $('uploadBtn').disabled =
        false;


      $('uploadBtn').innerHTML =
        '<span>Analyze Excel Data</span><b>→</b>';
    }
  }
);


/* ==========================================================
   REFRESH MASTER EXCEL THROUGH GITHUB ACTIONS
========================================================== */

async function initializeRefreshButton() {
  const btn = $('refreshScopusExcelBtn');
  if (!btn) return;
  btn.disabled = false;
  btn.title = 'Refresh DSATM Scopus master dataset';
}

function refreshStageLabel(stage) {
  const labels = {
    queued: 'Starting',
    updating_scopus: 'Updating Scopus',
    updating_excel: 'Updating Excel',
    deploying: 'Deploying',
    complete: 'Updated successfully',
    error: 'Refresh failed'
  };
  return labels[stage] || 'Updating';
}

function renderRefreshProgress(stage, percent, message) {
  const steps = [
    ['updating_scopus', 'Updating Scopus'],
    ['updating_excel', 'Updating Excel'],
    ['deploying', 'Deploying'],
    ['complete', 'Updated successfully']
  ];

  const order = {
    queued: 0,
    updating_scopus: 1,
    updating_excel: 2,
    deploying: 3,
    complete: 4,
    error: -1
  };

  const current = order[stage] ?? 0;
  const items = steps.map((item, index) => {
    const done = stage === 'complete' || current > index + 1;
    const active = current === index + 1;
    const cls = done ? 'done' : (active ? 'active' : 'pending');
    const icon = done ? '✓' : (active ? '●' : '○');
    return `<span class="scopus-refresh-step ${cls}"><b>${icon}</b>${esc(item[1])}</span>`;
  }).join('<span class="scopus-refresh-arrow">→</span>');

  $('uploadStatus').classList.toggle('loaded', stage === 'complete');
  $('uploadStatus').innerHTML = `
    <div class="scopus-refresh-progress">
      <div class="scopus-refresh-title">
        <strong>${esc(refreshStageLabel(stage))}</strong>
        <span>${Math.max(0, Math.min(100, Number(percent || 0)))}%</span>
      </div>
      <div class="scopus-refresh-track">
        <div class="scopus-refresh-bar" style="width:${Math.max(0, Math.min(100, Number(percent || 0)))}%"></div>
      </div>
      <div class="scopus-refresh-steps">${items}</div>
      <div class="scopus-refresh-message">${esc(message || '')}</div>
    </div>`;
}

async function monitorScopusRefresh(trigger) {
  const startedAt = trigger.triggered_at || '';
  const beforeSha = trigger.before_sha || '';
  const beforeHash = trigger.master_hash_before || '';
  let transientErrors = 0;

  for (let attempt = 0; attempt < 120; attempt += 1) {
    await new Promise(resolve => setTimeout(resolve, 4000));

    const params = new URLSearchParams({
      triggered_at: startedAt,
      before_sha: beforeSha,
      master_hash_before: beforeHash
    });

    try {
      const r = await fetch(`/api/scopus/refresh-status?${params.toString()}&_=${Date.now()}`, {
        cache: 'no-store'
      });
      const d = await r.json();

      if (!r.ok) {
        throw new Error(d.detail || d.error || 'Unable to read refresh status.');
      }

      transientErrors = 0;

      if (d.stage === 'error' || d.success === false) {
        renderRefreshProgress('error', 100, d.message || 'Scopus refresh failed.');
        throw new Error(d.message || 'Scopus refresh failed.');
      }

      renderRefreshProgress(d.stage || 'updating_scopus', d.percent || 0, d.message || 'Refreshing…');

      if (d.ready) {
        await new Promise(resolve => setTimeout(resolve, 1500));
        await bootstrapMasterDataset();
        setMode('excel');
        await loadSummary('');
        toast(d.no_changes ? 'Refresh complete. No new Scopus changes.' : 'Latest Institution Summary loaded successfully.');
        return d;
      }
    } catch (e) {
      transientErrors += 1;
      // A production deployment can briefly interrupt polling while Vercel
      // switches traffic. Keep waiting through short network errors.
      if (transientErrors >= 8) {
        throw e;
      }
    }
  }

  throw new Error('Refresh is still running. Please check GitHub Actions and reload the page shortly.');
}

initializeRefreshButton();

if ($('refreshScopusExcelBtn')) {
  $('refreshScopusExcelBtn').addEventListener('click', async () => {
    const btn = $('refreshScopusExcelBtn');
    btn.disabled = true;
    btn.innerHTML = '<span>Refreshing Scopus…</span><b>•••</b>';
    renderRefreshProgress('queued', 5, 'Starting the DSATM Scopus refresh in GitHub Actions…');

    try {
      const r = await fetch('/api/scopus/trigger-refresh', { method: 'POST' });
      let d = {};
      try { d = await r.json(); } catch (_) {}

      if (!r.ok) {
        throw new Error(d.detail || d.error || 'Unable to start the GitHub Scopus refresh.');
      }

      toast('Scopus refresh started.');
      await monitorScopusRefresh(d);
    } catch (e) {
      renderRefreshProgress('error', 100, e.message || 'Unable to refresh Scopus.');
      toast(e.message || 'Unable to refresh Scopus.');
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<span>Refresh Excel from Live Scopus</span><b>↻</b>';
    }
  });
}


/* ==========================================================
   DEPARTMENT FILTER
========================================================== */

function fillDepartments(depts) {

  const opts =
    '<option value="">All Departments</option>' +

    depts.map(
      d =>
        `<option value="${esc(d)}">
                    ${esc(d)}
                 </option>`
    ).join('');


  $('departmentFilter').innerHTML =
    opts;


  $('summaryDepartment').innerHTML =
    opts;
}


$('facultySearch').addEventListener(
  'input',
  applyFacultyFilters
);


$('departmentFilter').addEventListener(
  'change',
  applyFacultyFilters
);


if ($('searchBy')) {

  $('searchBy').addEventListener(
    'change',
    () => {

      $('facultySearch').value =
        '';


      $('facultySearch').placeholder =
        $('searchBy').value ===
          'scopus_id'
          ? 'Enter Scopus Author ID…'
          : 'Enter faculty name…';


      applyFacultyFilters();
    }
  );
}


/* ==========================================================
   FACULTY FILTER
========================================================== */

function applyFacultyFilters() {

  const q =
    $('facultySearch')
      .value
      .trim()
      .toLowerCase();


  const dep =
    $('departmentFilter')
      .value;


  const mode =
    $('searchBy')?.value ||
    'faculty';


  const rows =
    facultyMeta.filter(
      x => {

        if (
          dep &&
          x.department !== dep
        ) {

          return false;
        }


        if (!q) {

          return true;
        }


        if (
          mode ===
          'scopus_id'
        ) {

          return String(
            x.scopus_author_id || ''
          )
            .toLowerCase()
            .includes(q);
        }


        return String(
          x.faculty || ''
        )
          .toLowerCase()
          .includes(q);
      }
    );


  const label =
    mode === 'scopus_id'
      ? 'Scopus ID'
      : 'faculty';


  $('facultyMeta').textContent =
    q
      ? `${rows.length} match${rows.length === 1 ? '' : 'es'} by ${label}`
      : `${rows.length} faculty shown`;


  renderFacultyList(rows);
}


/* ==========================================================
   FACULTY LIST
========================================================== */

function renderFacultyList(rows) {

  $('facultyList').innerHTML =

    rows
      .slice(0, 300)
      .map(x => {

        const sid =
          x.scopus_author_id &&
            x.scopus_author_id !==
            'Not available in export'

            ? x.scopus_author_id
            : 'ID not available';


        return `
                    <div
                        class="faculty-item ${x.faculty ===
            selectedFaculty
            ? 'active'
            : ''
          }"
                        data-name="${esc(x.faculty)}"
                    >

                        <div class="faculty-list-name">
                            ${esc(x.faculty)}
                        </div>

                        <div class="faculty-list-id">
                            Scopus ID:
                            ${esc(sid)}
                        </div>

                    </div>
                `;
      })
      .join('')

    ||

    '<div class="faculty-meta">No faculty matches found</div>';


  document
    .querySelectorAll(
      '.faculty-item'
    )
    .forEach(
      el =>

        el.onclick =
        () =>
          loadFaculty(
            el.dataset.name
          )
    );
}


/* ==========================================================
   LOAD EXCEL FACULTY
========================================================== */

async function loadFaculty(name) {

  selectedFaculty =
    name;

  selectedLiveAuthorId =
    '';


  applyFacultyFilters();


  const r =
    await fetch(
      `/api/dashboard?faculty=${encodeURIComponent(name)}`
    );


  const d =
    await r.json();


  if (!r.ok) {

    toast(
      d.detail ||
      'Unable to load faculty profile.'
    );

    return;
  }


  lastDashboard =
    d;


  renderDashboard(
    d,
    'excel'
  );
}


/* ==========================================================
   MAIN DASHBOARD
========================================================== */

function renderDashboard(d, source) {

  currentDataSource =
    source;


  selectedFaculty =
    d.faculty_name ||
    d.faculty ||
    d.indexed_name ||
    selectedFaculty;


  $('welcome')
    .classList
    .add('hidden');


  $('summaryView')
    .classList
    .add('hidden');


  $('dashboard')
    .classList
    .remove('hidden');


  /* --------------------------------------------------
     DISPLAY REAL FACULTY NAME
  -------------------------------------------------- */

  const displayName =
    d.faculty_name ||
    d.faculty ||
    d.indexed_name ||
    selectedFaculty ||
    `Scopus Author ${d.author_id || ''}`;


  $('pageTitle').textContent =
    displayName;


  $('facultyAvatar').textContent =
    initials(displayName);


  $('facultyAvatar')
    .classList
    .remove('has-photo');


  $('facultyAvatar').style.backgroundImage =
    '';


  /* --------------------------------------------------
     AFFILIATION / DEPARTMENT
  -------------------------------------------------- */

  $('departmentTag').textContent =
    d.affiliation ||
    d.department ||
    'Affiliation / Department not detected';


  /* --------------------------------------------------
     SCOPUS ID
  -------------------------------------------------- */

  $('scopusIdTag').textContent =
    `Scopus ID: ${d.scopus_author_id ||
    d.author_id ||
    'Not available'
    }`;


  /* --------------------------------------------------
     DATA SOURCE
  -------------------------------------------------- */

  $('dataSourceTag').textContent =
    source === 'live'
      ? '● LIVE SCOPUS'
      : 'EXCEL DATASET';


  $('dataSourceTag')
    .classList
    .toggle(
      'live-source-tag',
      source === 'live'
    );


  $('pageSubtitle').textContent =
    source === 'live'

      ? 'Current publication and citation metadata retrieved through the Elsevier Scopus API.'

      : 'Scopus-indexed publication profile from the uploaded institutional Excel dataset.';


  $('exportBtn').disabled =
    false;


  $('reportBtn').disabled =
    false;


  const canSummary =
    source === 'excel' &&
    facultyMeta.length > 0;


  $('summaryBtn').disabled =
    !canSummary;


  if ($('sideSummaryBtn')) {

    $('sideSummaryBtn').disabled =
      !canSummary;
  }


  if ($('homeSummaryBtn')) {

    $('homeSummaryBtn').disabled =
      !canSummary;
  }


  /* ==================================================
     KPI DATA
  ================================================== */

  if (!d.kpis) {

    d.kpis = {
      publications:
        d.total_publications ||
        d.publications?.length ||
        0,

      citations:
        d.total_citations_for_returned_records ||
        0,

      h_index:
        d.scopus_h_index ||
        0,

      coauthors:
        0,

      latest_year:
        '',

      latest_year_publications:
        0,

      unique_sources:
        0
    };
  }


  /* Prefer actual Scopus h-index */

  if (
    source === 'live' &&
    d.scopus_h_index !== undefined &&
    d.scopus_h_index !== null &&
    d.scopus_h_index !== ''
  ) {

    d.kpis.h_index =
      d.scopus_h_index;
  }


  $('kpiPublications').textContent =
    d.kpis.publications ?? 0;


  $('heroRecordCount').textContent =
    d.kpis.publications ?? 0;


  $('kpiCitations').textContent =
    Number(
      d.kpis.citations || 0
    ).toLocaleString();


  $('kpiHIndex').textContent =
    d.kpis.h_index ?? 0;


  $('kpiCoauthors').textContent =
    d.kpis.coauthors ?? 0;


  $('kpiYear').textContent =
    d.kpis.latest_year || '—';


  $('kpiYearCount').textContent =
    `${d.kpis.latest_year_publications || 0
    } publication${d.kpis.latest_year_publications === 1
      ? ''
      : 's'
    }`;


  $('kpiSources').textContent =
    d.kpis.unique_sources ?? 0;


  /* --------------------------------------------------
     KPI CAPTIONS
  -------------------------------------------------- */

  document
    .querySelector(
      '.kpi-purple small'
    )
    .textContent =

    source === 'live'

      ? 'Citations in retrieved live records'

      : 'Dataset citation impact';


  document
    .querySelector(
      '.kpi-green small'
    )
    .textContent =

    source === 'live' &&
      d.scopus_h_index !== undefined &&
      d.scopus_h_index !== null &&
      d.scopus_h_index !== ''

      ? 'Scopus h-index from author profile'

      : source === 'live'

        ? 'Calculated from live records'

        : 'From uploaded records';


  /* ==================================================
     YEAR PERIOD
  ================================================== */

  const years =
    Object.keys(
      d.by_year || {}
    )
      .filter(
        y =>
          /^\d{4}$/.test(y)
      );


  $('profilePeriod').textContent =
    years.length

      ? `${years[0]} – ${years[years.length - 1]}`

      : 'Publication analytics';


  /* ==================================================
     CHARTS
  ================================================== */

  drawYearChart(
    d.by_year || {}
  );


  drawSourceBars(
    d.top_sources || {}
  );


  drawMix(
    d.source_types || {}
  );


  currentPublications =
    d.publications || [];


  buildYearFilter(
    currentPublications
  );


  renderPublications();


  window.scrollTo({
    top: 0,
    behavior: 'smooth'
  });
}


/* ==========================================================
   FACULTY PHOTO
========================================================== */

$('facultyPhoto').addEventListener(
  'change',
  e => {

    const f =
      e.target.files?.[0];

    if (!f) {
      return;
    }


    const reader =
      new FileReader();


    reader.onload =
      () => {

        const a =
          $('facultyAvatar');


        a.style.backgroundImage =
          `url(${reader.result})`;


        a.classList.add(
          'has-photo'
        );
      };


    reader.readAsDataURL(f);
  }
);


/* ==========================================================
   REPORT
========================================================== */

$('reportBtn').addEventListener(
  'click',
  () => {

    if (!lastDashboard) {
      return;
    }


    toast(
      'Opening print-ready faculty research report…'
    );


    setTimeout(
      () => window.print(),
      250
    );
  }
);


/* ==========================================================
   YEAR CHART
========================================================== */

function drawYearChart(data) {

  const canvas =
    $('yearChart');

  const ctx =
    canvas.getContext('2d');

  const ratio =
    window.devicePixelRatio || 1;

  const cssW =
    canvas.clientWidth || 760;

  const cssH =
    285;


  canvas.width =
    cssW * ratio;

  canvas.height =
    cssH * ratio;


  ctx.setTransform(
    ratio,
    0,
    0,
    ratio,
    0,
    0
  );


  ctx.clearRect(
    0,
    0,
    cssW,
    cssH
  );


  const entries =
    Object.entries(data)
      .filter(
        ([k]) =>
          k !== 'Unknown'
      );


  if (!entries.length) {

    ctx.fillStyle =
      '#8e99aa';

    ctx.font =
      '11px system-ui';

    ctx.fillText(
      'No year data available',
      18,
      40
    );

    return;
  }


  const pad = {
    l: 36,
    r: 12,
    t: 28,
    b: 35
  };


  const w =
    cssW -
    pad.l -
    pad.r;


  const h =
    cssH -
    pad.t -
    pad.b;


  const max =
    Math.max(
      ...entries.map(
        x => x[1]
      ),
      1
    );


  ctx.font =
    '9px system-ui';

  ctx.textAlign =
    'right';


  for (
    let i = 0;
    i <= 4;
    i++
  ) {

    const y =
      pad.t +
      h -
      (h * i / 4);


    ctx.strokeStyle =
      '#edf0f5';


    ctx.beginPath();

    ctx.moveTo(
      pad.l,
      y
    );

    ctx.lineTo(
      pad.l + w,
      y
    );

    ctx.stroke();


    ctx.fillStyle =
      '#9aa5b6';


    ctx.fillText(
      Math.round(
        max * i / 4
      ),
      pad.l - 9,
      y + 3
    );
  }


  ctx.textAlign =
    'center';


  const step =
    w / entries.length;


  const bw =
    Math.min(
      34,
      step * .52
    );


  entries.forEach(
    ([year, count], i) => {

      const x =
        pad.l +
        i * step +
        (step - bw) / 2;


      const bh =
        Math.max(
          4,
          h *
          (count / max)
        );


      const y =
        pad.t +
        h -
        bh;


      const grad =
        ctx.createLinearGradient(
          0,
          y,
          0,
          pad.t + h
        );


      grad.addColorStop(
        0,
        '#2f74dc'
      );


      grad.addColorStop(
        1,
        '#7357d9'
      );


      ctx.fillStyle =
        grad;


      roundRect(
        ctx,
        x,
        y,
        bw,
        bh,
        5
      );


      ctx.fill();


      ctx.fillStyle =
        '#738197';


      ctx.font =
        '9px system-ui';


      ctx.fillText(
        year,
        x + bw / 2,
        pad.t + h + 19
      );


      ctx.fillStyle =
        '#34455f';


      ctx.font =
        'bold 9px system-ui';


      ctx.fillText(
        count,
        x + bw / 2,
        y - 7
      );
    }
  );


  ctx.textAlign =
    'left';
}


/* ==========================================================
   ROUND RECT
========================================================== */

function roundRect(
  ctx,
  x,
  y,
  w,
  h,
  r
) {

  r =
    Math.min(
      r,
      w / 2,
      h / 2
    );


  ctx.beginPath();

  ctx.moveTo(
    x + r,
    y
  );

  ctx.arcTo(
    x + w,
    y,
    x + w,
    y + h,
    r
  );

  ctx.arcTo(
    x + w,
    y + h,
    x,
    y + h,
    r
  );

  ctx.arcTo(
    x,
    y + h,
    x,
    y,
    r
  );

  ctx.arcTo(
    x,
    y,
    x + w,
    y,
    r
  );

  ctx.closePath();
}


/* ==========================================================
   SOURCE BARS
========================================================== */

function drawSourceBars(data) {

  const vals =
    Object.values(data);


  const max =
    Math.max(
      ...vals,
      1
    );


  $('sourceBars').innerHTML =

    Object.entries(data)
      .map(
        ([s, c]) =>

          `<div class="source-row">

                        <div>

                            <div
                                class="source-label"
                                title="${esc(s)}"
                            >
                                ${esc(s)}
                            </div>

                            <div class="bar-track">

                                <div
                                    class="bar-fill"
                                    style="width:${Math.max(
            5,
            c / max * 100
          )
          }%"
                                ></div>

                            </div>

                        </div>

                        <div class="bar-count">
                            ${c}
                        </div>

                    </div>`
      )
      .join('')

    ||

    '<p class="faculty-meta">No source data available.</p>';
}


/* ==========================================================
   JOURNAL / CONFERENCE MIX
========================================================== */

function drawMix(data) {

  const j =
    data.Journal || 0;

  const c =
    data.Conference || 0;

  const o =
    data.Other || 0;


  const total =
    j + c + o || 1;


  const p1 =
    j / total * 100;


  const p2 =
    (j + c) /
    total *
    100;


  $('mixTotal').textContent =
    j + c + o;


  document
    .querySelector(
      '.donut'
    )
    .style.background =

    `conic-gradient(
            #2f74dc 0 ${p1}%,
            #e2bb39 ${p1}% ${p2}%,
            #dfe6ef ${p2}% 100%
        )`;


  $('mixLegend').innerHTML =

    `<div>

            <span>
                <i style="background:#2f74dc"></i>
                Journal
            </span>

            <strong>
                ${j}
            </strong>

        </div>


        <div>

            <span>
                <i style="background:#e2bb39"></i>
                Conference
            </span>

            <strong>
                ${c}
            </strong>

        </div>


        <div>

            <span>
                <i style="background:#dfe6ef"></i>
                Other
            </span>

            <strong>
                ${o}
            </strong>

        </div>`;
}


/* ==========================================================
   PUBLICATION FILTER
========================================================== */

function buildYearFilter(rows) {

  const years =
    [
      ...new Set(
        rows
          .map(
            r => r.year
          )
          .filter(Boolean)
      )
    ]
      .sort()
      .reverse();


  $('yearFilter').innerHTML =

    '<option value="">All years</option>' +

    years.map(
      y =>
        `<option>
                    ${esc(y)}
                 </option>`
    )
      .join('');
}


$('publicationSearch').addEventListener(
  'input',
  renderPublications
);


$('yearFilter').addEventListener(
  'change',
  renderPublications
);


/* ==========================================================
   PUBLICATION TABLE
========================================================== */

function renderPublications() {

  const q =
    $('publicationSearch')
      .value
      .toLowerCase();


  const y =
    $('yearFilter')
      .value;


  const rows =
    currentPublications.filter(
      r =>

        (!y || r.year === y)

        &&

        (
          !q ||

          `${r.title}
                     ${r.source}
                     ${r.doi}
                     ${r.authors}`

            .toLowerCase()

            .includes(q)
        )
    );


  $('resultCount').textContent =
    `Showing ${rows.length} of ${currentPublications.length} retrieved publications`;


  $('publicationBody').innerHTML =

    rows.map(
      (r, i) =>

        `<tr>

                    <td>

                        <div class="pub-index">
                            ${i + 1}
                        </div>

                    </td>


                    <td>

                        <div class="pub-title">
                            ${esc(r.title)}
                        </div>

                        <div
                            class="pub-authors"
                            title="${esc(r.authors)}"
                        >
                            ${esc(
          r.authors ||
          'Author details unavailable'
        )
        }
                        </div>

                    </td>


                    <td>

                        <span class="year-pill">
                            ${esc(r.year || '—')}
                        </span>

                    </td>


                    <td>
                        ${esc(r.source || '—')}
                    </td>


                    <td>

                        <span class="citation-pill">
                            ${r.citations ?? 0}
                        </span>

                    </td>


                    <td>

                        <span
                            class="type-pill"
                            title="${esc(
          r.document_type ||
          'Unspecified'
        )
        }"
                        >
                            ${esc(
          r.document_type ||
          'Unspecified'
        )
        }
                        </span>

                    </td>


                    <td>

                        ${r.link

          ?

          `<a
                                class="doi-link"
                                target="_blank"
                                rel="noopener"
                                href="${esc(r.link)}"
                             >
                                Open ↗
                             </a>`

          :

          esc(r.doi || '—')
        }

                    </td>

                </tr>`
    )
      .join('')

    ||

    '<tr><td class="empty-row" colspan="7">No publications match the current filters.</td></tr>';
}


/* ==========================================================
   EXPORT
========================================================== */

$('exportBtn').addEventListener(
  'click',
  () => {

    if (
      currentDataSource === 'live' &&
      selectedLiveAuthorId
    ) {

      location.href =
        `/api/scopus/export/${encodeURIComponent(selectedLiveAuthorId)}`;

    } else if (
      selectedFaculty
    ) {

      location.href =
        `/api/export?faculty=${encodeURIComponent(selectedFaculty)}`;
    }
  }
);


/* ==========================================================
   SUMMARY
========================================================== */

$('summaryBtn').addEventListener(
  'click',
  () => {

    if (
      currentDataSource ===
      'live'
    ) {

      toast(
        'Institution Summary uses the institutional Excel dataset. Switch to Excel Data mode.'
      );

      return;
    }


    loadSummary('');
  }
);


$('summaryDepartment').addEventListener(
  'change',
  () =>
    loadSummary(
      $('summaryDepartment').value
    )
);


/* ==========================================================
   LOAD SUMMARY
========================================================== */

async function loadSummary(
  dept = ''
) {

  if (
    currentDataSource ===
    'live'
  ) {

    toast(
      'Institution Summary is available in Excel Data mode.'
    );

    return;
  }


  const buttons =
    [
      $('summaryBtn'),
      $('sideSummaryBtn'),
      $('homeSummaryBtn')
    ]
      .filter(Boolean);


  buttons.forEach(
    b =>
      b.disabled = true
  );


  toast(
    'Loading institution summary…'
  );


  try {

    const r =
      await fetch(
        `/api/summary${dept
          ?
          `?department=${encodeURIComponent(dept)}`
          :
          ''
        }`
      );


    const d =
      await r.json();


    if (!r.ok) {

      toast(
        d.detail ||
        'Unable to load institution summary.'
      );

      return;
    }


    summaryRows =
      d.rows || [];


    $('dashboard')
      .classList
      .add('hidden');


    $('welcome')
      .classList
      .add('hidden');


    $('summaryView')
      .classList
      .remove('hidden');


    $('sumFaculty').textContent =
      d.total_faculty ?? 0;


    $('sumRecords').textContent =
      d.total_records ?? 0;


    $('sumCitations').textContent =
      (
        d.total_citations ?? 0
      )
        .toLocaleString();


    $('sumJournals').textContent =
      d.journal_count ?? 0;


    $('sumConferences').textContent =
      d.conference_count ?? 0;


    $('sumDepartments').textContent =
      d.department_count ?? 0;


    $('sumSources').textContent =
      d.unique_sources ?? 0;


    $('sumLatestYear').textContent =
      d.latest_year ?? '—';


    $('sumLeader').textContent =
      d.top_faculty ||
      d.rows?.[0]?.faculty ||
      '—';


    renderOverviewBars(
      'summaryDeptBars',
      d.department_faculty_counts || {},
      8
    );


    renderOverviewBars(
      'summaryYearBars',
      d.by_year || {},
      10,
      true
    );


    renderSummary(
      summaryRows
    );


    window.scrollTo({
      top: 0,
      behavior: 'smooth'
    });


    toast(
      'Institution summary loaded.'
    );


  } catch (e) {

    toast(
      'Unable to load institution summary: ' +
      e.message
    );


  } finally {

    buttons.forEach(
      b =>
        b.disabled = false
    );
  }
}


/* ==========================================================
   SUMMARY BARS
========================================================== */

function renderOverviewBars(
  id,
  data,
  limit = 8,
  chronological = false
) {

  const box =
    $(id);


  if (!box) {
    return;
  }


  let entries =
    Object.entries(data)
      .filter(
        ([k]) =>
          k &&
          k !== 'Unknown'
      );


  if (chronological) {

    entries =
      entries
        .sort(
          (a, b) =>
            String(a[0])
              .localeCompare(
                String(b[0])
              )
        )
        .slice(-limit);

  } else {

    entries =
      entries
        .sort(
          (a, b) =>
            b[1] - a[1]
        )
        .slice(
          0,
          limit
        );
  }


  const max =
    Math.max(
      ...entries.map(
        x =>
          Number(x[1]) ||
          0
      ),
      1
    );


  box.innerHTML =

    entries.map(
      ([label, value]) =>

        `<div class="overview-bar-row">

                    <div class="overview-bar-label">

                        <span
                            title="${esc(label)}"
                        >
                            ${esc(label)}
                        </span>

                        <b>
                            ${value}
                        </b>

                    </div>

                    <div class="overview-bar-track">

                        <i
                            style="width:${Math.max(
          4,
          (Number(value) || 0) /
          max *
          100
        )
        }%"
                        ></i>

                    </div>

                </div>`
    )
      .join('')

    ||

    '<p class="faculty-meta">No data available.</p>';
}


/* ==========================================================
   SUMMARY TABLE
========================================================== */

function renderSummary(rows) {

  $('summaryBody').innerHTML =

    rows.map(
      (r, i) =>

        `<tr>

                    <td>

                        <div class="rank-badge">
                            ${i + 1}
                        </div>

                    </td>


                    <td>

                        <div class="faculty-cell">

                            <div class="small-avatar">
                                ${esc(initials(r.faculty))}
                            </div>

                            ${esc(r.faculty)}

                        </div>

                    </td>


                    <td>
                        ${esc(r.department || '—')}
                    </td>


                    <td>
                        <strong>
                            ${r.publications}
                        </strong>
                    </td>


                    <td>
                        ${r.citations}
                    </td>


                    <td>

                        <button
                            class="summary-open"
                            data-name="${esc(r.faculty)}"
                        >
                            View Profile →
                        </button>

                    </td>

                </tr>`
    )
      .join('')

    ||

    '<tr><td colspan="6" class="empty-row">No faculty records found.</td></tr>';


  document
    .querySelectorAll(
      '.summary-open'
    )
    .forEach(
      b =>

        b.onclick =
        () =>
          loadFaculty(
            b.dataset.name
          )
    );
}


/* ==========================================================
   SUMMARY SEARCH
========================================================== */

$('summarySearch').addEventListener(
  'input',
  e => {

    const q =
      e.target
        .value
        .toLowerCase();


    renderSummary(

      summaryRows.filter(
        r =>
          r.faculty
            .toLowerCase()
            .includes(q)

          ||

          r.department
            .toLowerCase()
            .includes(q)
      )
    );
  }
);


/* ==========================================================
   BACK TO DASHBOARD
========================================================== */

$('backDashboard').addEventListener(
  'click',
  () => {

    if (lastDashboard) {

      renderDashboard(
        lastDashboard,
        currentDataSource
      );

    } else {

      $('summaryView')
        .classList
        .add('hidden');


      $('welcome')
        .classList
        .remove('hidden');
    }
  }
);


/* ==========================================================
   RESIZE
========================================================== */

window.addEventListener(
  'resize',
  () => {

    if (
      lastDashboard &&
      !$('dashboard')
        .classList
        .contains('hidden')
    ) {

      drawYearChart(
        lastDashboard.by_year || {}
      );
    }
  }
);


/* ==========================================================
   HOME BUTTONS
========================================================== */

if ($('openDashboardBtn')) {

  $('openDashboardBtn')
    .addEventListener(
      'click',
      () => {

        if (lastDashboard) {

          renderDashboard(
            lastDashboard,
            currentDataSource
          );

          return;
        }


        if (facultyMeta.length) {

          loadFaculty(
            facultyMeta[0].faculty
          );

          return;
        }


        toast(
          'Search Live Scopus or upload an Excel dataset first.'
        );
      }
    );
}


if ($('homeSummaryBtn')) {

  $('homeSummaryBtn')
    .addEventListener(
      'click',
      () => {

        if (
          currentDataSource !==
          'excel'

          ||

          !facultyMeta.length
        ) {

          toast(
            'Upload an institutional Excel dataset first.'
          );

          return;
        }


        loadSummary('');
      }
    );
}


if ($('sideSummaryBtn')) {

  $('sideSummaryBtn')
    .addEventListener(
      'click',
      () => {

        if (
          currentDataSource !==
          'excel'

          ||

          !facultyMeta.length
        ) {

          toast(
            'Institution Summary is available for Excel Data mode.'
          );

          return;
        }


        loadSummary('');
      }
    );
}


if ($('homeHelpBtn')) {

  $('homeHelpBtn')
    .addEventListener(
      'click',
      () =>
        toast(
          'Live Scopus: enter Author ID → Search. Excel: upload dataset → select faculty → analyze.'
        )
    );
}


/* ==========================================================
   HOME
========================================================== */

function showHome() {

  $('dashboard')
    .classList
    .add('hidden');


  $('summaryView')
    .classList
    .add('hidden');


  $('welcome')
    .classList
    .remove('hidden');


  window.scrollTo({
    top: 0,
    behavior: 'smooth'
  });
}


if ($('homeBtn')) {

  $('homeBtn')
    .addEventListener(
      'click',
      showHome
    );
}


if ($('changeDatasetBtn')) {

  $('changeDatasetBtn')
    .addEventListener(
      'click',
      () => {

        showHome();

        toast(
          'Choose Live Scopus or Excel Data on the home screen.'
        );
      }
    );
}


/* ==========================================================
   APPLICATION STARTUP
========================================================== */

document.addEventListener('DOMContentLoaded', async () => {
  const loaded = await bootstrapMasterDataset();
  if (!loaded) {
    setMode('live');
  }
});
