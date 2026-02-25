class VultronUwagiCard extends HTMLElement {
  constructor() {
    super();
    this._sortOrder = null;
  }

  set hass(hass) {
    this._hass = hass;

    if (this._sortOrder === null) {
      this._sortOrder = this.config.default_sort || 'desc';
    }

    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <style>
            .uwaga-item {
              margin-bottom: 10px;
              padding: 10px;
              background: var(--card-background-color);
              border-radius: 8px;
              cursor: pointer;
              transition: background 0.2s;
              border: 1px solid var(--divider-color);
              border-left: 5px solid #2196F3; /* nadpisywane inline kolorem */
              user-select: none;
              display: flex;
              justify-content: space-between;
              align-items: flex-start;
            }
            .uwaga-item:hover {
              background: var(--secondary-background-color);
            }
            .chevron {
              color: var(--divider-color);
              margin-top: 6px;
              flex-shrink: 0;
            }

            #uwagi-modal-overlay {
              display: none;
              position: fixed;
              top: 0; left: 0; width: 100%; height: 100%;
              background: rgba(0,0,0,0.7);
              z-index: 1000;
              align-items: center;
              justify-content: center;
              backdrop-filter: blur(3px);
            }
            #uwagi-modal-content {
              background: var(--ha-card-background, var(--card-background-color));
              width: 90%;
              max-width: 520px;
              max-height: 85%;
              border-radius: 12px;
              padding: 20px;
              overflow-y: auto;
              box-shadow: 0 10px 25px rgba(0,0,0,0.5);
              border: 1px solid var(--divider-color);
              user-select: text;
            }
            #uwagi-modal-close {
              float: right;
              cursor: pointer;
              padding: 5px;
              color: var(--secondary-text-color);
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
            }
            .modal-subtitle {
              font-size: 13px;
              color: var(--secondary-text-color);
              margin-top: 4px;
            }
            .modal-body {
              line-height: 1.6;
              font-size: 15px;
              color: var(--primary-text-color);
              white-space: pre-wrap;
            }
          </style>

          <div style="padding: 16px;">
            <div id="header-area"></div>
            <div id="vultron-uwagi-body"></div>
          </div>

          <div id="uwagi-modal-overlay">
            <div id="uwagi-modal-content">
              <div id="uwagi-modal-close"><ha-icon icon="mdi:close"></ha-icon></div>
              <div class="modal-header">
                <div class="modal-title" id="m-uwagi-title">Uwaga / Pochwała</div>
                <div class="modal-subtitle" id="m-uwagi-subtitle"></div>
              </div>
              <div id="m-uwagi-body" class="modal-body"></div>
              <div style="margin-top: 20px; text-align: center;">
                <mwc-button raised id="uwagi-btn-close">Zamknij</mwc-button>
              </div>
            </div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#vultron-uwagi-body');
      this.headerArea = this.querySelector('#header-area');

      const overlay = this.querySelector('#uwagi-modal-overlay');
      const closeBtn = this.querySelector('#uwagi-modal-close');
      const closeBtn2 = this.querySelector('#uwagi-btn-close');

      overlay.addEventListener('click', e => { if (e.target === overlay) overlay.style.display = 'none'; });
      [closeBtn, closeBtn2].forEach(el => el.addEventListener('click', () => overlay.style.display = 'none'));
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
    const childName = state.attributes.friendly_name?.replace('Uwagi: ', '') || 'Dziecko';

    this.headerArea.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
        <div style="font-size: 1.1em; font-weight: 500; color: var(--primary-text-color);">Uwagi: ${childName}</div>
        <div style="display: flex; gap: 10px; font-size: 0.8em; font-weight: bold;">
          <span id="sort-desc" style="cursor: pointer; color: ${this._sortOrder === 'desc' ? 'var(--primary-color)' : 'var(--secondary-text-color)'};">NAJNOWSZE</span>
          <span id="sort-asc" style="cursor: pointer; color: ${this._sortOrder === 'asc' ? 'var(--primary-color)' : 'var(--secondary-text-color)'};">NAJSTARSZE</span>
        </div>
      </div>
    `;

    this.headerArea.querySelectorAll('[id^="sort-"]').forEach(el => {
      el.addEventListener('click', () => {
        this._sortOrder = el.id === 'sort-desc' ? 'desc' : 'asc';
        this.hass = this._hass;
      });
    });
  }

  renderBody(state) {
    let uwagi = [...state.attributes.uwagi || []];

    uwagi.sort((a, b) => {
      const da = this._parseDate(a.data);
      const db = this._parseDate(b.data);
      return this._sortOrder === 'desc' ? db - da : da - db;
    });

    if (this.config.limit > 0) uwagi = uwagi.slice(0, this.config.limit);

    let html = uwagi.length ? '' : `<div style="text-align:center;padding:20px;opacity:0.6;">Brak wpisów w dzienniku.</div>`;

    uwagi.forEach(u => {
      let color = u.typ === "pozytywna" ? "#4CAF50" : u.typ === "negatywna" ? "#F44336" : "#2196F3";

      const short = u.tresc.length > 140 ? u.tresc.substring(0,137)+'...' : u.tresc;

      html += `
        <div class="uwaga-item" style="border-left: 5px solid ${color};"
             data-title="${(u.typ || 'Uwaga').charAt(0).toUpperCase() + (u.typ || 'Uwaga').slice(1)}"
             data-subtitle="Data: ${u.data} | Wystawił: ${u.autor}${u.punkty ? ' • Pkt: '+u.punkty : ''}"
             data-body="${u.tresc.replace(/"/g,'&quot;')}">
          <div style="flex:1;">
            <div style="display:flex;justify-content:space-between;font-size:0.85em;opacity:0.7;margin-bottom:4px;">
              <span>${u.data} | <b>${u.kategoria}</b></span>
              <span>${u.punkty ? 'Pkt: '+u.punkty : ''}</span>
            </div>
            <div style="font-size:0.95em;line-height:1.3;margin-bottom:6px;">${short}</div>
            <div style="font-size:0.75em;font-style:italic;text-align:right;opacity:0.6;">
              Wystawił: ${u.autor}
            </div>
          </div>
          <ha-icon icon="mdi:chevron-right" class="chevron"></ha-icon>
        </div>
      `;
    });

    this.content.innerHTML = html;

    // Kliknięcie → modal
    this.content.querySelectorAll('.uwaga-item').forEach(item => {
      item.addEventListener('click', () => {
        this.querySelector('#m-uwagi-title').innerText = item.dataset.title;
        this.querySelector('#m-uwagi-subtitle').innerText = item.dataset.subtitle;
        this.querySelector('#m-uwagi-body').innerText = item.dataset.body;
        this.querySelector('#uwagi-modal-overlay').style.display = 'flex';
      });
    });
  }

  _parseDate(str) {
    if (!str) return new Date(0);
    const p = str.split('.');
    return p.length === 3 ? new Date(p[2], p[1]-1, p[0]) : new Date(str);
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Musisz zdefiniować encję (entity)");
    this.config = config;
  }

  getCardSize() { return 6; }
}

customElements.define("vultron-uwagi-card", VultronUwagiCard);
