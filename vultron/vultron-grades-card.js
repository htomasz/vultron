class VultronGradesCard extends HTMLElement {
  constructor() {
    super();
    this._sortMode = null;
    this._periodMode = null; // null oznacza auto-wykrywanie z encji

    // Zmienne do zapobiegania wyciekom pamięci/CPU (State Caching)
    this._cachedState = null;
    this._cachedSortMode = null;
    this._cachedPeriodMode = null;
  }

  _normalizeDate(dateStr) {
    if (!dateStr || typeof dateStr !== 'string') return '—';
    if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) return dateStr;
    if (/^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}$/.test(dateStr)) return dateStr.split(' ')[0];
    const parts = dateStr.split('.').map(p => p.trim());
    if (parts.length >= 2) {
      const day   = parts[0].padStart(2, '0');
      const month = parts[1].padStart(2, '0');
      let year = parts[2] || new Date().getFullYear().toString();
      if (year.length === 2) year = (parseInt(year, 10) < 70 ? '20' : '19') + year;
      if (year.length === 4) return `${year}-${month}-${day}`;
    }
    return dateStr;
  }

  _getTargetEntity() {
    let baseEntity = this.config.entity;
    if (!this._periodMode) return baseEntity;
    const suffix = baseEntity.endsWith('_p1') ? '_p1' : '_p2';
    return baseEntity.replace(suffix, `_p${this._periodMode}`);
  }

  set hass(hass) {
    this._hass = hass;
    if (this._sortMode === null) this._sortMode = this.config.default_sort || 'date';

    const targetEntity = this._getTargetEntity();
    const newState = hass.states[targetEntity];

    // 1. Inicjalizacja DOM i zdarzeń (Tylko raz!)
    if (!this.content) {
      this.innerHTML = `
        <style>
          .grade-wrapper { position: relative; display: inline-block; cursor: pointer; }
          .vultron-tooltip {
            visibility: hidden; opacity: 0; width: 200px;
            background: var(--ha-card-background, var(--card-background-color, white));
            color: var(--primary-text-color); text-align: left; border-radius: 8px; padding: 10px;
            position: absolute; z-index: 10; bottom: 125%; left: 50%;
            transform: translateX(-50%) translateY(10px);
            box-shadow: 0 10px 20px rgba(0,0,0,0.2); border: 1px solid var(--divider-color);
            transition: all 0.2s ease-in-out; pointer-events: none; backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px); font-size: 0.85em; line-height: 1.4;
          }
          .vultron-tooltip::after {
            content: ""; position: absolute; top: 100%; left: 50%; margin-left: -5px;
            border-width: 5px; border-style: solid; border-color: var(--divider-color) transparent transparent transparent;
          }
          .grade-wrapper:hover .vultron-tooltip { visibility: visible; opacity: 1; transform: translateX(-50%) translateY(0); }
          .latest-grade-box { display: inline-block; background: var(--secondary-background-color); padding: 4px 10px; border-radius: 6px; border: 1px solid var(--divider-color); font-weight: bold; }
          .tooltip-header { font-weight: bold; border-bottom: 1px solid var(--divider-color); margin-bottom: 5px; padding-bottom: 3px; display: block; color: var(--primary-color); }
          .period-tab { cursor: pointer; padding: 2px 6px; border-radius: 4px; margin-right: 5px; font-size: 0.9em; }
          .period-active { background: var(--primary-color); color: white; }
        </style>
        <ha-card>
          <div style="padding: 16px;">
            <!-- NAGŁÓWEK (Tworzony statycznie, modyfikowane tylko klasy i tekst) -->
            <div id="header-area">
              <div style="margin-bottom: 10px; display: flex; justify-content: flex-start;">
                <span id="p-1" class="period-tab" style="border: 1px solid var(--divider-color);">OKRES 1</span>
                <span id="p-2" class="period-tab" style="border: 1px solid var(--divider-color);">OKRES 2</span>
              </div>
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
                <div id="child-name" style="font-size: 1.1em; font-weight: 500; color: var(--primary-text-color);"></div>
                <div style="display: flex; gap: 10px; font-size: 0.8em; font-weight: bold;">
                  <span id="sort-sub" style="cursor: pointer;">PRZEDMIOTY</span>
                  <span id="sort-dat" style="cursor: pointer;">NAJNOWSZE</span>
                </div>
              </div>
            </div>

            <div id="vultron-grades-body"></div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#vultron-grades-body');
      this.headerArea = this.querySelector('#header-area');

      // Podpinanie zdarzeń TYLKO RAZ
      this.querySelector('#p-1').addEventListener('click', () => { this._periodMode = 1; this._forceUpdate(); });
      this.querySelector('#p-2').addEventListener('click', () => { this._periodMode = 2; this._forceUpdate(); });
      this.querySelector('#sort-sub').addEventListener('click', () => { this._sortMode = 'subject'; this._forceUpdate(); });
      this.querySelector('#sort-dat').addEventListener('click', () => { this._sortMode = 'date'; this._forceUpdate(); });
    }

    // 2. Optymalizacja wydajności - przerywamy jeśli nic się nie zmieniło
    if (
      this._cachedState === newState &&
      this._cachedSortMode === this._sortMode &&
      this._cachedPeriodMode === this._periodMode
    ) {
      return;
    }

    // Zapisujemy nowy stan i odświeżamy
    this._cachedState = newState;
    this._cachedSortMode = this._sortMode;
    this._cachedPeriodMode = this._periodMode;

    this.updateView(newState);
  }

  _forceUpdate() {
    // Wymuszamy ponowne pobranie i wyrenderowanie poprzez ponowne wywołanie setter'a
    this.hass = this._hass;
  }

  updateView(state) {
    if (!state || !state.attributes.lista_przedmiotow) {
      this.content.innerHTML = `<div style="padding: 20px; text-align: center;">Brak danych dla wybranego okresu...</div>`;
      this.updateHeader(state);
      return;
    }

    this.updateHeader(state);
    if (this._sortMode === 'subject') this.renderBySubject(state);
    else this.renderByDate(state);
  }

  updateHeader(state) {
    const currentP = state ? state.attributes.period_number : this._periodMode;
    const childName = state && state.attributes.friendly_name ? state.attributes.friendly_name.split('(')[0].replace('Oceny: ', '') : 'Dziecko';

    // Bezpieczne wstawienie tekstu bez XSS
    this.querySelector('#child-name').innerText = childName;

    // Aktualizacja podświetlenia okresów
    this.querySelector('#p-1').classList.toggle('period-active', currentP == 1);
    this.querySelector('#p-2').classList.toggle('period-active', currentP == 2);

    // Aktualizacja kolorów sortowania
    this.querySelector('#sort-sub').style.color = this._sortMode === 'subject' ? 'var(--primary-color)' : 'var(--secondary-text-color)';
    this.querySelector('#sort-dat').style.color = this._sortMode === 'date' ? 'var(--primary-color)' : 'var(--secondary-text-color)';
  }

  getGradeColor(val) {
    let color = "var(--primary-text-color)";
    if (!val) return color;
    const v = String(val).toUpperCase();
    if (/[56AB]/.test(v)) color = "#4CAF50";
    else if (/[12EF]/.test(v)) color = "#F44336";
    else if (/[3CD]/.test(v)) color = "#FF9800";
    else if (v.includes("NB")) color = "#9E9E9E";
    else if (v.includes("%")) color = "#2196F3";
    return color;
  }

  _esc(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  renderBySubject(state) {
    let html = `<table style="width: 100%; border-collapse: collapse;">`;
    state.attributes.lista_przedmiotow.forEach(p => {
      const oceny = p.oceny || [];
      const average = p.srednia;
      const avgHtml = average ? `<div style="font-size: 0.8em; opacity: 0.6; font-weight: normal; margin-top: 2px;">Średnia: ${this._esc(average)}</div>` : '';

      html += `
        <tr style="border-bottom: 1px solid var(--divider-color);">
          <td style="padding: 12px 0; width: 35%; font-weight: 500; color: var(--primary-text-color); vertical-align: top;">
            ${this._esc(p.przedmiot)}
            ${avgHtml}
          </td>
          <td style="padding: 8px 0; display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end;">
            ${oceny.map(o => {
              const color = this.getGradeColor(o.w);
              return `
                <div class="grade-wrapper">
                  <div style="background: var(--secondary-background-color); border: 1px solid var(--divider-color); border-radius: 6px; padding: 4px 8px; text-align: center; min-width: 40px;">
                    <div style="font-weight: bold; color: ${color}; font-size: 1.1em;">${this._esc(o.w)}</div>
                    <div style="font-size: 0.65em; opacity: 0.6; margin-top: -2px;">${this._esc(o.d)}</div>
                  </div>
                  <div class="vultron-tooltip">
                    <span class="tooltip-header">${this._esc(p.przedmiot)}</span>
                    ${this._esc(o.i)}
                  </div>
                </div>`;
            }).join('')}
          </td>
        </tr>`;
    });
    this.content.innerHTML = html + `</table>`;
  }

  renderByDate(state) {
    let allGrades = [];
    state.attributes.lista_przedmiotow.forEach(p => {
      (p.oceny || []).forEach(o => {
        let sortKey = 0;
        if (o.d && o.d.includes('.')) {
          const [d, m] = o.d.split('.').map(Number);
          sortKey = (m < 9 ? m + 12 : m) * 100 + d;
        }
        allGrades.push({ przedmiot: p.przedmiot, val: o.w, date: o.d, info: o.i, sortKey: sortKey });
      });
    });

    allGrades.sort((a, b) => b.sortKey - a.sortKey);
    const limit = parseInt(this.config.limit) || 0;
    const gradesToDisplay = (limit > 0) ? allGrades.slice(0, limit) : allGrades;

    let html = `<table style="width: 100%; border-collapse: collapse;">`;
    gradesToDisplay.forEach(g => {
      const color = this.getGradeColor(g.val);
      const displayDate = this._normalizeDate(g.date);

      html += `
        <tr style="border-bottom: 1px solid var(--divider-color);">
          <td style="padding: 10px 0; width: 35%; vertical-align: middle;">
            <div style="display: flex; justify-content: space-between; align-items: center; gap: 12px;">
              <div style="font-size: 1.1em; font-weight: 500; color: var(--primary-text-color); flex: 1;">
                ${this._esc(g.przedmiot)}
              </div>
              <span style="font-weight: bold; color: var(--primary-color); background: var(--secondary-background-color); padding: 2px 6px; border-radius: 6px; font-size: 0.78em; white-space: nowrap;">
                ${this._esc(displayDate)}
              </span>
            </div>
          </td>
          <td style="padding: 10px 0; text-align: right;">
            <div class="grade-wrapper">
              <span class="latest-grade-box" style="color: ${color};">${this._esc(g.val)}</span>
              <div class="vultron-tooltip" style="bottom: 100%; right: 0; left: auto; transform: translateY(-10px);">
                <span class="tooltip-header">${this._esc(g.przedmiot)}</span>
                ${this._esc(g.info)}
              </div>
            </div>
          </td>
        </tr>`;
    });
    this.content.innerHTML = html + `</table>`;
  }

  setConfig(config) { if (!config.entity) throw new Error("Entity missing"); this.config = config; }
  getCardSize() { return 8; }
}
customElements.define("vultron-grades-card", VultronGradesCard);