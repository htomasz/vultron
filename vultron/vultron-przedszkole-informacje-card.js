class VultronPrzedszkoleInformacjeCard extends HTMLElement {
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
            <div id="header" style="display: flex; align-items: center; gap: 8px; margin-bottom: 14px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
              <ha-icon icon="mdi:school" style="color: var(--primary-color);"></ha-icon>
              <div id="placowka-name" style="font-size: 1.05em; font-weight: 600;"></div>
            </div>
            <div id="body"></div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#body');
      this.nameLabel = this.querySelector('#placowka-name');
    }

    const state = this._hass.states[this.config.entity];

    if (this._cachedState === state) {
      return;
    }
    this._cachedState = state;

    this._render(state);
  }

  _row(icon, label, value) {
    if (!value) return '';
    return `
      <div style="display:flex; align-items:flex-start; gap:10px; padding:7px 0; border-bottom:1px solid var(--divider-color);">
        <ha-icon icon="${icon}" style="--mdc-icon-size:18px; opacity:0.6; margin-top:1px;"></ha-icon>
        <div>
          <div style="font-size:0.68em; opacity:0.6; text-transform:uppercase; letter-spacing:0.5px;">${label}</div>
          <div style="font-size:0.9em;">${this._esc(value)}</div>
        </div>
      </div>`;
  }

  _render(state) {
    if (!state) {
      this.content.innerHTML = `<div style="text-align:center; padding:20px;">Brak danych o placówce</div>`;
      this.nameLabel.innerText = '';
      return;
    }

    const a = state.attributes;
    this.nameLabel.innerText = state.state || 'Placówka';

    let html = '';
    html += this._row('mdi:map-marker', 'Adres', a.adres);
    html += this._row('mdi:account-tie', 'Dyrektor', a.dyrektor);
    html += this._row('mdi:phone', 'Telefon służbowy', a.tel_sluzbowy);
    html += this._row('mdi:cellphone', 'Telefon komórkowy', a.tel_komorkowy);
    html += this._row('mdi:phone-classic', 'Telefon', a.tel_domowy);
    html += this._row('mdi:email', 'E-mail', a.mail);
    html += this._row('mdi:web', 'Strona WWW', a.strona_www);

    this.content.innerHTML = html || `<div style="text-align:center; padding:16px; opacity:0.7;">Brak dodatkowych danych kontaktowych</div>`;
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Entity missing");
    this.config = config;
  }

  getCardSize() { return 4; }
}

customElements.define("vultron-przedszkole-informacje-card", VultronPrzedszkoleInformacjeCard);
