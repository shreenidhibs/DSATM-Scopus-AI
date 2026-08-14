const $ = id => document.getElementById(id);

let facultyMeta = [];
let selectedFaculty = '';
let selectedLiveAuthorId = '';
let currentPublications = [];
let summaryRows = [];
let lastDashboard = null;
let currentDataSource = 'live';

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
   Search by Faculty Name OR Scopus Author ID
========================================================== */

let liveSearchMode = 'name';


/* ----------------------------------------------------------
   SAFE ELEMENT HELPERS
---------------------------------------------------------- */

function liveEl(id) {
  return document.getElementById(id);
}


function setLiveSearchButtonLoading(message) {

  const btn = liveEl('liveSearchBtn');

  if (!btn) {
    return;
  }

  btn.disabled = true;

  btn.innerHTML =
    `<span>${esc(message)}</span><b>•••</b>`;
}


function resetLiveSearchButton() {

  const btn = liveEl('liveSearchBtn');

  if (!btn) {
    return;
  }

  btn.disabled = false;

  btn.innerHTML =
    '<span>Search Live Scopus</span><b>→</b>';
}


/* ----------------------------------------------------------
   SEARCH MODE BUTTONS
---------------------------------------------------------- */

if (liveEl('liveByNameBtn')) {

  liveEl('liveByNameBtn').addEventListener(
    'click',
    () => setLiveSearchMode('name')
  );
}


if (liveEl('liveByIdBtn')) {

  liveEl('liveByIdBtn').addEventListener(
    'click',
    () => setLiveSearchMode('id')
  );
}


if (liveEl('liveSearchBtn')) {

  liveEl('liveSearchBtn').addEventListener(
    'click',
    searchLiveScopus
  );
}


/* ----------------------------------------------------------
   ENTER KEY SUPPORT
---------------------------------------------------------- */

if (liveEl('liveAuthorId')) {

  liveEl('liveAuthorId').addEventListener(
    'keydown',
    e => {

      if (e.key === 'Enter') {
        searchLiveScopus();
      }
    }
  );
}


if (liveEl('liveAuthorName')) {

  liveEl('liveAuthorName').addEventListener(
    'keydown',
    e => {

      if (e.key === 'Enter') {
        searchLiveScopus();
      }
    }
  );
}


/* ----------------------------------------------------------
   CHANGE SEARCH MODE
---------------------------------------------------------- */

function setLiveSearchMode(mode) {

  liveSearchMode =
    mode === 'id'
      ? 'id'
      : 'name';


  /* ---------------------------------------
     NAME BUTTON
  --------------------------------------- */

  if (liveEl('liveByNameBtn')) {

    liveEl('liveByNameBtn')
      .classList
      .toggle(
        'active',
        liveSearchMode === 'name'
      );
  }


  /* ---------------------------------------
     ID BUTTON
  --------------------------------------- */

  if (liveEl('liveByIdBtn')) {

    liveEl('liveByIdBtn')
      .classList
      .toggle(
        'active',
        liveSearchMode === 'id'
      );
  }


  /* ---------------------------------------
     NAME SEARCH PANEL
  --------------------------------------- */

  if (liveEl('liveNamePanel')) {

    liveEl('liveNamePanel')
      .classList
      .toggle(
        'hidden',
        liveSearchMode !== 'name'
      );
  }


  /* ---------------------------------------
     AUTHOR ID SEARCH PANEL
  --------------------------------------- */

  if (liveEl('liveIdPanel')) {

    liveEl('liveIdPanel')
      .classList
      .toggle(
        'hidden',
        liveSearchMode !== 'id'
      );
  }


  /* ---------------------------------------
     CLEAR PREVIOUS AUTHOR MATCHES
  --------------------------------------- */

  if (liveEl('liveAuthorMatches')) {

    liveEl('liveAuthorMatches')
      .classList
      .add('hidden');

    liveEl('liveAuthorMatches')
      .innerHTML = '';
  }


  /* ---------------------------------------
     STATUS MESSAGE
  --------------------------------------- */

  if (liveEl('liveStatus')) {

    liveEl('liveStatus')
      .classList
      .remove('loaded');


    liveEl('liveStatus').innerHTML =

      liveSearchMode === 'name'

        ? '<i></i><span>Enter faculty name to find matching Scopus authors.</span>'

        : '<i></i><span>Enter the numeric Scopus Author ID.</span>';
  }


  /* ---------------------------------------
     AUTO FOCUS
  --------------------------------------- */

  setTimeout(
    () => {

      if (
        liveSearchMode === 'name' &&
        liveEl('liveAuthorName')
      ) {

        liveEl('liveAuthorName')
          .focus();

      }

      else if (
        liveSearchMode === 'id' &&
        liveEl('liveAuthorId')
      ) {

        liveEl('liveAuthorId')
          .focus();
      }

    },
    100
  );
}


/* ==========================================================
   MAIN LIVE SCOPUS SEARCH
========================================================== */

async function searchLiveScopus() {

  /* ---------------------------------------
     SEARCH BY FACULTY NAME
  --------------------------------------- */

  if (liveSearchMode === 'name') {

    return searchLiveScopusByName();
  }


  /* ---------------------------------------
     SEARCH BY SCOPUS AUTHOR ID
  --------------------------------------- */

  const input =
    liveEl('liveAuthorId');


  const authorId =

    (input?.value || '')

      .trim()

      .replace(
        /\s+/g,
        ''
      );


  /* ---------------------------------------
     VALIDATE AUTHOR ID
  --------------------------------------- */

  if (!/^\d+$/.test(authorId)) {

    toast(
      'Enter a valid numeric Scopus Author ID.'
    );

    return;
  }


  /* ---------------------------------------
     LOAD AUTHOR
  --------------------------------------- */

  return loadLiveScopusAuthor(
    authorId
  );
}


/* ==========================================================
   SEARCH SCOPUS BY FACULTY NAME
========================================================== */

/* ==========================================================
   SEARCH LIVE SCOPUS BY FACULTY NAME
   Uses uploaded DSATM Excel faculty directory
========================================================== */

async function searchLiveScopusByName() {

  const input =
    liveEl('liveAuthorName');


  const name =
    (input?.value || '')
      .trim()
      .replace(/\s+/g, ' ');


  /* -------------------------------------------------------
     VALIDATE NAME
  ------------------------------------------------------- */

  if (name.length < 2) {

    toast(
      'Enter at least 2 characters of the faculty name.'
    );

    return;
  }


  /* -------------------------------------------------------
     CHECK WHETHER EXCEL DIRECTORY IS AVAILABLE
  ------------------------------------------------------- */

  if (
    !Array.isArray(facultyMeta) ||
    facultyMeta.length === 0
  ) {

    if (liveEl('liveStatus')) {

      liveEl('liveStatus')
        .classList
        .remove('loaded');


      liveEl('liveStatus').innerHTML =

        '<i></i>' +

        '<span>' +

        'Faculty directory is not loaded. ' +

        'Upload the DSATM Scopus Excel file first, ' +

        'or search directly using Scopus Author ID.' +

        '</span>';
    }


    toast(
      'Upload the DSATM Scopus Excel file first.'
    );

    return;
  }


  /* -------------------------------------------------------
     SHOW SEARCHING STATUS
  ------------------------------------------------------- */

  setLiveSearchButtonLoading(
    'Searching faculty directory…'
  );


  if (liveEl('liveStatus')) {

    liveEl('liveStatus')
      .classList
      .remove('loaded');


    liveEl('liveStatus').innerHTML =

      '<i></i>' +

      '<span>' +

      'Searching DSATM faculty directory…' +

      '</span>';
  }


  try {

    /* =====================================================
       NORMALIZE SEARCH TEXT
    ===================================================== */

    const query =
      name.toLowerCase();


    /* =====================================================
       FIND FACULTY MATCHES
    ===================================================== */

    const matches =

      facultyMeta

        .filter(
          item => {

            const facultyName =
              String(
                item.faculty || ''
              )
                .trim()
                .toLowerCase();


            return facultyName.includes(
              query
            );
          }
        )

        .slice(
          0,
          20
        );


    /* -------------------------------------------------------
       NO MATCH
    ------------------------------------------------------- */

    if (!matches.length) {

      if (liveEl('liveAuthorMatches')) {

        liveEl('liveAuthorMatches')
          .innerHTML = '';

        liveEl('liveAuthorMatches')
          .classList
          .add('hidden');
      }


      if (liveEl('liveStatus')) {

        liveEl('liveStatus').innerHTML =

          '<i></i>' +

          '<span>' +

          'Faculty not found in the DSATM directory. ' +

          'Try another spelling or use Scopus Author ID.' +

          '</span>';
      }


      toast(
        'Faculty not found in DSATM directory.'
      );


      return;
    }


    /* =====================================================
       IF ONLY ONE EXACT MATCH
    ===================================================== */

    const exactMatches =

      matches.filter(
        item =>

          String(
            item.faculty || ''
          )
            .trim()
            .toLowerCase()

          ===

          query
      );


    if (exactMatches.length === 1) {

      const exact =
        exactMatches[0];


      const authorId =

        String(
          exact.scopus_author_id || ''
        )
          .replace(/\D/g, '');


      /* ---------------------------------------------------
         VALID SCOPUS ID FOUND
      --------------------------------------------------- */

      if (authorId) {

        if (liveEl('liveAuthorId')) {

          liveEl('liveAuthorId').value =
            authorId;
        }


        if (liveEl('liveStatus')) {

          liveEl('liveStatus').innerHTML =

            `<i></i>

             <span>

               Faculty matched:

               <strong>
                 ${esc(exact.faculty)}
               </strong>

               <br>

               Loading live Scopus profile…

             </span>`;
        }


        resetLiveSearchButton();


        return loadLiveScopusAuthor(
          authorId
        );
      }
    }


    /* =====================================================
       SHOW MULTIPLE MATCHES
    ===================================================== */

    if (!liveEl('liveAuthorMatches')) {

      throw new Error(
        'Faculty match panel is missing.'
      );
    }


    liveEl('liveAuthorMatches')
      .innerHTML =

      matches

        .map(
          item => {

            const faculty =
              String(
                item.faculty || ''
              ).trim();


            const department =
              String(
                item.department ||
                'Department not detected'
              ).trim();


            const rawScopusId =
              String(
                item.scopus_author_id || ''
              ).trim();


            const authorId =
              rawScopusId
                .replace(
                  /\D/g,
                  ''
                );


            const hasValidId =
              /^\d+$/.test(
                authorId
              );


            const idText =
              hasValidId
                ? authorId
                : 'Not available';


            const disabled =
              hasValidId
                ? ''
                : 'disabled';


            return `

              <button

                type="button"

                class="live-author-card"

                data-author-id="${esc(authorId)}"

                ${disabled}

              >

                <span
                  class="live-author-card-main"
                >

                  <strong>

                    ${esc(faculty)}

                    <span
                      class="excel-match-badge"
                    >
                      DSATM Faculty
                    </span>

                  </strong>


                  <small>

                    ${esc(department)}

                  </small>

                </span>


                <span
                  class="author-id"
                >

                  Scopus ID:
                  ${esc(idText)}

                </span>

              </button>

            `;

          }
        )

        .join('');


    /* -------------------------------------------------------
       SHOW RESULTS
    ------------------------------------------------------- */

    liveEl('liveAuthorMatches')
      .classList
      .remove('hidden');


    /* =====================================================
       CLICK FACULTY
    ===================================================== */

    liveEl('liveAuthorMatches')

      .querySelectorAll(
        '.live-author-card'
      )

      .forEach(
        btn => {

          btn.addEventListener(

            'click',

            () => {

              const authorId =
                String(
                  btn.dataset.authorId ||
                  ''
                )
                  .replace(
                    /\D/g,
                    ''
                  );


              if (!authorId) {

                toast(
                  'No Scopus Author ID is available for this faculty.'
                );

                return;
              }


              /* -------------------------------------------
                 PUT ID INTO SCOPUS ID FIELD
              ------------------------------------------- */

              if (liveEl('liveAuthorId')) {

                liveEl('liveAuthorId')
                  .value =
                  authorId;
              }


              /* -------------------------------------------
                 HIDE SEARCH RESULTS
              ------------------------------------------- */

              liveEl('liveAuthorMatches')
                .classList
                .add('hidden');


              /* -------------------------------------------
                 LOAD LIVE DATA
              ------------------------------------------- */

              loadLiveScopusAuthor(
                authorId
              );
            }
          );
        }
      );


    /* =====================================================
       STATUS
    ===================================================== */

    if (liveEl('liveStatus')) {

      liveEl('liveStatus').innerHTML =

        `<i></i>

         <span>

           ${matches.length}

           DSATM faculty match${matches.length === 1
          ? ''
          : 'es'
        } found.

           Select the correct faculty.

         </span>`;
    }

  }

  catch (e) {

    console.error(
      'Faculty Directory Search Error:',
      e
    );


    if (liveEl('liveStatus')) {

      liveEl('liveStatus').innerHTML =

        `<i></i>

         <span>

           ${esc(e.message)}

         </span>`;
    }


    toast(
      e.message
    );

  }

  finally {

    resetLiveSearchButton();
  }
}


async function loadLiveScopusAuthor(authorId) {

  /* -------------------------------------------------------
     CLEAN AUTHOR ID
  ------------------------------------------------------- */

  authorId =
    String(authorId || '')
      .trim()
      .replace(/\D/g, '');


  /* -------------------------------------------------------
     VALIDATE AUTHOR ID
  ------------------------------------------------------- */

  if (!authorId) {

    toast(
      'Scopus Author ID is missing.'
    );

    return;
  }


  /* -------------------------------------------------------
     SHOW LOADING
  ------------------------------------------------------- */

  setLiveSearchButtonLoading(
    'Fetching live Scopus data…'
  );


  if (liveEl('liveStatus')) {

    liveEl('liveStatus')
      .classList
      .remove('loaded');


    liveEl('liveStatus').innerHTML =

      '<i></i>' +

      '<span>' +

      'Connecting to Elsevier Scopus…' +

      '</span>';
  }


  try {

    /* =====================================================
       CALL EXISTING FASTAPI AUTHOR ENDPOINT
    ===================================================== */

    const r =
      await fetch(

        `/api/scopus/author/${encodeURIComponent(
          authorId
        )

        }`
      );


    let d = {};


    try {

      d =
        await r.json();

    }

    catch (_) {

      d = {};
    }


    /* -------------------------------------------------------
       CHECK API RESPONSE
    ------------------------------------------------------- */

    if (
      !r.ok ||
      d.success === false
    ) {

      throw new Error(

        d.detail ||

        d.error ||

        'Unable to retrieve Scopus profile.'
      );
    }


    /* =====================================================
       SAVE SELECTED AUTHOR
    ===================================================== */

    selectedLiveAuthorId =
      authorId;


    /* =====================================================
       GET REAL FACULTY NAME
    ===================================================== */

    selectedFaculty =

      d.faculty_name ||

      d.faculty ||

      d.indexed_name ||

      `Scopus Author ${authorId}`;


    /* =====================================================
       NORMALIZE LIVE RESPONSE
    ===================================================== */

    d.faculty =
      selectedFaculty;


    d.faculty_name =

      d.faculty_name ||

      selectedFaculty;


    d.scopus_author_id =

      d.scopus_author_id ||

      d.author_id ||

      authorId;


    d.department =

      d.department ||

      d.affiliation ||

      'Affiliation not available';


    /* =====================================================
       USE REAL SCOPUS H-INDEX
    ===================================================== */

    if (

      d.kpis &&

      d.scopus_h_index !== undefined &&

      d.scopus_h_index !== null &&

      d.scopus_h_index !== ''

    ) {

      d.kpis.h_index =
        d.scopus_h_index;
    }


    /* =====================================================
       SAVE DASHBOARD
    ===================================================== */

    lastDashboard =
      d;


    /* =====================================================
       LIVE STATUS
    ===================================================== */

    if (liveEl('liveStatus')) {

      liveEl('liveStatus')
        .classList
        .add('loaded');


      /* ---------------------------------------------------
         TOTAL PUBLICATIONS
      --------------------------------------------------- */

      const total =

        d.total_publications_scopus ??

        d.total_publications ??

        d.kpis?.publications ??

        0;


      /* ---------------------------------------------------
         RETURNED PUBLICATIONS
      --------------------------------------------------- */

      const returned =

        d.returned_publications ??

        d.publications?.length ??

        0;


      /* ---------------------------------------------------
         STATUS NOTE
      --------------------------------------------------- */

      const note =

        d.truncated

          ?

          `Showing ${returned} of ${total} publications`

          :

          `${total} publications retrieved`;


      /* ---------------------------------------------------
         DISPLAY STATUS
      --------------------------------------------------- */

      liveEl('liveStatus').innerHTML =

        `<i></i>

         <span>

            <strong>
              Live Scopus connected
            </strong>

            <br>

            ${esc(note)}

         </span>`;
    }


    /* =====================================================
       RENDER EXISTING DASHBOARD
    ===================================================== */

    renderDashboard(
      d,
      'live'
    );


    /* =====================================================
       SUCCESS MESSAGE
    ===================================================== */

    toast(
      'Live Scopus profile loaded successfully.'
    );

  }

  catch (e) {

    /* =====================================================
       ERROR
    ===================================================== */

    console.error(
      'Live Scopus Profile Error:',
      e
    );


    if (liveEl('liveStatus')) {

      liveEl('liveStatus').innerHTML =

        `<i></i>

         <span>

           ${esc(e.message)}

         </span>`;
    }


    toast(
      e.message
    );

  }

  finally {

    /* =====================================================
       RESET BUTTON
    ===================================================== */

    resetLiveSearchButton();
  }
}


/* ==========================================================
   DEFAULT LIVE SEARCH MODE
========================================================== */

if (liveEl('liveAuthorName')) {

  setLiveSearchMode(
    'name'
  );

}

else {

  /*
     Backward compatibility:
     if index.html has not yet been updated
     with the Faculty Name controls,
     continue using Scopus Author ID.
  */

  liveSearchMode =
    'id';
}


/* ==========================================================
   END LIVE SCOPUS SEARCH
========================================================== */




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
      facultyMeta =
        d.faculty_meta || [];

      if ($('refreshScopusExcelBtn')) {
        $('refreshScopusExcelBtn').disabled =
          !Array.isArray(facultyMeta) ||
          facultyMeta.length === 0;
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
   REFRESH UPLOADED EXCEL WITH LIVE SCOPUS DATA
========================================================== */

/* ==========================================================
   REFRESH EXCEL WITH LIVE SCOPUS DATA
   Works with auto-loaded or manually uploaded Excel
========================================================== */

if ($('refreshScopusExcelBtn')) {

  $('refreshScopusExcelBtn')
    .addEventListener(
      'click',
      async () => {

        const btn =
          $('refreshScopusExcelBtn');


        /* --------------------------------------------------
           CHECK FACULTY DIRECTORY
        -------------------------------------------------- */

        if (
          !Array.isArray(facultyMeta) ||
          facultyMeta.length === 0
        ) {

          toast(
            'Faculty directory is not loaded.'
          );

          if ($('uploadStatus')) {

            $('uploadStatus')
              .classList
              .remove('loaded');


            $('uploadStatus').innerHTML =

              '<i></i>' +

              '<span>' +

              'No faculty directory is available. ' +

              'Please restart the application or upload the Excel file.' +

              '</span>';
          }

          return;
        }


        /* --------------------------------------------------
           START REFRESH
        -------------------------------------------------- */

        btn.disabled =
          true;


        btn.innerHTML =

          '<span>' +

          'Refreshing faculty from Scopus…' +

          '</span>' +

          '<b>•••</b>';


        if ($('uploadStatus')) {

          $('uploadStatus')
            .classList
            .remove('loaded');


          $('uploadStatus').innerHTML =

            '<i></i>' +

            '<span>' +

            `Retrieving live Scopus data for ${facultyMeta.length} faculty…`

            +

            '</span>';
        }


        try {

          /* --------------------------------------------------
             CALL BACKEND REFRESH API
          -------------------------------------------------- */

          const r =
            await fetch(

              '/api/scopus/refresh-excel' +

              '?max_records_per_author=500'
            );


          /* --------------------------------------------------
             HANDLE ERROR RESPONSE
          -------------------------------------------------- */

          if (!r.ok) {

            let message =
              'Unable to refresh Excel from Scopus.';


            try {

              const d =
                await r.json();


              message =

                d.detail ||

                d.error ||

                d.message ||

                message;

            }

            catch (_) { }


            throw new Error(
              message
            );
          }


          /* --------------------------------------------------
             DOWNLOAD GENERATED EXCEL
          -------------------------------------------------- */

          const blob =
            await r.blob();


          const disposition =

            r.headers.get(
              'Content-Disposition'
            )

            || '';


          const match =

            disposition.match(
              /filename="?([^";]+)"?/i
            );


          const filename =

            match

              ? match[1]

              : 'DSATM_Scopus_Live_Updated.xlsx';


          /* --------------------------------------------------
             REFRESH SUMMARY
          -------------------------------------------------- */

          const updated =

            r.headers.get(
              'X-Scopus-Updated-Faculty'
            )

            || '0';


          const issues =

            r.headers.get(
              'X-Scopus-Refresh-Issues'
            )

            || '0';


          /* --------------------------------------------------
             DOWNLOAD FILE
          -------------------------------------------------- */

          const url =
            URL.createObjectURL(
              blob
            );


          const a =
            document.createElement(
              'a'
            );


          a.href =
            url;


          a.download =
            filename;


          document.body
            .appendChild(
              a
            );


          a.click();


          a.remove();


          URL.revokeObjectURL(
            url
          );


          /* --------------------------------------------------
             SUCCESS STATUS
          -------------------------------------------------- */

          if ($('uploadStatus')) {

            $('uploadStatus')
              .classList
              .add('loaded');


            $('uploadStatus').innerHTML =

              `<i></i>

               <span>

                 <strong>
                   Live Scopus refresh completed
                 </strong>

                 <br>

                 ${esc(updated)} faculty updated

                 ·

                 ${esc(issues)} issue(s)

                 <br>

                 Updated Excel downloaded successfully.

               </span>`;
          }


          toast(
            'Live Scopus workbook created successfully.'
          );


        }

        catch (e) {

          /* --------------------------------------------------
             ERROR
          -------------------------------------------------- */

          console.error(
            'Refresh Excel Error:',
            e
          );


          if ($('uploadStatus')) {

            $('uploadStatus')
              .classList
              .remove('loaded');


            $('uploadStatus').innerHTML =

              `<i></i>

               <span>

                 ${esc(e.message)}

               </span>`;
          }


          toast(
            e.message
          );

        }

        finally {

          /* --------------------------------------------------
             RESET BUTTON
          -------------------------------------------------- */

          btn.disabled =
            false;


          btn.innerHTML =

            '<span>' +

            'Refresh Excel from Live Scopus' +

            '</span>' +

            '<b>↻</b>';
        }
      }
    );
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

    <div class="publication-links">

        ${(r.doi_url || r.doi)

          ? `<a
                  class="doi-link"
                  target="_blank"
                  rel="noopener noreferrer"
                  href="${esc(
            r.doi_url ||
            `https://doi.org/${r.doi}`
          )}"
               >
                  DOI ↗
               </a>`

          : ''
        }

        ${r.scopus_url

          ? `<a
                  class="doi-link scopus-link"
                  target="_blank"
                  rel="noopener noreferrer"
                  href="${esc(r.scopus_url)}"
               >
                  Scopus ↗
               </a>`

          : ''
        }

        ${!(r.doi_url || r.doi || r.scopus_url)
          ? '—'
          : ''
        }

    </div>

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
   INITIAL MODE
========================================================== */
/* ==========================================================
   AUTO LOAD FACULTY DIRECTORY FROM BACKEND
========================================================== */

async function loadDefaultFacultyDirectory() {

  try {

    const r =
      await fetch('/api/bootstrap');

    const d =
      await r.json();

    if (
      !r.ok ||
      !d.loaded
    ) {

      console.log(
        'No default faculty directory loaded.'
      );

      return;
    }

    facultyMeta =
      d.faculty_meta || [];

    if ($('refreshScopusExcelBtn')) {
      $('refreshScopusExcelBtn').disabled =
        facultyMeta.length === 0;
    }
    $('facultySearch').disabled =
      false;

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

    if ($('homeSummaryBtn')) {
      $('homeSummaryBtn').disabled =
        false;
    }

    if ($('openDashboardBtn')) {
      $('openDashboardBtn').disabled =
        false;
    }

    $('facultyMeta').textContent =
      `${facultyMeta.length} faculty shown`;

    fillDepartments(
      d.departments || []
    );

    applyFacultyFilters();

    console.log(
      `Default faculty directory loaded: ${facultyMeta.length} faculty`
    );

  }

  catch (e) {

    console.error(
      'Unable to load default faculty directory:',
      e
    );
  }
}
/* ==========================================================
   FULL INSTITUTION SCOPUS SYNC
========================================================== */

if ($('refreshScopusExcelBtn')) {

  $('refreshScopusExcelBtn')
    .addEventListener(
      'click',
      async () => {

        const btn =
          $('refreshScopusExcelBtn');

        let progressTimer =
          null;


        if (!facultyMeta.length) {

          toast(
            'Institution faculty directory is not loaded.'
          );

          return;
        }


        btn.disabled = true;


        btn.innerHTML =
          '<span>Starting institution sync…</span><b>•••</b>';


        /* ==================================================
           POLL PROGRESS
        ================================================== */

        const updateProgress =
          async () => {

            try {

              const r =
                await fetch(
                  '/api/scopus/sync-progress',
                  {
                    cache: 'no-store'
                  }
                );


              if (!r.ok) {
                return;
              }


              const p =
                await r.json();


              const percent =
                Number(
                  p.percent || 0
                );


              btn.innerHTML =

                `<span>

                  Syncing Scopus ${percent}%

                </span>

                <b>↻</b>`;


              if ($('uploadStatus')) {

                $('uploadStatus').innerHTML =

                  `<div style="width:100%">

                    <div
                      style="
                        display:flex;
                        justify-content:space-between;
                        gap:10px;
                        margin-bottom:7px;
                      "
                    >

                      <strong>
                        Institution Scopus Sync
                      </strong>

                      <strong>
                        ${percent}%
                      </strong>

                    </div>


                    <div
                      style="
                        width:100%;
                        height:7px;
                        border-radius:20px;
                        background:#1c2b41;
                        overflow:hidden;
                        margin-bottom:8px;
                      "
                    >

                      <div
                        style="
                          height:100%;
                          width:${percent}%;
                          border-radius:20px;
                          background:#2f86ed;
                          transition:width .35s ease;
                        "
                      ></div>

                    </div>


                    <span>

                      ${esc(
                    p.message ||
                    'Synchronizing Scopus…'
                  )}

                    </span>


                    <br>


                    <small>

                      New publications:
                      ${Number(
                    p.new_publications || 0
                  )}

                      &nbsp; · &nbsp;

                      Updated:
                      ${Number(
                    p.updated_publications || 0
                  )}

                      &nbsp; · &nbsp;

                      New authors:
                      ${Number(
                    p.new_authors || 0
                  )}

                      &nbsp; · &nbsp;

                      Issues:
                      ${Number(
                    p.issues || 0
                  )}

                    </small>

                  </div>`;
              }

            }

            catch (_) {

              // Ignore temporary polling errors.
            }
          };


        progressTimer =
          setInterval(
            updateProgress,
            1000
          );


        updateProgress();


        try {

          /* ==================================================
             RUN INSTITUTION SYNC
          ================================================== */

          const r =
            await fetch(
              '/api/scopus/refresh-excel',
              {
                cache: 'no-store'
              }
            );


          if (!r.ok) {

            let message =
              'Unable to synchronize institution with Scopus.';


            try {

              const d =
                await r.json();


              message =
                d.detail ||
                d.error ||
                d.message ||
                message;

            }

            catch (_) { }


            throw new Error(
              message
            );
          }


          /* ==================================================
             RESPONSE COUNTS
          ================================================== */

          const oldCount =
            r.headers.get(
              'X-Scopus-Existing'
            ) || '0';


          const total =
            r.headers.get(
              'X-Scopus-Total'
            ) || '0';


          const newPublications =
            r.headers.get(
              'X-Scopus-New-Publications'
            ) || '0';


          const newAuthors =
            r.headers.get(
              'X-Scopus-New-Authors'
            ) || '0';


          const updated =
            r.headers.get(
              'X-Scopus-Updated'
            ) || '0';


          const issues =
            r.headers.get(
              'X-Scopus-Issues'
            ) || '0';


          /* ==================================================
             DOWNLOAD MASTER EXCEL
          ================================================== */

          const blob =
            await r.blob();


          const url =
            URL.createObjectURL(
              blob
            );


          const a =
            document.createElement(
              'a'
            );


          a.href =
            url;


          a.download =
            'DSATM_Scopus_Master_Updated.xlsx';


          document.body
            .appendChild(
              a
            );


          a.click();


          a.remove();


          URL.revokeObjectURL(
            url
          );


          /* ==================================================
             RELOAD FACULTY DIRECTORY FROM BACKEND
          ================================================== */

          await loadDefaultFacultyDirectory();


          if ($('uploadStatus')) {

            $('uploadStatus')
              .classList
              .add('loaded');


            $('uploadStatus').innerHTML =

              `<div style="width:100%">

                <strong>
                  ✓ Institution Scopus Sync Complete
                </strong>

                <br><br>

                Existing Excel:
                ${esc(oldCount)}

                <br>

                Current Scopus:
                ${esc(total)}

                <br>

                <strong>
                  New publications added:
                  +${esc(newPublications)}
                </strong>

                <br>

                Existing publications updated:
                ${esc(updated)}

                <br>

                <strong>
                  New DSATM authors detected:
                  +${esc(newAuthors)}
                </strong>

                <br>

                Issues:
                ${esc(issues)}

                <br><br>

                Master Excel saved and downloaded.

              </div>`;
          }


          toast(
            `Scopus sync complete: +${newPublications} publications`
          );


        }

        catch (e) {

          console.error(
            'Institution Scopus Sync Error:',
            e
          );


          if ($('uploadStatus')) {

            $('uploadStatus')
              .classList
              .remove('loaded');


            $('uploadStatus').innerHTML =

              `<i></i>

               <span>

                 ${esc(e.message)}

               </span>`;
          }


          toast(
            e.message
          );

        }

        finally {

          if (progressTimer) {

            clearInterval(
              progressTimer
            );
          }


          btn.disabled =
            false;


          btn.innerHTML =
            '<span>Refresh Excel from Live Scopus</span><b>↻</b>';
        }
      }
    );
}
loadDefaultFacultyDirectory();

setMode('live');