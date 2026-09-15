class VultronPrzedszkoleOplatyCard extends HTMLElement {
  constructor() {
    super();
    this._cachedState = null;
  }

  // Zabezpieczenie przed atakami XSS - dane pochodzą z zewnętrznego API
  // (Vulcan), traktujemy je tak samo jak w pozostałych kartach dodatku.
  _esc(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  _formatKwota(kwota) {
    const n = Number(kwota) || 0;
    return n.toFixed(2).replace('.', ',') + ' zł';
  }

  set hass(hass) {
    this._hass = hass;

    if (!this.config || !this.config.entity) return;

    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <div style="padding: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
              <div id="student-name" style="font-size: 1.1em; font-weight: 500; color: #00bcd4;"></div>
            </div>

            <div id="summary" style="text-align: center; margin-bottom: 16px;">
              <div style="font-size: 0.75em; opacity: 0.7; text-transform: uppercase; letter-spacing: 1px;">Do zapłaty łącznie</div>
              <div id="summary-kwota" style="font-size: 1.8em; font-weight: bold; color: var(--primary-color); margin-top: 2px;"></div>
            </div>

            <div id="konta-list"></div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#konta-list');
      this.studentLabel = this.querySelector('#student-name');
      this.summaryKwota = this.querySelector('#summary-kwota');
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
      this.content.innerHTML = `<div style="text-align:center; padding:20px;">Brak danych o opłatach</div>`;
      this.summaryKwota.innerText = '—';
      return;
    }

    this.studentLabel.innerText = (state.attributes.friendly_name || '').replace('Opłaty (przedszkole): ', '');
    this.summaryKwota.innerText = this._formatKwota(state.state);

    const konta = state.attributes.konta || [];

    if (konta.length === 0) {
      this.content.innerHTML = `<div style="text-align:center; padding:16px; opacity:0.7;">Brak aktywnych zobowiązań</div>`;
      return;
    }

    let html = '';
    konta.forEach(k => {
      const doZaplaty = Number(k.kwota_do_zaplaty) || 0;
      const jestOplacone = doZaplaty === 0;
      const kolorKwoty = jestOplacone ? '#4caf50' : '#f44336';

      const dodatkowe = [];
      if (Number(k.kwota_upomnien) > 0) dodatkowe.push(`Upomnienia: ${this._formatKwota(k.kwota_upomnien)}`);
      if (Number(k.kwota_odsetek) > 0) dodatkowe.push(`Odsetki: ${this._formatKwota(k.kwota_odsetek)}`);
      if (Number(k.kwota_umorzenia) > 0) dodatkowe.push(`Umorzenie: ${this._formatKwota(k.kwota_umorzenia)}`);

      html += `
        <div style="border: 1px solid var(--divider-color); border-radius: 8px; padding: 12px 14px; margin-bottom: 8px;">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <div>
              <div style="font-size: 0.95em; font-weight: 600; color: ${kolorKwoty};">
                ${jestOplacone ? '✓ Opłacone' : this._formatKwota(doZaplaty)}
              </div>
              <div style="font-size: 0.72em; opacity: 0.6; margin-top: 2px;">
                Status: ${this._esc(k.status_platnosci)} · Sync: ${this._esc(k.data_synchronizacji || '—')}
              </div>
            </div>
            ${k.aktywne ? '' : '<span style="font-size:0.7em; opacity:0.5; font-style:italic;">nieaktywne</span>'}
          </div>
          ${dodatkowe.length > 0 ? `
            <div style="margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--divider-color); font-size: 0.78em; opacity: 0.8;">
              ${dodatkowe.map(d => this._esc(d)).join('<br>')}
            </div>
          ` : ''}
        </div>`;
    });

    this.content.innerHTML = html;
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Entity missing");
    this.config = config;
  }

  getCardSize() { return 4; }
}

customElements.define("vultron-przedszkole-oplaty-card", VultronPrzedszkoleOplatyCard);
