class VultronStatsCard extends HTMLElement {
  constructor() {
    super();
    // Cache stanu, aby zapobiegać Render Leak
    this._cachedIndexState = null;
    this._cachedSubState = null;
  }

  // Zabezpieczenie przed XSS
  _esc(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  set hass(hass) {
    this._hass = hass;
    const state = hass.states[this.config.entity];
    if (!state || !state.attributes || !state.attributes.rows) return;

    // 1. Inicjalizacja DOM i zdarzeń TYLKO RAZ
    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <div style="padding: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
              <div>
                <div id="student-display-name" style="font-size: 1.1em; font-weight: 500; color: var(--primary-text-color);"></div>
                <div style="font-size: 0.85em; color: var(--secondary-text-color); text-transform: uppercase;">Statystyki frekwencji</div>
              </div>
              <div style="font-size: 1.2em; font-weight: bold; color: var(--primary-color);"><span id="perc-header">0</span>%</div>
            </div>

            <div style="margin-bottom: 12px;">
              <select id="subject-select" style="width:100%; padding:6px 8px; border-radius:6px; border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color); font-size:0.9em;"></select>
            </div>

            <div style="display:flex; flex-wrap:wrap; align-items:center; gap:20px;">
              <div style="flex:1; min-width:160px; text-align:center; position:relative;">
                 <svg viewBox="0 0 36 36" style="width:140px; height:140px; transform:rotate(-90deg);">
                   <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="var(--divider-color)" stroke-width="2.5" />
                   <path id="arc" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="var(--primary-color)" stroke-width="2.5" stroke-dasharray="0, 100" stroke-linecap="round" />
                 </svg>
                 <div style="position:absolute; top:50%; left:50%; transform:translate(-50%, -50%); text-align:center;">
                   <div style="font-size:1.8em; font-weight:bold;"><span id="perc">0</span>%</div>
                   <div style="font-size:0.6em; opacity:0.6; line-height:1; text-transform: uppercase;">Od początku roku</div>
                 </div>
              </div>
              <div style="flex:4; overflow-x:auto;">
                <table style="width:100%; border-collapse:collapse; font-size:0.95em; text-align:right;">
                  <thead><tr id="h-row"></tr></thead>
                  <tbody id="b-rows"></tbody>
                </table>
              </div>
            </div>
          </div>
        </ha-card>`;

      this.content = this.querySelector('#b-rows');
      this.studentDisplayName = this.querySelector('#student-display-name');
      this._subjectSelect = this.querySelector('#subject-select');

      // Podpinanie listenera raz - wymusza przerysowanie po zmianie przedmiotu
      this._subjectSelect.addEventListener('change', () => {
        this._renderCurrent(true);
      });
    }

    this._indexState = state;

    // 2. Optymalizacja wydajności (State Caching)
    // Pobieramy stan sub-encji na podstawie wybranego przedmiotu
    const selectedId = this._subjectSelect.value ? parseInt(this._subjectSelect.value, 10) : -1;
    let subState = null;

    if (selectedId !== -1 && !isNaN(selectedId)) {
      const przedmioty = this._indexState.attributes.przedmioty || [];
      const found = przedmioty.find(p => p.id === selectedId);
      if (found) {
        const baseSlug = this.config.entity.replace('sensor.vultron_stats_', '');
        const subEntityId = `sensor.vultron_stats_${baseSlug}_${this._slugify(found.nazwa)}`;
        subState = this._hass.states[subEntityId];
      }
    }

    // Przerwij, jeśli nic się nie zmieniło (ani główna encja, ani sub-encja)
    if (this._cachedIndexState === state && this._cachedSubState === subState) {
      return;
    }

    this._cachedIndexState = state;
    this._cachedSubState = subState;

    // 3. Renderowanie
    this._updateSubjectOptions(state);
    this._renderCurrent(false);
  }

  _slugify(nazwa) {
    return nazwa
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/\u0142/g, 'l')
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '');
  }

  _updateSubjectOptions(state) {
    const przedmioty = state.attributes.przedmioty || [];
    if (!przedmioty.length) return;

    const sel = this._subjectSelect;
    const currentVal = sel.value;

    // ZABEZPIECZENIE XSS: p.nazwa przepuszczone przez _esc()
    const newOptions = przedmioty.map(p =>
      `<option value="${p.id}">${this._esc(p.nazwa)}</option>`
    ).join('');

    if (sel.innerHTML !== newOptions) {
      sel.innerHTML = newOptions;

      if (currentVal && Array.from(sel.options).some(opt => opt.value === currentVal)) {
        sel.value = currentVal;
      } else if (sel.options.length > 0) {
        sel.value = sel.options[0].value;
      }
    }
  }

  _renderCurrent(forceRecheck = false) {
    const selectedId = parseInt(this._subjectSelect.value, 10);

    if (selectedId === -1 || isNaN(selectedId)) {
      this._render(this._indexState);
      return;
    }

    const przedmioty = this._indexState.attributes.przedmioty || [];
    const found = przedmioty.find(p => p.id === selectedId);
    if (!found) return;

    const baseSlug = this.config.entity.replace('sensor.vultron_stats_', '');
    const subEntityId = `sensor.vultron_stats_${baseSlug}_${this._slugify(found.nazwa)}`;
    const subState = this._hass.states[subEntityId];

    if (subState) {
      if (forceRecheck) this._cachedSubState = subState;
      this._render(subState);
    } else {
      this._render(this._indexState); // Fallback do ogólnych statystyk
    }
  }

  _render(state) {
    if (!state || !state.attributes.rows) return;

    // Użycie innerText do nazwy ucznia chroni przed XSS natywnie
    this.studentDisplayName.innerText = (this._indexState.attributes.friendly_name || '').replace('Statystyki: ', '');

    this.querySelector('#arc').setAttribute('stroke-dasharray', `${state.state}, 100`);
    this.querySelector('#perc').innerText = state.state;
    this.querySelector('#perc-header').innerText = state.state;

    const mKeys   = [9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8];
    const mLabels = ["IX","X","XI","XII","I","II","III","IV","V","VI","VII","VIII"];

    this.querySelector('#h-row').innerHTML =
      `<th style="text-align:left; padding-bottom:8px;">Kategoria</th>` +
      mLabels.map(m => `<th style="padding:0 3px;">${m}</th>`).join('') +
      `<th style="padding:0 5px;">S1</th><th style="padding:0 5px;">S2</th><th style="padding:0 5px; font-weight:bold;">Razem</th>`;

    // ZABEZPIECZENIE XSS: r.k przepuszczone przez _esc()
    this.content.innerHTML = state.attributes.rows.map(r => `
      <tr style="border-top:1px solid var(--divider-color);">
        <td style="text-align:left; padding:8px 6px 8px 0; font-weight:500; color:var(--primary-color);">${this._esc(r.k)}</td>
        ${mKeys.map(m => `<td style="opacity:${r.m[m] ? 1 : 0.3};">${r.m[m] || 0}</td>`).join('')}
        <td style="padding:0 5px;">${r.s1 || 0}</td>
        <td style="padding:0 5px;">${r.s2 || 0}</td>
        <td style="padding:0 5px; font-weight:bold;">${r.r || 0}</td>
      </tr>`).join('');
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Entity missing");
    this.config = config;
  }
}

customElements.define("vultron-stats-card", VultronStatsCard);