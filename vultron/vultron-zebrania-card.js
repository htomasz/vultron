class VultronZebraniaCard extends HTMLElement {
  constructor() {
    super();
    this._uid = 'vz-' + Math.random().toString(36).substr(2, 9);
    this._sortOrder = null;
    this._cachedSignature = null;
    this._currentZebrania = [];
    this._hass = null;
  }

  _esc(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  _safeUrl(url) {
    if (!url || typeof url !== 'string') return '#';
    const trimmed = url.trim();
    if (/^https?:\/\//i.test(trimmed)) return this._esc(trimmed);
    return '#';
  }

  set hass(hass) {
    this._hass = hass;

    if (this._sortOrder === null) {
      this._sortOrder = this.config?.default_sort || 'desc';
    }

    if (!this._initialized) {
      this._initializeDOM();
      this._initialized = true;
    }

    const state = hass?.states?.[this.config?.entity];
    const signature = state ? JSON.stringify(state.attributes?.zebrania || []) : 'null';

    if (signature === this._cachedSignature && this._sortOrder === this._lastSortOrder) {
      return;
    }

    this._cachedSignature = signature;
    this._lastSortOrder = this._sortOrder;

    if (!state || !Array.isArray(state.attributes?.zebrania)) {
      this.content.innerHTML = `<div style="text-align:center;padding:25px;opacity:0.6;">Brak danych o zebraniach.</div>`;
      return;
    }

    this.renderHeader(state);
    this.renderBody(state);
  }

  _initializeDOM() {
    this.innerHTML = `
      <ha-card>
        <style>
          .zebranie-item {
            margin-bottom: 10px;
            padding: 12px 14px;
            background: var(--card-background-color);
            border-radius: 8px;
            cursor: pointer;
            transition: background 0.2s;
            border: 1px solid var(--divider-color);
            border-left: 5px solid #FF9800;
            user-select: none;
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            position: relative;
          }
          .zebranie-item.future { border-left: 5px solid #4CAF50; }
          .zebranie-item:hover { background: var(--secondary-background-color); }
          .chevron { color: var(--divider-color); margin-top: 6px; flex-shrink: 0; }
          #${this._uid}-modal-overlay {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.75);
            z-index: 10000;
            align-items: center;
            justify-content: center;
            backdrop-filter: blur(4px);
          }
          #${this._uid}-modal-content {
            background: var(--ha-card-background, var(--card-background-color));
            width: 90%;
            max-width: 520px;
            max-height: 88vh;
            border-radius: 12px;
            padding: 20px;
            overflow-y: auto;
            box-shadow: 0 10px 30px rgba(0,0,0,0.6);
            border: 1px solid var(--divider-color);
          }
          .modal-header {
            border-bottom: 1px solid var(--divider-color);
            margin-bottom: 15px;
            padding-bottom: 10px;
          }
          .modal-title {
            font-size: 16px;
            font-weight: bold;
            color: var(--primary-color);
            display: flex;
            align-items: center;
            gap: 8px;
          }
          .header-title {
            display: flex;
            align-items: center;
            gap: 8px;
          }
        </style>

        <div style="padding:16px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;border-bottom:2px solid var(--primary-color);padding-bottom:8px">
            <div id="${this._uid}-student-name" class="header-title" style="font-size:1.1em;font-weight:500;color:var(--primary-text-color)">
              <ha-icon icon="mdi:account-group"></ha-icon>
              Zebrania
            </div>
            <div style="display:flex;gap:12px;font-size:0.8em;font-weight:bold">
              <span id="${this._uid}-sort-desc" style="cursor:pointer">NAJNOWSZE</span>
              <span id="${this._uid}-sort-asc" style="cursor:pointer">NAJSTARSZE</span>
            </div>
          </div>
          <div id="${this._uid}-body"></div>
        </div>

        <div id="${this._uid}-modal-overlay">
          <div id="${this._uid}-modal-content">
            <div style="float:right;cursor:pointer;padding:5px;color:var(--secondary-text-color)" id="${this._uid}-modal-close">
              <ha-icon icon="mdi:close"></ha-icon>
            </div>
            <div class="modal-header">
              <div class="modal-title" id="${this._uid}-m-title">
                <ha-icon icon="mdi:account-group"></ha-icon>
                Zebranie z rodzicami
              </div>
              <div id="${this._uid}-m-subtitle" style="font-size:13px;color:var(--secondary-text-color);margin-top:4px"></div>
            </div>
            <div id="${this._uid}-m-body" style="line-height:1.6;font-size:15px;color:var(--primary-text-color);white-space:pre-wrap"></div>
            <div id="${this._uid}-m-online" style="margin-top:15px;font-weight:bold"></div>
            <div style="margin-top:25px;text-align:center">
              <mwc-button raised id="${this._uid}-btn-close">Zamknij</mwc-button>
            </div>
          </div>
        </div>
      </ha-card>
    `;

    this.content = this.querySelector(`#${this._uid}-body`);

    // Eventy sortowania
    this.querySelector(`#${this._uid}-sort-desc`).addEventListener('click', () => {
      this._sortOrder = 'desc';
      this._forceUpdate();
    });

    this.querySelector(`#${this._uid}-sort-asc`).addEventListener('click', () => {
      this._sortOrder = 'asc';
      this._forceUpdate();
    });

    // Zamykanie modala
    const overlay = this.querySelector(`#${this._uid}-modal-overlay`);
    overlay.addEventListener('click', e => {
      if (
        e.target === overlay ||
        e.target.closest(`#${this._uid}-modal-close`) ||
        e.target.closest(`#${this._uid}-btn-close`)
      ) {
        overlay.style.display = 'none';
      }
    });

    // Delegacja kliknięć na elementy zebrań
    this.content.addEventListener('click', e => {
      const item = e.target.closest('.zebranie-item');
      if (!item) return;
      const idx = Array.prototype.indexOf.call(this.content.children, item);
      if (idx >= 0 && this._currentZebrania[idx]) {
        this._showModal(this._currentZebrania[idx]);
      }
    });
  }

  _forceUpdate() {
    if (this._hass) this.hass = this._hass;
  }

  renderHeader(state) {
    const name = state.attributes.friendly_name?.replace('Zebrania: ', '') || 'Dziecko';
    this.querySelector(`#${this._uid}-student-name`).lastChild.textContent = `Zebrania: ${name}`;

    this.querySelector(`#${this._uid}-sort-desc`).style.color =
      this._sortOrder === 'desc' ? 'var(--primary-color)' : 'var(--secondary-text-color)';

    this.querySelector(`#${this._uid}-sort-asc`).style.color =
      this._sortOrder === 'asc' ? 'var(--primary-color)' : 'var(--secondary-text-color)';
  }

  renderBody(state) {
    let zebrania = [...(state.attributes.zebrania || [])];

    zebrania.sort((a, b) => {
      const strA = `${a?.data || '0000-00-00'}T${a?.godzina || '00:00'}`;
      const strB = `${b?.data || '0000-00-00'}T${b?.godzina || '00:00'}`;
      const timeA = Date.parse(strA);
      const timeB = Date.parse(strB);
      if (isNaN(timeA) || isNaN(timeB)) return 0;
      return this._sortOrder === 'desc' ? timeB - timeA : timeA - timeB;
    });

    if (this.config.limit > 0) zebrania = zebrania.slice(0, this.config.limit);

    this._currentZebrania = zebrania;

    let html = zebrania.length
      ? ''
      : `<div style="text-align:center;padding:25px;opacity:0.6">Brak zebrań w dzienniku.</div>`;

    const today = new Date().toISOString().split('T')[0];

    zebrania.forEach(z => {
      const shortDesc = (z.opis || "Brak szczegółów").length > 140
        ? z.opis.substring(0, 137) + '…'
        : (z.opis || "Brak szczegółów");

      const isFuture = z.data >= today;
      const onlineInfo = z.online
        ? ` <ha-icon icon="mdi:laptop" style="width:16px;height:16px"></ha-icon> Online`
        : '';

      html += `
        <div class="zebranie-item ${isFuture ? 'future' : ''}" role="button" tabindex="0">
          <div style="flex:1;position:relative;padding-right:80px">
            <div style="position:absolute;top:10px;right:12px;text-align:right">
              <div style="font-weight:bold;color:var(--primary-color);background:var(--secondary-background-color);padding:3px 8px;border-radius:6px;font-size:0.82em">
                ${this._esc(z.data || '?')}
              </div>
              <div style="font-size:0.75em;opacity:0.7;margin-top:4px">
                <ha-icon icon="mdi:clock-outline" style="width:12px;height:12px"></ha-icon>
                ${this._esc(z.godzina || '—')}
              </div>
            </div>
            <div style="font-size:0.9em;opacity:0.85;margin-bottom:6px;padding-top:2px;font-weight:bold">
              <ha-icon icon="mdi:map-marker-outline" style="width:16px;height:16px"></ha-icon>
              ${this._esc(z.sala || 'Brak sali')}
            </div>
            <div style="font-size:0.95em;line-height:1.35;margin-bottom:8px">
              ${this._esc(shortDesc)}
            </div>
            <div style="font-size:0.78em;font-style:italic;opacity:0.65">${onlineInfo}</div>
          </div>
          <ha-icon icon="mdi:chevron-right" class="chevron"></ha-icon>
        </div>`;
    });

    this.content.innerHTML = html;
  }

  _showModal(z) {
    this.querySelector(`#${this._uid}-m-subtitle`).innerHTML = `
      <strong>Data:</strong> ${this._esc(z.data || '—')} | 
      <strong>Godzina:</strong> ${this._esc(z.godzina || '—')}<br>
      <strong>Sala:</strong> ${this._esc(z.sala || '—')}
    `;

    this.querySelector(`#${this._uid}-m-body`).textContent =
      z.opis || "Brak szczegółowego opisu.";

    const onlineDiv = this.querySelector(`#${this._uid}-m-online`);
    if (z.online) {
      const safe = this._safeUrl(z.online);
      onlineDiv.innerHTML = safe !== '#'
        ? `<a href="${safe}" target="_blank" rel="noopener noreferrer" style="color:var(--primary-color);text-decoration:underline">
             <ha-icon icon="mdi:open-in-new"></ha-icon> Dołącz do spotkania online
           </a>`
        : `<span style="opacity:0.6">Link do spotkania online niedostępny</span>`;
    } else {
      onlineDiv.innerHTML = '';
    }

    this.querySelector(`#${this._uid}-modal-overlay`).style.display = 'flex';
  }

  setConfig(config) {
    if (!config?.entity) {
      throw new Error("Musisz podać entity w konfiguracji karty");
    }
    this.config = {
      limit: 10,
      default_sort: 'desc',
      ...config
    };
  }

  getCardSize() {
    return 6;
  }
}

customElements.define('vultron-zebrania-card', VultronZebraniaCard);
