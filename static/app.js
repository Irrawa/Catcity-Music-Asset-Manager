let vocab = null;
let tracks = [];
let clusters = [];
let filteredIds = [];
let selectedId = null;
let selectedTrack = null;
let dirty = false;

// Cluster split UI state: cluster_id -> Set(track_id)
let clusterSplitSelection = {};

const el = (id) => document.getElementById(id);


// ---- Virtual key builder (mood_context_instrument_style_sn)
const VK_GROUPS = {
  mood: 'moods',
  context: 'usable_in_contexts',
  instrument: 'instruments',
  style: 'styles',
};
const VK_PROTECTED_GROUPS = new Set(Object.values(VK_GROUPS));

// Map tag group name -> VK select element id
const VK_SELECT_BY_GROUP = {
  [VK_GROUPS.mood]: 'vkMoodSelect',
  [VK_GROUPS.context]: 'vkContextSelect',
  [VK_GROUPS.instrument]: 'vkInstrumentSelect',
  [VK_GROUPS.style]: 'vkStyleSelect',
};

function slugifyPartForKey(s) {
  // Keep underscores as separators only; normalize inner separators to hyphens.
  s = String(s || '').toLowerCase().trim();
  s = s.replace(/[^a-z0-9]+/g, '-');
  s = s.replace(/-+/g, '-').replace(/^-|-$/g, '');
  return s || 'unk';
}

function escapeRegExp(str) {
  return String(str).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function getVkTagValue(groupKey) {
  const arr = (selectedTrack && selectedTrack.tags && selectedTrack.tags[groupKey]) ? selectedTrack.tags[groupKey] : [];
  return (arr && arr.length) ? String(arr[0]) : '';
}

function setVkTagValue(groupKey, value) {
  if (!selectedTrack) return;
  selectedTrack.tags = selectedTrack.tags || {};

  // IMPORTANT: preserve additional tag values.
  // VK uses ONLY the first value as the canonical identity tag,
  // but teams still want multi-choice tags for the same group.
  // So: move the chosen value to the front, and keep the rest.
  let arr = selectedTrack.tags[groupKey];
  if (!Array.isArray(arr)) arr = [];

  const v = (value == null) ? '' : String(value);
  if (!v) {
    selectedTrack.tags[groupKey] = [];
    return;
  }

  const vLow = v.toLowerCase();
  const rest = [];
  for (const x of arr) {
    const xs = String(x);
    if (xs.toLowerCase() === vLow) continue;
    rest.push(xs);
  }
  selectedTrack.tags[groupKey] = [v, ...rest];
}

function formatSn(n) {
  return String(n).padStart(3, '0');
}

function computeNextSn(prefix, excludeTrackId=null) {
  const used = new Set();
  const re = new RegExp('^' + escapeRegExp(prefix) + '(\\d+)$');
  for (const t of tracks) {
    if (excludeTrackId && t.track_id === excludeTrackId) continue;
    const k = t.virtual_key || '';
    const m = k.match(re);
    if (!m) continue;
    const sn = parseInt(m[1], 10);
    if (!isNaN(sn)) used.add(sn);
  }
  let sn = 1;
  while (used.has(sn)) sn += 1;
  return sn;
}

function currentSnIfValid(prefix) {
  const k = (selectedTrack && selectedTrack.virtual_key) ? String(selectedTrack.virtual_key) : '';
  const re = new RegExp('^' + escapeRegExp(prefix) + '(\\d+)$');
  const m = k.match(re);
  if (!m) return null;
  const sn = parseInt(m[1], 10);
  return isNaN(sn) ? null : sn;
}

function rebuildVirtualKey(forceNewSn=false) {
  if (!selectedTrack) return;

  const moodRaw = el('vkMoodSelect') ? el('vkMoodSelect').value : getVkTagValue(VK_GROUPS.mood);
  const ctxRaw = el('vkContextSelect') ? el('vkContextSelect').value : getVkTagValue(VK_GROUPS.context);
  const instRaw = el('vkInstrumentSelect') ? el('vkInstrumentSelect').value : getVkTagValue(VK_GROUPS.instrument);
  const styleRaw = el('vkStyleSelect') ? el('vkStyleSelect').value : getVkTagValue(VK_GROUPS.style);

  const mood = slugifyPartForKey(moodRaw);
  const ctx = slugifyPartForKey(ctxRaw);
  const inst = slugifyPartForKey(instRaw);
  const style = slugifyPartForKey(styleRaw);

  const prefix = `${mood}_${ctx}_${inst}_${style}_`;

  // Prefer keeping the existing SN if it is still unique for this prefix.
  let sn = null;
  if (!forceNewSn) {
    const cur = currentSnIfValid(prefix);
    if (cur != null) {
      const candidate = prefix + formatSn(cur);
      const collision = tracks.some(t => t.track_id !== selectedTrack.track_id && (t.virtual_key || '') === candidate);
      if (!collision) sn = cur;
    }
  }

  if (sn == null) {
    sn = computeNextSn(prefix, selectedTrack.track_id);
  }

  const newKey = prefix + formatSn(sn);

  // Write back: tags (single selection) + virtual key
  setVkTagValue(VK_GROUPS.mood, moodRaw);
  setVkTagValue(VK_GROUPS.context, ctxRaw);
  setVkTagValue(VK_GROUPS.instrument, instRaw);
  setVkTagValue(VK_GROUPS.style, styleRaw);

  selectedTrack.virtual_key = newKey;

  // Update header + input
  if (el('dVirtualKey')) el('dVirtualKey').textContent = newKey;
  if (el('virtualKeyInput')) el('virtualKeyInput').value = newKey;

  setDirty(true);
}

function labelForVkOption(v) {
  if (String(v) === 'unk' || String(v).toLowerCase() === 'unknown') return '---- (unknown)';
  return String(v);
}

function buildSelectOptions(selectEl, values, placeholder='—', labelFn=null) {
  if (!selectEl) return;
  const prev = selectEl.value;
  selectEl.innerHTML = '';

  const empty = document.createElement('option');
  empty.value = '';
  empty.textContent = placeholder;
  selectEl.appendChild(empty);

  for (const v of values || []) {
    const o = document.createElement('option');
    o.value = v;
    o.textContent = labelFn ? labelFn(v) : v;
    selectEl.appendChild(o);
  }

  if (prev) selectEl.value = prev;
}

function renderVirtualKeyBuilder() {
  if (!selectedTrack || !vocab) return;

  const moodOpts = (vocab.tag_vocab && vocab.tag_vocab[VK_GROUPS.mood]) ? vocab.tag_vocab[VK_GROUPS.mood] : [];
  const ctxOpts = (vocab.tag_vocab && vocab.tag_vocab[VK_GROUPS.context]) ? vocab.tag_vocab[VK_GROUPS.context] : [];
  const instOpts = (vocab.tag_vocab && vocab.tag_vocab[VK_GROUPS.instrument]) ? vocab.tag_vocab[VK_GROUPS.instrument] : [];
  const styleOpts = (vocab.tag_vocab && vocab.tag_vocab[VK_GROUPS.style]) ? vocab.tag_vocab[VK_GROUPS.style] : [];

  buildSelectOptions(el('vkMoodSelect'), moodOpts, '—', labelForVkOption);
  buildSelectOptions(el('vkContextSelect'), ctxOpts, '—', labelForVkOption);
  buildSelectOptions(el('vkInstrumentSelect'), instOpts, '—', labelForVkOption);
  buildSelectOptions(el('vkStyleSelect'), styleOpts, '—', labelForVkOption);

  // Try to initialize from existing tags
  const curMood = getVkTagValue(VK_GROUPS.mood);
  const curCtx = getVkTagValue(VK_GROUPS.context);
  const curInst = getVkTagValue(VK_GROUPS.instrument);
  const curStyle = getVkTagValue(VK_GROUPS.style);

  if (el('vkMoodSelect')) el('vkMoodSelect').value = curMood;
  if (el('vkContextSelect')) el('vkContextSelect').value = curCtx;
  if (el('vkInstrumentSelect')) el('vkInstrumentSelect').value = curInst;
  if (el('vkStyleSelect')) el('vkStyleSelect').value = curStyle;

  // Show current virtual key (do not auto-mutate on selection unless user edits / regenerates).
  if (el('virtualKeyInput')) el('virtualKeyInput').value = selectedTrack.virtual_key || '';
}

// ---- Scales helpers (support configurable ranges + legacy catalogs)
function getScaleInfo() {
  const defs = vocab && vocab.scale_defs ? vocab.scale_defs : {};

  // If this is an older catalog, scale_defs may be missing. Fall back to scale_names.
  if (Object.keys(defs).length === 0 && vocab && Array.isArray(vocab.scale_names) && vocab.scale_names.length) {
    for (const n of vocab.scale_names) {
      if (!defs[n]) defs[n] = { min: 0, max: 5, default: 0 };
    }
  }

  // Prefer the order in vocab.scale_names if present, otherwise alphabetical.
  let names = [];
  if (vocab && Array.isArray(vocab.scale_names) && vocab.scale_names.length) {
    names = vocab.scale_names.filter(n => defs[n]);
    for (const k of Object.keys(defs)) {
      if (!names.includes(k)) names.push(k);
    }
  } else {
    names = Object.keys(defs).sort((a,b) => String(a).localeCompare(String(b)));
  }

  return { defs, names };
}

function setDirty(v) {
  dirty = v;
  el('saveBtn').disabled = !dirty;
}

async function apiJSON(url, opts={}) {
  const r = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const data = await r.json();
      msg = data.detail || JSON.stringify(data);
    } catch (_) {}
    throw new Error(msg);
  }
  return await r.json();
}

function fmtSec(s) {
  if (s == null || isNaN(s)) return '';
  const m = Math.floor(s / 60);
  const ss = Math.floor(s % 60);
  return `${m}:${String(ss).padStart(2,'0')}`;
}

function buildRoleOptions(selectEl, roles) {
  selectEl.innerHTML = '';
  const empty = document.createElement('option');
  empty.value = '';
  empty.textContent = '—';
  selectEl.appendChild(empty);
  for (const r of roles) {
    const o = document.createElement('option');
    o.value = r;
    o.textContent = r;
    selectEl.appendChild(o);
  }
}

function buildFilterUI() {
  // Role filters
  const roleFilter = el('roleFilter');
  roleFilter.innerHTML = '<option value="">All roles</option>';
  for (const r of vocab.primary_roles) {
    const o = document.createElement('option');
    o.value = r;
    o.textContent = r;
    roleFilter.appendChild(o);
  }

  buildRoleOptions(el('primaryRoleSelect'), vocab.primary_roles);

  // Tag group selectors
  const groups = Object.keys(vocab.tag_vocab || {});
  const tg = el('tagGroupSelect');
  tg.innerHTML = '';
  for (const g of groups) {
    const o = document.createElement('option');
    o.value = g;
    o.textContent = g;
    tg.appendChild(o);
  }
  if (groups.length === 0) {
    const o = document.createElement('option');
    o.value = '';
    o.textContent = '(no tag groups)';
    tg.appendChild(o);
  }
  tg.onchange = () => {
    populateTagValueSelect();
    renderList();
  };

  // Scale filters
  const sf = el('scaleFilters');
  sf.innerHTML = '';
  const { defs, names } = getScaleInfo();
  for (const sname of names) {
    const sdef = defs[sname] || { min: 0, max: 5, default: 0 };
    const div = document.createElement('div');
    div.className = 'scale-filter';
    div.innerHTML = `
      <div class="muted tiny">${sname}</div>
      <input type="number" min="${sdef.min}" max="${sdef.max}" step="1" id="sf_${sname}_min" placeholder="min" />
      <input type="number" min="${sdef.min}" max="${sdef.max}" step="1" id="sf_${sname}_max" placeholder="max" />
    `;
    sf.appendChild(div);
    el(`sf_${sname}_min`).addEventListener('input', renderList);
    el(`sf_${sname}_max`).addEventListener('input', renderList);
  }

  populateTagValueSelect();
}

function populateTagValueSelect() {
  const group = el('tagGroupSelect').value;
  const tv = el('tagValueSelect');
  tv.innerHTML = '<option value="">Any tag</option>';
  const vals = (vocab.tag_vocab && vocab.tag_vocab[group]) ? vocab.tag_vocab[group] : [];
  for (const v of vals) {
    const o = document.createElement('option');
    o.value = v;
    o.textContent = v;
    tv.appendChild(o);
  }
  tv.onchange = renderList;
}

function getFilters() {
  const q = el('searchInput').value.trim().toLowerCase();
  const role = el('roleFilter').value;
  const loopable = el('loopableFilter').checked;
  const lenMin = parseFloat(el('lenMin').value);
  const lenMax = parseFloat(el('lenMax').value);
  const tagGroup = el('tagGroupSelect').value;
  const tagValue = el('tagValueSelect').value;

  const scales = {};
  const { names } = getScaleInfo();
  for (const sname of names) {
    const minEl = el(`sf_${sname}_min`);
    const maxEl = el(`sf_${sname}_max`);
    const mn = minEl.value === '' ? null : parseInt(minEl.value, 10);
    const mx = maxEl.value === '' ? null : parseInt(maxEl.value, 10);
    scales[sname] = { mn, mx };
  }

  return { q, role, loopable, lenMin, lenMax, tagGroup, tagValue, scales };
}

function passesFilters(t, f) {
  if (f.role && (t.primary_role || '') !== f.role) return false;
  if (f.loopable && !(t.loop_info && t.loop_info.can_loop)) return false;

  if (!isNaN(f.lenMin) && f.lenMin !== null) {
    if (t.length_sec == null || t.length_sec < f.lenMin) return false;
  }
  if (!isNaN(f.lenMax) && f.lenMax !== null) {
    if (t.length_sec == null || t.length_sec > f.lenMax) return false;
  }

  if (f.tagValue) {
    const arr = (t.tags && t.tags[f.tagGroup]) ? t.tags[f.tagGroup] : [];
    if (!arr.includes(f.tagValue)) return false;
  }

  const { defs } = getScaleInfo();
  for (const [name, {mn, mx}] of Object.entries(f.scales)) {
    const sdef = defs[name] || { min: 0, max: 5, default: 0 };
    const fallback = sdef.default != null ? sdef.default : sdef.min;
    const v = (t.scales && t.scales[name] != null) ? t.scales[name] : fallback;
    if (mn != null && v < mn) return false;
    if (mx != null && v > mx) return false;
  }

  if (f.q) {
    const hay = `${t.virtual_key} ${t.original_path} ${t.notes || ''}`.toLowerCase();
    if (!hay.includes(f.q)) return false;
  }

  return true;
}

function renderList() {
  const f = getFilters();
  const list = el('trackList');
  list.innerHTML = '';

  const filtered = tracks.filter(t => passesFilters(t, f));
  filtered.sort((a,b) => (a.virtual_key || '').localeCompare(b.virtual_key || ''));
  filteredIds = filtered.map(t => t.track_id);

  el('filterHint').textContent = `${filtered.length} / ${tracks.length} tracks`;

  for (const t of filtered) {
    const div = document.createElement('div');
    div.className = 'track' + (t.track_id === selectedId ? ' active' : '');
    div.onclick = () => selectTrack(t.track_id);

    const left = document.createElement('div');
    left.style.flex = '1';
    left.innerHTML = `
      <div class="name">${escapeHtml(t.virtual_key || '(unnamed)')}</div>
      <div class="meta">${escapeHtml((t.primary_role || ''))} · ${escapeHtml((t.cluster_name || ''))} · ${escapeHtml((t.file_format||''))} · ${escapeHtml(fmtSec(t.length_sec))}</div>
    `;

    const right = document.createElement('div');
    const badge = document.createElement('div');
    badge.className = 'badge' + (t.missing_file ? ' missing' : '');
    badge.textContent = t.missing_file ? 'missing' : (t.duplicate_of ? 'dup' : '');
    right.appendChild(badge);

    div.appendChild(left);
    div.appendChild(right);
    list.appendChild(div);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

async function selectTrack(id) {
  if (dirty) {
    const ok = confirm('You have unsaved changes. Discard them?');
    if (!ok) return;
  }

  selectedId = id;
  setDirty(false);

  renderList();

  const data = await apiJSON(`/api/track/${id}`);
  selectedTrack = data.track;
  showDetail(selectedTrack);
}

function showDetail(t) {
  el('emptyState').style.display = 'none';
  el('detail').style.display = 'block';

  el('dVirtualKey').textContent = t.virtual_key;
  el('dPath').textContent = t.original_path;

  const dc = el('dCluster');
  if (dc) {
    const cname = (clusters || []).find(c => c.cluster_id === t.cluster_id)?.name || '';
    dc.textContent = cname ? `Cluster: ${cname}` : '';
  }

  const miss = el('dMissing');
  miss.className = 'badge' + (t.missing_file ? ' missing' : '');
  miss.textContent = t.missing_file ? 'missing file' : (t.duplicate_of ? 'duplicate' : '');

  // Missing actions
  const ma = el('missingActions');
  if (ma) {
    ma.style.display = t.missing_file ? 'block' : 'none';
  }
  const mh = el('missingHint');
  if (mh) {
    if (t.missing_file) {
      const fn = t.raw_file_name || (t.original_path ? t.original_path.split('/').pop() : '');
      const pd = t.raw_parent_dir_name || '';
      const pdText = pd ? ` / parent folder: ${pd}` : '';
      mh.textContent = `Expected filename: ${fn}${pdText}. Use "Locate file..." to relink, or remove the track if it is no longer needed.`;
    } else {
      mh.textContent = '';
    }
  }

  el('virtualKeyInput').value = t.virtual_key || '';
  renderVirtualKeyBuilder();
  el('primaryRoleSelect').value = t.primary_role || '';
  el('bpmInput').value = t.bpm != null ? t.bpm : '';
  el('lengthInput').value = t.length_sec != null ? t.length_sec : '';

  // Player
  const player = el('player');
  if (t.missing_file) {
    player.removeAttribute('src');
    player.load();
  } else {
    player.src = `/audio/${t.track_id}`;
  }

  // AB loop defaults from loop_info
  el('abA').value = (t.loop_info && t.loop_info.loop_start_sec != null) ? t.loop_info.loop_start_sec : '';
  el('abB').value = (t.loop_info && t.loop_info.loop_end_sec != null) ? t.loop_info.loop_end_sec : '';
  el('abLoopToggle').checked = false;

  // Scales
  const se = el('scalesEditor');
  se.innerHTML = '';
  const { defs, names } = getScaleInfo();
  for (const sname of names) {
    const sdef = defs[sname] || { min: 0, max: 5, default: 0 };
    const fallback = sdef.default != null ? sdef.default : sdef.min;
    const v = (t.scales && t.scales[sname] != null) ? t.scales[sname] : fallback;
    const wrap = document.createElement('div');
    wrap.innerHTML = `
      <label>${sname}
        <input type="range" min="${sdef.min}" max="${sdef.max}" step="1" id="scale_${sname}" value="${v}" />
      </label>
    `;
    se.appendChild(wrap);
    wrap.querySelector('input').addEventListener('input', (ev) => {
      selectedTrack.scales[sname] = parseInt(ev.target.value,10);
      setDirty(true);
    });
  }

  // Tags
  renderTagsEditor();

  // Loop info
  el('canLoop').checked = !!(t.loop_info && t.loop_info.can_loop);
  el('loopStart').value = (t.loop_info && t.loop_info.loop_start_sec != null) ? t.loop_info.loop_start_sec : '';
  el('loopEnd').value = (t.loop_info && t.loop_info.loop_end_sec != null) ? t.loop_info.loop_end_sec : '';
  el('introSec').value = (t.loop_info && t.loop_info.intro_sec != null) ? t.loop_info.intro_sec : '';
  el('outroSec').value = (t.loop_info && t.loop_info.outro_sec != null) ? t.loop_info.outro_sec : '';

  // Licensing
  const lic = t.licensing || {};
  el('licSource').value = lic.source_pack || '';
  el('licType').value = lic.license_type || '';
  el('licProof').value = lic.proof_url_or_file || '';
  el('licAttrReq').checked = !!lic.attribution_required;
  el('licAttrText').value = lic.attribution_text || '';

  el('notesInput').value = t.notes || '';

  wireDetailInputs();
}
function renderTagsEditor() {
  const te = el('tagsEditor');
  te.innerHTML = '';

  const tag_vocab = vocab.tag_vocab || {};
  for (const [group, values] of Object.entries(tag_vocab)) {
    const isProtected = VK_PROTECTED_GROUPS.has(group);
    const cur = (selectedTrack.tags && selectedTrack.tags[group]) ? selectedTrack.tags[group] : [];

    const box = document.createElement('div');
    box.className = 'card';
    box.style.marginBottom = '10px';
    const title = document.createElement('div');
    title.className = 'card-title';
    title.textContent = group;
    box.appendChild(title);

    if (isProtected) {
      const hint = document.createElement('div');
      hint.className = 'muted tiny';
      hint.style.marginTop = '4px';
      hint.textContent = 'Primary value for Virtual key is chosen above. You can multi-select additional values here.';
      box.appendChild(hint);
    }

    const grid = document.createElement('div');
    grid.style.display = 'grid';
    grid.style.gridTemplateColumns = 'repeat(3, minmax(0, 1fr))';
    grid.style.gap = '6px';

    const primary = (isProtected ? getVkTagValue(group) : '');

    for (const v of values) {
      const lbl = document.createElement('label');
      lbl.className = 'inline tiny';
      const chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.checked = cur.includes(v);
      chk.onchange = () => {
        if (!selectedTrack) return;
        selectedTrack.tags = selectedTrack.tags || {};
        let arr = selectedTrack.tags[group];
        if (!Array.isArray(arr)) arr = [];
        // Work with strings only
        arr = arr.map(x => String(x));

        const vs = String(v);
        const idx = arr.indexOf(vs);

        if (chk.checked) {
          if (idx < 0) arr.push(vs);
        } else {
          if (idx >= 0) arr.splice(idx, 1);
        }

        // For VK component groups, keep Virtual-key behavior intact:
        // - VK reads ONLY the first value.
        // - If the first value is removed, shift primary to the next available value,
        //   otherwise fall back to 'unk' if it exists.
        if (isProtected) {
          const removedPrimary = (idx === 0 && !chk.checked);
          if (removedPrimary) {
            let newPrimary = (arr.length > 0) ? String(arr[0]) : '';
            if (!newPrimary) {
              const opts = (tag_vocab[group] || []).map(x => String(x));
              if (opts.includes('unk')) {
                newPrimary = 'unk';
                arr = ['unk'];
              }
            }

            // Update the VK select UI to reflect the new primary.
            const selId = VK_SELECT_BY_GROUP[group];
            if (selId && el(selId)) {
              el(selId).value = newPrimary;
            }

            // Rebuild key (preserves extra tag values via setVkTagValue).
            selectedTrack.tags[group] = arr;
            rebuildVirtualKey(false);
            renderTagsEditor();
            renderList();
            return;
          }

          // If this group somehow becomes empty, re-seed with 'unk' (if available).
          if (arr.length === 0) {
            const opts = (tag_vocab[group] || []).map(x => String(x));
            if (opts.includes('unk')) {
              arr = ['unk'];
              const selId = VK_SELECT_BY_GROUP[group];
              if (selId && el(selId)) {
                el(selId).value = 'unk';
              }
              selectedTrack.tags[group] = arr;
              rebuildVirtualKey(false);
              renderTagsEditor();
              renderList();
              return;
            }
          }
        }

        selectedTrack.tags[group] = arr;
        setDirty(true);
        renderList();
      };
      lbl.appendChild(chk);
      const span = document.createElement('span');
      span.textContent = v;
      lbl.appendChild(span);

      if (isProtected && String(v) === primary) {
        const badge = document.createElement('span');
        badge.className = 'muted tiny';
        badge.style.marginLeft = '6px';
        badge.textContent = '(VK primary)';
        lbl.appendChild(badge);
      }

      grid.appendChild(lbl);
    }

    box.appendChild(grid);
    te.appendChild(box);
  }
}

// ----- Missing-track actions
async function locateSelectedTrackFile() {
  if (!selectedTrack) return;
  try {
    const res = await apiJSON(`/api/track/${selectedTrack.track_id}/locate`, { method: 'POST', body: '{}' });
    if (res && res.cancelled) {
      return;
    }
    if (res && res.track) {
      // Merge file-link fields without overwriting unsaved metadata edits
      const t2 = res.track;
      selectedTrack.original_path = t2.original_path;
      selectedTrack.file_format = t2.file_format;
      selectedTrack.raw_file_name = t2.raw_file_name;
      selectedTrack.raw_parent_dir_name = t2.raw_parent_dir_name;
      selectedTrack.fingerprint = t2.fingerprint;
      selectedTrack.length_sec = t2.length_sec;
      selectedTrack.missing_file = t2.missing_file;
      await refreshTracks(false);
      renderList();
      showDetail(selectedTrack);
    }
  } catch (e) {
    alert(`Locate failed: ${e.message}`);
  }
}

async function deleteSelectedTrack() {
  if (!selectedTrack) return;
  if (dirty) {
    const ok = confirm('You have unsaved changes. Removing this track will discard them. Continue?');
    if (!ok) return;
  }
  const ok2 = confirm('Remove this track from the catalog? This does not delete the audio file from disk.');
  if (!ok2) return;
  try {
    await apiJSON(`/api/track/${selectedTrack.track_id}`, { method: 'DELETE' });
    await refreshTracks(true);
    renderList();
  } catch (e) {
    alert(`Remove failed: ${e.message}`);
  }
}

let detailWired = false;
function wireDetailInputs() {
  if (detailWired) return;
  detailWired = true;

  // Virtual key builder (auto mood_context_instrument_style_sn)
  ['vkMoodSelect','vkContextSelect','vkInstrumentSelect','vkStyleSelect'].forEach((id) => {
    const sel = el(id);
    if (!sel) return;
    sel.addEventListener('change', () => {
      if (!selectedTrack) return;
      rebuildVirtualKey(false);
    });
  });
  const regen = el('vkRegenBtn');
  if (regen) {
    regen.addEventListener('click', () => {
      if (!selectedTrack) return;
      rebuildVirtualKey(true);
    });
  }

  // Missing-track actions
  const locateBtn = el('locateBtn');
  if (locateBtn) locateBtn.addEventListener('click', locateSelectedTrackFile);
  const removeBtn = el('removeTrackBtn');
  if (removeBtn) removeBtn.addEventListener('click', deleteSelectedTrack);
  el('primaryRoleSelect').addEventListener('change', (e) => {
    selectedTrack.primary_role = e.target.value;
    setDirty(true);
    renderList();
  });
  el('bpmInput').addEventListener('input', (e) => {
    const v = e.target.value;
    selectedTrack.bpm = v === '' ? null : parseInt(v,10);
    setDirty(true);
  });
  el('notesInput').addEventListener('input', (e) => {
    selectedTrack.notes = e.target.value;
    setDirty(true);
    renderList();
  });

  // Loop info editor
  function updLoop() {
    selectedTrack.loop_info = selectedTrack.loop_info || {};
    selectedTrack.loop_info.can_loop = el('canLoop').checked;
    selectedTrack.loop_info.loop_start_sec = el('loopStart').value === '' ? null : parseFloat(el('loopStart').value);
    selectedTrack.loop_info.loop_end_sec = el('loopEnd').value === '' ? null : parseFloat(el('loopEnd').value);
    selectedTrack.loop_info.intro_sec = el('introSec').value === '' ? null : parseFloat(el('introSec').value);
    selectedTrack.loop_info.outro_sec = el('outroSec').value === '' ? null : parseFloat(el('outroSec').value);
    setDirty(true);
    renderList();
  }
  ['canLoop','loopStart','loopEnd','introSec','outroSec'].forEach(id => {
    el(id).addEventListener('change', updLoop);
    el(id).addEventListener('input', updLoop);
  });

  // Licensing
  function updLic() {
    selectedTrack.licensing = selectedTrack.licensing || {};
    selectedTrack.licensing.source_pack = el('licSource').value;
    selectedTrack.licensing.license_type = el('licType').value;
    selectedTrack.licensing.proof_url_or_file = el('licProof').value;
    selectedTrack.licensing.attribution_required = el('licAttrReq').checked;
    selectedTrack.licensing.attribution_text = el('licAttrText').value;
    setDirty(true);
  }
  ['licSource','licType','licProof','licAttrReq','licAttrText'].forEach(id => {
    el(id).addEventListener('change', updLic);
    el(id).addEventListener('input', updLic);
  });

  // Save button
  el('saveBtn').addEventListener('click', async () => {
    if (!selectedTrack) return;
    try {
      await apiJSON(`/api/track/${selectedTrack.track_id}` , {
        method: 'PUT',
        body: JSON.stringify({ track: selectedTrack })
      });
      setDirty(false);
      // refresh list (virtual key / role / notes might change)
      await refreshTracks(false);
      // reselect in list UI
      renderList();
    } catch (e) {
      alert(`Save failed: ${e.message}`);
    }
  });

  // Rescan
  el('rescanBtn').addEventListener('click', async () => {
    if (dirty) {
      const ok = confirm('You have unsaved changes. Continue rescan and discard them?');
      if (!ok) return;
    }
    try {
      const r = await apiJSON('/api/rescan', { method: 'POST', body: '{}' });
      await refreshTracks(true);
      alert(`Rescan done. New: ${r.summary.new}, Updated: ${r.summary.updated}, Relinked: ${r.summary.relinked}, Duplicates: ${r.summary.duplicates}, Missing: ${r.summary.missing}`);
    } catch (e) {
      alert(`Rescan failed: ${e.message}`);
    }
  });

  // Add tag modal
  el('addTagBtn').addEventListener('click', () => openModal());
  el('modalCancel').addEventListener('click', closeModal);
  el('modalOk').addEventListener('click', async () => {
    const group = el('modalTagGroup').value;
    const value = el('modalTagValue').value.trim();
    if (!value) return;
    try {
      const res = await apiJSON('/api/vocab/add', { method:'POST', body: JSON.stringify({ kind:'tag', group, value }) });
      vocab = res.vocab;
      buildFilterUI();
      if (selectedTrack) {
        // ensure editor sees new value
        renderTagsEditor();
        renderVirtualKeyBuilder();
      }
      closeModal();
    } catch (e) {
      alert(`Add tag failed: ${e.message}`);
    }
  });

  // A/B loop preview
  const player = el('player');
  player.addEventListener('timeupdate', () => {
    if (!el('abLoopToggle').checked) return;
    const a = parseFloat(el('abA').value);
    const b = parseFloat(el('abB').value);
    if (isNaN(a) || isNaN(b) || b <= a) return;
    if (player.currentTime >= b) {
      player.currentTime = a;
    }
  });

  // Keyboard shortcuts
  document.addEventListener('keydown', (ev) => {
    const tag = (ev.target && ev.target.tagName) ? ev.target.tagName.toLowerCase() : '';
    const inInput = ['input','textarea','select'].includes(tag);

    if (ev.ctrlKey && ev.key.toLowerCase() === 's') {
      ev.preventDefault();
      if (!el('saveBtn').disabled) el('saveBtn').click();
      return;
    }

    if (inInput) return;

    if (ev.key === ' ') {
      ev.preventDefault();
      const p = el('player');
      if (p.paused) p.play(); else p.pause();
    }

    if (ev.key === 'ArrowDown') {
      ev.preventDefault();
      navSelect(1);
    }
    if (ev.key === 'ArrowUp') {
      ev.preventDefault();
      navSelect(-1);
    }
  });
}

function openModal() {
  const mg = el('modalTagGroup');
  mg.innerHTML = '';
  const groups = Object.keys(vocab.tag_vocab || {});

  if (groups.length === 0) {
    const o = document.createElement('option');
    o.value = '';
    o.textContent = '(no tag groups — create one in Manage Tags...)';
    mg.appendChild(o);
    el('modalOk').disabled = true;
  } else {
    for (const g of groups) {
      const o = document.createElement('option');
      o.value = g;
      o.textContent = g;
      mg.appendChild(o);
    }
    el('modalOk').disabled = false;
  }

  el('modalTagValue').value = '';
  el('modal').style.display = 'flex';
  el('modalTagValue').focus();
}

function closeModal() {
  el('modal').style.display = 'none';
  el('modalOk').disabled = false;
}

function closeTagAdminModal() {
  el('modal').style.display = 'none';
}

function navSelect(delta) {
  if (filteredIds.length === 0) return;
  if (!selectedId) {
    selectTrack(filteredIds[0]);
    return;
  }
  const idx = filteredIds.indexOf(selectedId);
  const next = Math.max(0, Math.min(filteredIds.length - 1, idx + delta));
  selectTrack(filteredIds[next]);
}

async function refreshTracks(resetSelection) {
  const data = await apiJSON('/api/tracks');
  tracks = data.tracks;
  await refreshClusters();
  if (resetSelection) {
    selectedId = null;
    selectedTrack = null;
    el('detail').style.display = 'none';
    el('emptyState').style.display = 'block';
    setDirty(false);
  }
}


async function refreshClusters() {
  try {
    const data = await apiJSON('/api/clusters');
    clusters = data.clusters || [];
  } catch (e) {
    clusters = [];
  }
}



// ----- Tag admin modal (add/delete tag groups, delete tag values)
function openTagAdminModal() {
  if (dirty) {
    const ok = confirm('You have unsaved changes. Discard them before managing tag vocabulary?');
    if (!ok) return;
    setDirty(false);
  }
  renderTagAdminModal();
  el('tagAdminModal').style.display = 'flex';
  el('tagGroupNewName').focus();
}

function closeTagAdminModal() {
  el('tagAdminModal').style.display = 'none';
}

async function refreshAfterCatalogMutation() {
  await refreshTracks(false);
  renderList();
  if (selectedId) {
    try {
      const data = await apiJSON(`/api/track/${selectedId}`);
      selectedTrack = data.track;
      showDetail(selectedTrack);
      setDirty(false);
    } catch (_) {
      selectedId = null;
      selectedTrack = null;
      el('detail').style.display = 'none';
      el('emptyState').style.display = 'block';
      setDirty(false);
    }
  }
}

function renderTagAdminModal() {
  // Render groups list
  const gl = el('tagGroupList');
  gl.innerHTML = '';
  const groups = Object.keys(vocab.tag_vocab || {}).sort();

  if (groups.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'muted tiny';
    empty.textContent = 'No tag groups yet. Add one above.';
    gl.appendChild(empty);
  } else {
    for (const g of groups) {
      const row = document.createElement('div');
      row.className = 'tag-admin-row';

      const name = document.createElement('div');
      name.textContent = g;
      row.appendChild(name);

      const btn = document.createElement('button');
      btn.className = 'danger';
      btn.type = 'button';
      btn.textContent = 'Delete';
      const isProtected = VK_PROTECTED_GROUPS.has(g);
      if (isProtected) {
        btn.disabled = true;
        btn.className = 'secondary';
        btn.textContent = 'Required';
        const sub = document.createElement('div');
        sub.className = 'sub';
        sub.textContent = 'Used by Virtual key; cannot be deleted.';
        row.appendChild(sub);
      }
      btn.onclick = async () => {
        const ok = confirm(`Delete tag group "${g}"? This removes the group and all its assignments from every track.`);
        if (!ok) return;
        try {
          const res = await apiJSON('/api/vocab/tag_group/delete', {
            method: 'POST',
            body: JSON.stringify({ group: g })
          });
          vocab = res.vocab;
          buildFilterUI();
          await refreshAfterCatalogMutation();
          renderTagAdminModal();
        } catch (e) {
          alert(`Delete group failed: ${e.message}`);
        }
      };
      row.appendChild(btn);

      gl.appendChild(row);
    }
  }

  // Group selector for value deletion
  const vs = el('tagValueGroupSelect');
  const prev = vs.value;
  vs.innerHTML = '';
  if (groups.length === 0) {
    const o = document.createElement('option');
    o.value = '';
    o.textContent = '(no groups)';
    vs.appendChild(o);
  } else {
    for (const g of groups) {
      const o = document.createElement('option');
      o.value = g;
      o.textContent = g;
      vs.appendChild(o);
    }
    if (prev && groups.includes(prev)) {
      vs.value = prev;
    }
  }

  renderTagValueList();
}

function renderTagValueList() {
  const group = el('tagValueGroupSelect').value;
  const list = el('tagValueList');
  list.innerHTML = '';
  if (!group || !(vocab.tag_vocab && vocab.tag_vocab[group])) {
    const empty = document.createElement('div');
    empty.className = 'muted tiny';
    empty.textContent = 'No values to show.';
    list.appendChild(empty);
    return;
  }

  const values = (vocab.tag_vocab[group] || []).slice().sort((a,b) => String(a).localeCompare(String(b)));
  if (values.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'muted tiny';
    empty.textContent = 'This group has no values. Use “Add Tag Value...” from the Tags panel to add values.';
    list.appendChild(empty);
    return;
  }

  for (const v of values) {
    const row = document.createElement('div');
    row.className = 'tag-admin-row';

    const name = document.createElement('div');
    name.textContent = v;
    row.appendChild(name);

    const btn = document.createElement('button');
    btn.className = 'danger';
    btn.type = 'button';
    btn.textContent = 'Delete';
    btn.onclick = async () => {
      const ok = confirm(`Delete tag value "${v}" from group "${group}"? This removes the value from every track.`);
      if (!ok) return;
      try {
        const res = await apiJSON('/api/vocab/tag_value/delete', {
          method: 'POST',
          body: JSON.stringify({ group, value: v })
        });
        vocab = res.vocab;
        buildFilterUI();
        await refreshAfterCatalogMutation();
        renderTagAdminModal();
      } catch (e) {
        alert(`Delete value failed: ${e.message}`);
      }
    };
    row.appendChild(btn);

    list.appendChild(row);
  }
}

async function addTagGroupFromAdmin() {
  const group = el('tagGroupNewName').value.trim();
  if (!group) return;
  try {
    const res = await apiJSON('/api/vocab/tag_group/add', {
      method: 'POST',
      body: JSON.stringify({ group })
    });
    vocab = res.vocab;
    el('tagGroupNewName').value = '';
    buildFilterUI();
    await refreshAfterCatalogMutation();
    renderTagAdminModal();
  } catch (e) {
    alert(`Add group failed: ${e.message}`);
  }
}


// ----- Scale admin modal (add/delete scales, configure min/max/default)
function openScaleAdminModal() {
  if (dirty) {
    const ok = confirm('You have unsaved changes. Discard them before managing scale definitions?');
    if (!ok) return;
    setDirty(false);
  }
  renderScaleAdminModal();
  el('scaleAdminModal').style.display = 'flex';
  el('scaleNewName').focus();
}

function closeScaleAdminModal() {
  el('scaleAdminModal').style.display = 'none';
}

function renderScaleAdminModal() {
  const list = el('scaleList');
  list.innerHTML = '';

  const { defs, names } = getScaleInfo();
  if (!names.length) {
    const empty = document.createElement('div');
    empty.className = 'muted tiny';
    empty.textContent = 'No scales yet. Add one above.';
    list.appendChild(empty);
    return;
  }

  for (const name of names) {
    const d = defs[name] || { min: 0, max: 5, default: 0 };

    const row = document.createElement('div');
    row.className = 'tag-admin-row';

    const left = document.createElement('div');
    left.style.flex = '1';
    left.innerHTML = `<div class="label">${escapeHtml(name)}</div>`;
    row.appendChild(left);

    const controls = document.createElement('div');
    controls.className = 'row';
    controls.style.gap = '8px';

    const minInput = document.createElement('input');
    minInput.type = 'number';
    minInput.step = '1';
    minInput.value = d.min;
    minInput.title = 'min';
    minInput.style.width = '90px';

    const maxInput = document.createElement('input');
    maxInput.type = 'number';
    maxInput.step = '1';
    maxInput.value = d.max;
    maxInput.title = 'max';
    maxInput.style.width = '90px';

    const defInput = document.createElement('input');
    defInput.type = 'number';
    defInput.step = '1';
    defInput.value = d.default != null ? d.default : 0;
    defInput.title = 'default';
    defInput.style.width = '90px';

    const updateBtn = document.createElement('button');
    updateBtn.type = 'button';
    updateBtn.textContent = 'Update';
    updateBtn.onclick = async () => {
      try {
        const res = await apiJSON('/api/vocab/scale/update', {
          method: 'POST',
          body: JSON.stringify({
            name,
            min: parseInt(minInput.value, 10),
            max: parseInt(maxInput.value, 10),
            default: parseInt(defInput.value, 10),
          })
        });
        vocab = res.vocab;
        buildFilterUI();
        await refreshAfterCatalogMutation();
        renderScaleAdminModal();
      } catch (e) {
        alert(`Update scale failed: ${e.message}`);
      }
    };

    const delBtn = document.createElement('button');
    delBtn.className = 'danger';
    delBtn.type = 'button';
    delBtn.textContent = 'Delete';
    delBtn.onclick = async () => {
      const ok = confirm(`Delete scale "${name}"? This removes it from the vocabulary and from all tracks.`);
      if (!ok) return;
      try {
        const res = await apiJSON('/api/vocab/scale/delete', {
          method: 'POST',
          body: JSON.stringify({ name })
        });
        vocab = res.vocab;
        buildFilterUI();
        await refreshAfterCatalogMutation();
        renderScaleAdminModal();
      } catch (e) {
        alert(`Delete scale failed: ${e.message}`);
      }
    };

    controls.appendChild(minInput);
    controls.appendChild(maxInput);
    controls.appendChild(defInput);
    controls.appendChild(updateBtn);
    controls.appendChild(delBtn);
    row.appendChild(controls);

    list.appendChild(row);
  }
}

async function addScaleFromAdmin() {
  const name = el('scaleNewName').value.trim();
  if (!name) return;

  const mn = parseInt(el('scaleNewMin').value, 10);
  const mx = parseInt(el('scaleNewMax').value, 10);
  const dv = parseInt(el('scaleNewDefault').value, 10);

  try {
    const res = await apiJSON('/api/vocab/scale/add', {
      method: 'POST',
      body: JSON.stringify({ name, min: mn, max: mx, default: dv })
    });
    vocab = res.vocab;
    el('scaleNewName').value = '';
    buildFilterUI();
    await refreshAfterCatalogMutation();
    renderScaleAdminModal();
  } catch (e) {
    alert(`Add scale failed: ${e.message}`);
  }
}


// ----- Cluster admin modal (merge clusters)
async function openClusterAdminModal() {
  if (dirty) {
    const ok = confirm('You have unsaved changes. Discard them before managing clusters?');
    if (!ok) return;
    setDirty(false);
  }
  try {
    // Show first (so user gets immediate feedback), then render.
    el('clusterAdminModal').style.display = 'flex';
    await renderClusterAdminModal();
  } catch (e) {
    const hint = el('clusterMergeHint');
    if (hint) hint.textContent = `Failed to load clusters: ${e.message}`;
  }
}

function _getClusterSplitSet(clusterId) {
  if (!clusterId) return new Set();
  if (!clusterSplitSelection[clusterId]) clusterSplitSelection[clusterId] = new Set();
  return clusterSplitSelection[clusterId];
}

function _tracksInCluster(clusterId) {
  if (!clusterId) return [];
  return (tracks || []).filter(t => String(t.cluster_id || '') === String(clusterId));
}

function renderClusterSplitTrackList() {
  const sourceSel = el('clusterSplitSourceSelect');
  const list = el('clusterSplitTrackList');
  const hint = el('clusterSplitCountHint');
  const btn = el('clusterSplitBtn');
  if (!sourceSel || !list) return;

  const cid = sourceSel.value;
  const ctracks = _tracksInCluster(cid);
  const set = _getClusterSplitSet(cid);

  // Drop selections that no longer exist
  const idsNow = new Set(ctracks.map(t => t.track_id));
  for (const x of Array.from(set)) {
    if (!idsNow.has(x)) set.delete(x);
  }

  list.innerHTML = '';
  if (!ctracks.length) {
    const empty = document.createElement('div');
    empty.className = 'muted tiny';
    empty.textContent = 'No tracks in this cluster.';
    list.appendChild(empty);
    if (hint) hint.textContent = '';
    if (btn) btn.disabled = true;
    return;
  }

  // Stable ordering for usability
  const ordered = ctracks.slice().sort((a, b) => {
    const ka = String(a.virtual_key || '');
    const kb = String(b.virtual_key || '');
    const c = ka.localeCompare(kb);
    if (c !== 0) return c;
    return String(a.original_path || '').localeCompare(String(b.original_path || ''));
  });

  for (const t of ordered) {
    const row = document.createElement('label');
    row.className = 'cluster-track-row';
    row.style.cursor = 'pointer';

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = set.has(t.track_id);
    cb.onchange = () => {
      if (cb.checked) set.add(t.track_id);
      else set.delete(t.track_id);
      renderClusterSplitTrackList();
    };

    const txt = document.createElement('div');
    txt.style.flex = '1';
    const vk = escapeHtml(t.virtual_key || '');
    const p = escapeHtml(t.original_path || '');
    txt.innerHTML = `<div><strong>${vk}</strong></div><div class="meta">${p}</div>`;

    row.appendChild(cb);
    row.appendChild(txt);
    list.appendChild(row);
  }

  const selectedCount = set.size;
  const total = ctracks.length;
  if (hint) hint.textContent = `${selectedCount} / ${total} selected`;

  // Must be a strict subset: cannot move 0 or all.
  if (btn) btn.disabled = (selectedCount <= 0 || selectedCount >= total);
}

function clusterSplitSelectAll() {
  const cid = el('clusterSplitSourceSelect') ? el('clusterSplitSourceSelect').value : '';
  const ctracks = _tracksInCluster(cid);
  const set = _getClusterSplitSet(cid);
  set.clear();
  for (const t of ctracks) set.add(t.track_id);
  renderClusterSplitTrackList();
}

function clusterSplitSelectNone() {
  const cid = el('clusterSplitSourceSelect') ? el('clusterSplitSourceSelect').value : '';
  const set = _getClusterSplitSet(cid);
  set.clear();
  renderClusterSplitTrackList();
}

function closeClusterAdminModal() {
  el('clusterAdminModal').style.display = 'none';
}

function clusterLabel(c) {
  const n = c.name || '';
  const cnt = c.track_count != null ? c.track_count : 0;
  return `${n}  (${cnt})`;
}

async function renderClusterAdminModal() {
  await refreshClusters();

  const targetSel = el('clusterTargetSelect');
  const sourceSel = el('clusterSourceSelect');
  const splitSel = el('clusterSplitSourceSelect');
  const splitList = el('clusterSplitTrackList');
  const list = el('clusterList');
  const hint = el('clusterMergeHint');

  if (hint) {
    hint.textContent = 'Merging copies target cluster shared metadata (tags, scales, bpm, licensing) to moved tracks, and regenerates virtual keys to avoid collisions.';
  }

  if (targetSel) targetSel.innerHTML = '';
  if (sourceSel) sourceSel.innerHTML = '';
  if (splitSel) splitSel.innerHTML = '';
  if (list) list.innerHTML = '';
  if (splitList) splitList.innerHTML = '';

  if (!clusters || clusters.length === 0) {
    if (list) {
      const empty = document.createElement('div');
      empty.className = 'muted tiny';
      empty.textContent = 'No clusters found.';
      list.appendChild(empty);
    }
    return;
  }

  // Build selects
  for (const c of clusters) {
    const o1 = document.createElement('option');
    o1.value = c.cluster_id;
    o1.textContent = clusterLabel(c);
    if (targetSel) targetSel.appendChild(o1);

    const o2 = document.createElement('option');
    o2.value = c.cluster_id;
    o2.textContent = clusterLabel(c);
    if (sourceSel) sourceSel.appendChild(o2);

    const o3 = document.createElement('option');
    o3.value = c.cluster_id;
    o3.textContent = clusterLabel(c);
    if (splitSel) splitSel.appendChild(o3);
  }

  // Default: target = current track cluster if available
  if (selectedTrack && selectedTrack.cluster_id && targetSel) {
    targetSel.value = selectedTrack.cluster_id;
  }

  // Default: split source = current track cluster if available
  if (selectedTrack && selectedTrack.cluster_id && splitSel) {
    splitSel.value = selectedTrack.cluster_id;
  }

  // Default source: first different
  if (sourceSel && targetSel) {
    let src = sourceSel.value;
    if (src === targetSel.value) {
      const alt = clusters.find(c => c.cluster_id !== targetSel.value);
      if (alt) sourceSel.value = alt.cluster_id;
    }
  }

  // Ensure split select has a value
  if (splitSel && !splitSel.value && clusters[0]) {
    splitSel.value = clusters[0].cluster_id;
  }

  // Helper: set select value and visually highlight
  function setTarget(id) {
    if (targetSel) targetSel.value = id;
    // Ensure source differs
    if (sourceSel && sourceSel.value === id) {
      const alt = clusters.find(x => x.cluster_id !== id);
      if (alt) sourceSel.value = alt.cluster_id;
    }
    highlightSelected();
  }

  function setSource(id) {
    if (sourceSel) sourceSel.value = id;
    if (targetSel && targetSel.value === id) {
      const alt = clusters.find(x => x.cluster_id !== id);
      if (alt) targetSel.value = alt.cluster_id;
    }
    highlightSelected();
  }

  function highlightSelected() {
    const tgt = targetSel ? targetSel.value : '';
    const rows = list ? Array.from(list.querySelectorAll('.cluster-row')) : [];
    for (const r of rows) {
      r.classList.toggle('selected', r.dataset.clusterId === tgt);
    }
  }

  // Render list (clickable)
  for (const c of clusters) {
    const row = document.createElement('div');
    row.className = 'cluster-row';
    row.dataset.clusterId = c.cluster_id;

    const left = document.createElement('div');
    left.innerHTML = `<div><strong>${escapeHtml(c.name || '')}</strong></div><div class='meta'>${c.track_count} tracks</div>`;

    const actions = document.createElement('div');
    actions.className = 'actions';

    const tgtBtn = document.createElement('button');
    tgtBtn.type = 'button';
    tgtBtn.className = 'secondary';
    tgtBtn.textContent = 'Target';
    tgtBtn.onclick = (ev) => { ev.stopPropagation(); setTarget(c.cluster_id); };

    const srcBtn = document.createElement('button');
    srcBtn.type = 'button';
    srcBtn.className = 'secondary';
    srcBtn.textContent = 'Source';
    srcBtn.onclick = (ev) => { ev.stopPropagation(); setSource(c.cluster_id); };

    const idShort = String(c.cluster_id || '').slice(0, 8);
    const idSpan = document.createElement('span');
    idSpan.className = 'muted tiny';
    idSpan.textContent = idShort;

    actions.appendChild(tgtBtn);
    actions.appendChild(srcBtn);
    actions.appendChild(idSpan);

    row.onclick = () => setTarget(c.cluster_id);

    row.appendChild(left);
    row.appendChild(actions);

    if (list) list.appendChild(row);
  }

  highlightSelected();

  // Split list for currently selected split source
  renderClusterSplitTrackList();
}

async function mergeClustersFromModal() {
  const target = el('clusterTargetSelect').value;
  const source = el('clusterSourceSelect').value;
  if (!target || !source) return;
  if (target === source) {
    alert('Target and source cluster cannot be the same.');
    return;
  }
  const ok = confirm('Merge the source cluster into the target cluster? This will move all tracks and copy shared metadata.');
  if (!ok) return;
  try {
    await apiJSON('/api/clusters/merge', {
      method: 'POST',
      body: JSON.stringify({ target_cluster_id: target, source_cluster_id: source })
    });
    await refreshAfterCatalogMutation();
    await refreshClusters();
    await renderClusterAdminModal();
  } catch (e) {
    alert(`Merge failed: ${e.message}`);
  }
}

async function splitClusterFromModal() {
  const source = el('clusterSplitSourceSelect') ? el('clusterSplitSourceSelect').value : '';
  const name = el('clusterSplitNewName') ? el('clusterSplitNewName').value.trim() : '';
  if (!source) return;

  const ctracks = _tracksInCluster(source);
  const set = _getClusterSplitSet(source);
  const ids = Array.from(set);
  if (!ids.length) {
    alert('Please select at least 1 track to split.');
    return;
  }
  if (ids.length >= ctracks.length) {
    alert('Selection must be a strict subset: leave at least 1 track in the original cluster.');
    return;
  }

  const ok = confirm(`Split ${ids.length} track(s) into a new cluster?`);
  if (!ok) return;

  try {
    await apiJSON('/api/clusters/split', {
      method: 'POST',
      body: JSON.stringify({ source_cluster_id: source, track_ids: ids, new_cluster_name: name })
    });

    // Clear selection for this source cluster (so user doesn't accidentally repeat)
    set.clear();
    if (el('clusterSplitNewName')) el('clusterSplitNewName').value = '';

    await refreshAfterCatalogMutation();
    await refreshClusters();
    await renderClusterAdminModal();
  } catch (e) {
    alert(`Split failed: ${e.message}`);
  }
}

async function init() {
  try {
    const st = await apiJSON('/api/status');
    if (!st.configured) {
      window.location.href = '/setup';
      return;
    }
    vocab = st.vocab;
    el('status').textContent = `${st.raw_music_directory} · ${st.catalog.track_count} tracks`;

    buildFilterUI();

    el('searchInput').addEventListener('input', renderList);
    el('roleFilter').addEventListener('change', renderList);
    el('loopableFilter').addEventListener('change', renderList);
    el('lenMin').addEventListener('input', renderList);
    el('lenMax').addEventListener('input', renderList);

    // Tag admin modal actions
    el('manageTagsBtn').addEventListener('click', openTagAdminModal);
    el('tagAdminCloseBtn').addEventListener('click', closeTagAdminModal);
    el('tagGroupAddBtn').addEventListener('click', addTagGroupFromAdmin);
    el('tagValueGroupSelect').addEventListener('change', renderTagValueList);

    // Scale admin modal actions
    el('manageScalesBtn').addEventListener('click', openScaleAdminModal);
    el('scaleAdminCloseBtn').addEventListener('click', closeScaleAdminModal);
    el('scaleAddBtn').addEventListener('click', addScaleFromAdmin);

    // Cluster admin modal actions
    el('clustersBtn').addEventListener('click', openClusterAdminModal);
    // Allow reselecting RawMusicDirectory + CatalogFile after already configured
    el('changeConfigBtn').addEventListener('click', () => {
      if (dirty) {
        const ok = confirm('You have unsaved changes. Go to Setup and discard them?');
        if (!ok) return;
        setDirty(false);
      }
      window.location.href = '/setup';
    });
    el('clusterAdminCloseBtn').addEventListener('click', closeClusterAdminModal);
    if (el('clusterAdminCloseBtnTop')) {
      el('clusterAdminCloseBtnTop').addEventListener('click', closeClusterAdminModal);
    }
    el('clusterMergeBtn').addEventListener('click', mergeClustersFromModal);

    // Cluster split actions
    if (el('clusterSplitSourceSelect')) {
      el('clusterSplitSourceSelect').addEventListener('change', renderClusterSplitTrackList);
    }
    if (el('clusterSplitSelectAllBtn')) {
      el('clusterSplitSelectAllBtn').addEventListener('click', clusterSplitSelectAll);
    }
    if (el('clusterSplitSelectNoneBtn')) {
      el('clusterSplitSelectNoneBtn').addEventListener('click', clusterSplitSelectNone);
    }
    if (el('clusterSplitBtn')) {
      el('clusterSplitBtn').addEventListener('click', splitClusterFromModal);
    }

    await refreshTracks(true);
    renderList();
  } catch (e) {
    el('status').textContent = `Error: ${e.message}`;
  }
}

init();
