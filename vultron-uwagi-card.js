class VultronUwagiCard extends HTMLElement {
  constructor() { super(); this._sortOrder = null; }
  set hass(hass) {
    this._hass = hass;
    if (this._sortOrder === null) this._sortOrder = this.config.default_sort || 'desc';
    if (!this.content) {
      this.innerHTML = `<ha-card><div style="padding: 16px;"><div id="header-area"></div><div id="vultron-uwagi-body"></div></div></ha-card>`;
      this.content = this.querySelector('#vultron-uwagi-body');
      this.headerArea = this.querySelector('#header-area');
    }
    const state = hass.states[this.config.entity];
    if (!state || !state.attributes.uwagi) return;
    this.renderHeader(state);
    this.renderBody(state);
  }

  renderHeader(state) {
    const childName = (state.attributes.friendly_name || '').replace('Uwagi: ', '');
    this.headerArea.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
        <div style="font-size: 1.1em; font-weight: 500; color: var(--primary-text-color);">Uwagi: ${childName}</div>
        <div style="display: flex; gap: 10px; font-size: 0.8em; font-weight: bold;">
          <span id="sort-desc" style="cursor: pointer; color: ${this._sortOrder === 'desc' ? 'var(--primary-color)' : 'var(--secondary-text-color)'};">NAJNOWSZE</span>
          <span id="sort-asc" style="cursor: pointer; color: ${this._sortOrder === 'asc' ? 'var(--primary-color)' : 'var(--secondary-text-color)'};">NAJSTARSZE</span>
        </div>
      </div>`;
    this.headerArea.querySelector('#sort-desc').addEventListener('click', () => { this._sortOrder = 'desc'; this.hass = this._hass; });
    this.headerArea.querySelector('#sort-asc').addEventListener('click', () => { this._sortOrder = 'asc'; this.hass = this._hass; });
  }

  renderBody(state) {
    let uwagi = [...state.attributes.uwagi];
    uwagi.sort((a, b) => {
      const p = (s) => { const x=s.split('.'); return new Date(x[2], x[1]-1, x[0]); };
      return this._sortOrder === 'desc' ? p(b.data) - p(a.data) : p(a.data) - p(b.data);
    });
    if (this.config.limit && this.config.limit > 0) uwagi = uwagi.slice(0, this.config.limit);
    let html = "";
    uwagi.forEach(u => {
      let color = "#2196F3"; if (u.typ === "pozytywna") color = "#4CAF50"; if (u.typ === "negatywna") color = "#F44336";
      html += `
        <div style="margin-bottom: 10px; padding: 10px; background: var(--secondary-background-color); border-radius: 8px; border-left: 5px solid ${color};">
          <div style="display: flex; justify-content: space-between; font-size: 0.85em; opacity: 0.7; margin-bottom: 4px;">
            <span>${u.data} | <b>${u.kategoria}</b></span>
            <span>${u.punkty ? `Pkt: ${u.punkty}` : ''}</span>
          </div>
          <div style="font-size: 0.95em; line-height: 1.3; margin-bottom: 6px;">${u.tresc}</div>
          <div style="font-size: 0.75em; font-style: italic; text-align: right; opacity: 0.6;">Wystawił: ${u.autor}</div>
        </div>`;
    });
    this.content.innerHTML = html || "Brak wpisów.";
  }
  setConfig(config) { this.config = config; }
}
customElements.define("vultron-uwagi-card", VultronUwagiCard);