class VultronUwagiCard extends HTMLElement {
  constructor() {
    super();
    this._sortOrder = null; // Inicjalizacja jako null, aby sprawdzić config
  }

  set hass(hass) {
    this._hass = hass;
    
    // Pobranie domyślnego sortowania z configu (domyślnie 'desc' - najnowsze)
    if (this._sortOrder === null) {
      this._sortOrder = this.config.default_sort || 'desc';
    }

    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <div style="padding: 16px;">
            <div id="header-area"></div>
            <div id="vultron-uwagi-body"></div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#vultron-uwagi-body');
      this.headerArea = this.querySelector('#header-area');
    }

    const state = hass.states[this.config.entity];
    if (!state || !state.attributes.uwagi) {
      this.content.innerHTML = "Brak danych o uwagach.";
      return;
    }

    this.renderHeader(state);
    this.renderBody(state);
  }

  renderHeader(state) {
    const childName = state.attributes.friendly_name ? state.attributes.friendly_name.replace('Uwagi: ', '') : 'Dziecko';
    
    this.headerArea.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 2px solid var(--primary-color); padding-bottom: 5px;">
        <div style="font-size: 1.1em; font-weight: 500; color: var(--primary-text-color);">Uwagi: ${childName}</div>
        <div style="display: flex; gap: 10px; font-size: 0.8em; font-weight: bold;">
          <span id="sort-desc" style="cursor: pointer; color: ${this._sortOrder === 'desc' ? 'var(--primary-color)' : 'var(--secondary-text-color)'};">NAJNOWSZE</span>
          <span id="sort-asc" style="cursor: pointer; color: ${this._sortOrder === 'asc' ? 'var(--primary-color)' : 'var(--secondary-text-color)'};">NAJSTARSZE</span>
        </div>
      </div>
    `;

    this.headerArea.querySelector('#sort-desc').addEventListener('click', () => { 
      this._sortOrder = 'desc'; 
      this.hass = this._hass; 
    });
    this.headerArea.querySelector('#sort-asc').addEventListener('click', () => { 
      this._sortOrder = 'asc'; 
      this.hass = this._hass; 
    });
  }

  renderBody(state) {
    let uwagi = [...state.attributes.uwagi];

    // --- SORTOWANIE PO DACIE ---
    uwagi.sort((a, b) => {
      const dateA = this._parseDate(a.data);
      const dateB = this._parseDate(b.data);
      return this._sortOrder === 'desc' ? dateB - dateA : dateA - dateB;
    });

    // --- LOGIKA LIMITU ---
    if (this.config.limit && this.config.limit > 0) {
      uwagi = uwagi.slice(0, this.config.limit);
    }

    let html = "";
    uwagi.forEach(u => {
      let color = "#2196F3"; // info
      if (u.typ === "pozytywna") color = "#4CAF50"; // pochwała
      if (u.typ === "negatywna") color = "#F44336"; // uwaga

      html += `
        <div style="margin-bottom: 10px; padding: 10px; background: var(--secondary-background-color); border-radius: 8px; border-left: 5px solid ${color};">
          <div style="display: flex; justify-content: space-between; font-size: 0.85em; opacity: 0.7; margin-bottom: 4px;">
            <span>${u.data} | <b>${u.kategoria}</b></span>
            <span>${u.punkty ? `Pkt: ${u.punkty}` : ''}</span>
          </div>
          <div style="font-size: 0.95em; line-height: 1.3; margin-bottom: 6px;">${u.tresc}</div>
          <div style="font-size: 0.75em; font-style: italic; text-align: right; opacity: 0.6;">
            Wystawił: ${u.autor}
          </div>
        </div>
      `;
    });

    this.content.innerHTML = html || "Brak wpisów w dzienniku.";
  }

  _parseDate(dateStr) {
    if (!dateStr) return new Date(0);
    const parts = dateStr.split('.');
    if (parts.length === 3) {
      return new Date(parts[2], parts[1] - 1, parts[0]);
    }
    return new Date(dateStr);
  }

  setConfig(config) { 
    if (!config.entity) throw new Error("Musisz zdefiniować encję (entity)");
    this.config = config; 
  }

  getCardSize() { return 6; }
}

customElements.define("vultron-uwagi-card", VultronUwagiCard);
