class VultronPrzedszkoleNauczycieleCard extends HTMLElement {
  constructor() {
    super();
    this._cachedState = null;
  }

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

    if (!this.config || !this.config.entity) return;

    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <div style="padding: 16px;">
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 14px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
              <ha-icon icon="mdi:account-tie" style="color: var(--primary-color);"></ha-icon>
              <div id="student-name" style="font-size: 1.05em; font-weight: 600;"></div>
            </div>
            <div id="body"></div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#body');
      this.studentLabel = this.querySelector('#student-name');
    }

    const state = this._hass.states[this.config.entity];

    if (this._cachedState === state) {
      return;
    }
    this._cachedState = state;

    this._render(state);
  }

  _render(state) {
    if (!state) {
      this.content.innerHTML = `<div style="text-align:center; padding:20px;">Brak danych o nauczycielach</div>`;
      this.studentLabel.innerText = '';
      return;
    }

    this.studentLabel.innerText = (state.attributes.friendly_name || '').replace('Nauczyciele (przedszkole): ', '');

    const nauczyciele = state.attributes.nauczyciele || [];

    if (nauczyciele.length === 0) {
      this.content.innerHTML = `<div style="text-align:center; padding:16px; opacity:0.7;">Brak danych o nauczycielach</div>`;
      return;
    }

    let html = '';
    nauczyciele.forEach(n => {
      const isWychowawca = n.wychowawca;
      html += `
        <div style="display:flex; align-items:center; gap:10px; padding:9px 0; border-bottom:1px solid var(--divider-color);">
          <ha-icon icon="${isWychowawca ? 'mdi:star-circle' : 'mdi:account'}" style="--mdc-icon-size:20px; color:${isWychowawca ? '#ff9800' : 'var(--secondary-text-color)'};"></ha-icon>
          <div style="flex:1;">
            <div style="font-size:0.92em; font-weight:${isWychowawca ? '700' : '500'};">
              ${this._esc(n.imie)} ${this._esc(n.nazwisko)}
              ${isWychowawca ? '<span style="font-size:0.7em; color:#ff9800; margin-left:6px;">WYCHOWAWCA</span>' : ''}
            </div>
            <div style="font-size:0.78em; opacity:0.7; margin-top:1px;">${this._esc(n.przedmiot)}</div>
          </div>
        </div>`;
    });

    this.content.innerHTML = html;
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Entity missing");
    this.config = config;
  }

  getCardSize() { return 5; }
}

customElements.define("vultron-przedszkole-nauczyciele-card", VultronPrzedszkoleNauczycieleCard);
