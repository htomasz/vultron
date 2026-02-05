class VultronGradesCard extends HTMLElement {
  constructor() {
    super();
    this._sortMode = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._sortMode === null) this._sortMode = this.config.default_sort || 'date';
    if (!this.content) {
      this.innerHTML = `
        <style>
          /* Styl kontenera tooltipa */
          .grade-wrapper {
            position: relative;
            display: inline-block;
            cursor: pointer;
          }

          /* Styl samej chmurki */
          .vultron-tooltip {
            visibility: hidden;
            opacity: 0;
            width: 200px;
            background: var(--ha-card-background, var(--card-background-color, white));
            color: var(--primary-text-color);
            text-align: left;
            border-radius: 8px;
            padding: 10px;
            position: absolute;
            z-index: 10;
            bottom: 125%; /* Pojawia się nad oceną */
            left: 50%;
            transform: translateX(-50%) translateY(10px);
            box-shadow: 0 10px 20px rgba(0,0,0,0.2);
            border: 1px solid var(--divider-color);
            transition: all 0.2s ease-in-out;
            pointer-events: none;
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            font-size: 0.85em;
            line-height: 1.4;
          }

          /* Trójkącik pod chmurką */
          .vultron-tooltip::after {
            content: "";
            position: absolute;
            top: 100%;
            left: 50%;
            margin-left: -5px;
            border-width: 5px;
            border-style: solid;
            border-color: var(--divider-color) transparent transparent transparent;
          }

          /* Pokazywanie po najechaniu */
          .grade-wrapper:hover .vultron-tooltip {
            visibility: visible;
            opacity: 1;
            transform: translateX(-50%) translateY(0);
          }

          /* Styl dla widoku "Najnowsze" (tekstowe oceny) */
          .latest-grade-box {
            display: inline-block;
            background: var(--secondary-background-color);
            padding: 4px 10px;
            border-radius: 6px;
            border: 1px solid var(--divider-color);
            font-weight: bold;
          }

          .tooltip-header {
            font-weight: bold;
            border-bottom: 1px solid var(--divider-color);
            margin-bottom: 5px;
            padding-bottom: 3px;
            display: block;
            color: var(--primary-color);
          }
        </style>
        <ha-card>
          <div style="padding: 16px;">
            <div id="header-area"></div>
            <div id="vultron-grades-body"></div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#vultron-grades-body');
      this.headerArea = this.querySelector('#header-area');
    }

    const entityId = this.config.entity;
    const state = hass.states[entityId];

    if (!state || !state.attributes.lista_przedmiotow) {
      this.content.innerHTML = `<div style="padding: 20px; text-align: center;">Oczekiwanie na dane...</div>`;
      return;
    }

    this.renderHeader(state);
    if (this._sortMode === 'subject') this.renderBySubject(state); else this.renderByDate(state);
  }

  renderHeader(state) {
    const childName = state.attributes.friendly_name ? state.attributes.friendly_name.replace('Oceny: ', '') : 'Dziecko';
    this.headerArea.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
        <div style="font-size: 1.1em; font-weight: 500; color: var(--primary-text-color);">${childName}</div>
        <div style="display: flex; gap: 10px; font-size: 0.8em; font-weight: bold;">
          <span id="sort-sub" style="cursor: pointer; color: ${this._sortMode === 'subject' ? 'var(--primary-color)' : 'var(--secondary-text-color)'};">PRZEDMIOTY</span>
          <span id="sort-dat" style="cursor: pointer; color: ${this._sortMode === 'date' ? 'var(--primary-color)' : 'var(--secondary-text-color)'};">NAJNOWSZE</span>
        </div>
      </div>
    `;
    this.headerArea.querySelector('#sort-sub').addEventListener('click', () => { this._sortMode = 'subject'; this.hass = this._hass; });
    this.headerArea.querySelector('#sort-dat').addEventListener('click', () => { this._sortMode = 'date'; this.hass = this._hass; });
  }

  getGradeColor(val) {
    let color = "var(--primary-text-color)";
    if (!val) return color;
    if ("56".includes(val[0])) color = "#4CAF50";
    if ("12".includes(val[0])) color = "#F44336";
    if ("3".includes(val[0])) color = "#FF9800";
    return color;
  }

  renderBySubject(state) {
    let html = `<table style="width: 100%; border-collapse: collapse;">`;
    state.attributes.lista_przedmiotow.forEach(p => {
      const oceny = p.oceny || [];
      html += `
        <tr style="border-bottom: 1px solid var(--divider-color);">
          <td style="padding: 12px 0; width: 35%; font-weight: 500; color: var(--primary-text-color); vertical-align: top;">${p.przedmiot}</td>
          <td style="padding: 8px 0; display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end;">
            ${oceny.map(o => {
              const color = this.getGradeColor(o.w);
              return `
                <div class="grade-wrapper">
                  <div style="background: var(--secondary-background-color); border: 1px solid var(--divider-color); border-radius: 6px; padding: 4px 8px; text-align: center; min-width: 40px;">
                    <div style="font-weight: bold; color: ${color}; font-size: 1.1em;">${o.w}</div>
                    <div style="font-size: 0.65em; opacity: 0.6; margin-top: -2px;">${o.d}</div>
                  </div>
                  <div class="vultron-tooltip">
                    <span class="tooltip-header">${p.przedmiot}</span>
                    ${o.i}
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
      html += `
        <tr style="border-bottom: 1px solid var(--divider-color);">
          <td style="padding: 8px 0; font-size: 0.9em; color: var(--secondary-text-color); width: 20%;">${g.date}</td>
          <td style="padding: 8px 0; font-weight: 500; color: var(--primary-text-color);">${g.przedmiot}</td>
          <td style="padding: 8px 0; text-align: right;">
            <div class="grade-wrapper">
              <span class="latest-grade-box" style="color: ${color};">${g.val}</span>
              <div class="vultron-tooltip" style="bottom: 100%; right: 0; left: auto; transform: translateY(-10px);">
                <span class="tooltip-header">${g.przedmiot}</span>
                ${g.info}
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
