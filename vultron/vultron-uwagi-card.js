class VultronUwagiCard extends HTMLElement {
  constructor() {
    super();
    this._sortOrder = null;

    // Zmienne do zapobiegania wyciekom CPU (State Caching)
    this._cachedState = null;
    this._cachedSortOrder = null;
  }

  // Funkcja zabezpieczająca przed XSS
  _esc(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  _normalizeDateToISO(dateStr) {
    if (!dateStr || typeof dateStr !== 'string') return '—';
    if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) return dateStr;
    if (/^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}$/.test(dateStr)) return dateStr.split(' ')[0];
    const parts = dateStr.split('.').map(p => p.trim());
    if (parts.length === 2 || parts.length === 3) {
      const day   = parts[0].padStart(2, '0');
      const month = parts[1].padStart(2, '0');
      let year    = parts[2] || new Date().getFullYear().toString();
      if (year.length === 2) year = (parseInt(year, 10) < 70 ? '20' : '19') + year;
      if (year.length === 4 && !isNaN(parseInt(day)) && !isNaN(parseInt(month))) {
        return `${year}-${month}-${day}`;
      }
    }
    return dateStr;
  }

  set hass(hass) {
    this._hass = hass;

    if (this._sortOrder === null) {
      this._sortOrder = this.config.default_sort || 'desc';
    }

    // 1. INICJALIZACJA DOM I ZDARZEŃ (Wykona się tylko raz!)
    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <style>
            .uwaga-item { margin-bottom: 10px; padding: 12px 14px; background: var(--card-background-color); border-radius: 8px; cursor: pointer; transition: background 0.2s; border: 1px solid var(--divider-color); border-left: 5px solid #2196F3; user-select: none; display: flex; justify-content: space-between; align-items: flex-start; position: relative; }
            .uwaga-item:hover { background: var(--secondary-background-color); }
            .chevron { color: var(--divider-color); margin-top: 6px; flex-shrink: 0; }

            #uwagi-modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); z-index: 10000; align-items: center; justify-content: center; backdrop-filter: blur(3px); }
            #uwagi-modal-content { background: var(--ha-card-background, var(--card-background-color)); width: 90%; max-width: 520px; max-height: 85%; border-radius: 12px; padding: 20px; overflow-y: auto; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid var(--divider-color); user-select: text; }
            #uwagi-modal-close { float: right; cursor: pointer; padding: 5px; color: var(--secondary-text-color); }

            .modal-header { border-bottom: 1px solid var(--divider-color); margin-bottom: 15px; padding-bottom: 10px; }
            .modal-title { font-size: 16px; font-weight: bold; color: var(--primary-color); }
            .modal-subtitle { font-size: 13px; color: var(--secondary-text-color); margin-top: 4px; }
            .modal-body { line-height: 1.6; font-size: 15px; color: var(--primary-text-color); white-space: pre-wrap; }
          </style>

          <div style="padding: 16px;">
            <div id="header-area">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
                <div id="student-name" style="font-size: 1.1em; font-weight: 500; color: var(--primary-text-color); display:flex; align-items:center; gap:8px;"><ha-icon icon="mdi:comment-alert"></ha-icon>Uwagi</div>
                <div style="display: flex; gap: 10px; font-size: 0.8em; font-weight: bold;">
                  <span id="sort-desc" style="cursor: pointer;">NAJNOWSZE</span>
                  <span id="sort-asc" style="cursor: pointer;">NAJSTARSZE</span>
                </div>
              </div>
            </div>

            <!-- Tutaj będą lądować wpisy. Reszta karty jest nienaruszalna. -->
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

      // PODPINANIE ZDARZEŃ TYLKO RAZ (chroni przed wyciekami i psuciem przycisków)
      this.querySelector('#sort-desc').addEventListener('click', () => { this._sortOrder = 'desc'; this._forceUpdate(); });
      this.querySelector('#sort-asc').addEventListener('click', () => { this._sortOrder = 'asc'; this._forceUpdate(); });

      // Niezawodny system zamykania okna modalnego (Event Delegation)
      const overlay = this.querySelector('#uwagi-modal-overlay');
      overlay.addEventListener('click', (e) => {
        // Zamykamy okno tylko jeśli kliknięto w tło (overlay), "X", lub przycisk MWC "Zamknij"
        if (
          e.target === overlay ||
          e.target.closest('#uwagi-modal-close') ||
          e.target.closest('#uwagi-btn-close')
        ) {
          overlay.style.display = 'none';
        }
      });
    }

    const state = hass.states[this.config.entity];

    // 2. STATE CACHING (Ochrona przed Render Leak - zawieszaniem HA)
    if (this._cachedState === state && this._cachedSortOrder === this._sortOrder) {
      return; // Jeśli stan i sortowanie są takie same jak ostatnio, przerywamy (oszczędzamy CPU)
    }

    this._cachedState = state;
    this._cachedSortOrder = this._sortOrder;

    // 3. RENDEROWANIE ZMIAN
    if (!state || !state.attributes.uwagi) {
      this.content.innerHTML = "<div style='text-align:center;padding:20px;opacity:0.6;'>Brak danych o uwagach.</div>";
      return;
    }

    this.renderHeader(state);
    this.renderBody(state);
  }

  _forceUpdate() {
    this.hass = this._hass; // Wymusza ponowne odpalenie gettera/settera po zmianie sortowania
  }

  renderHeader(state) {
    const childName = state.attributes.friendly_name?.replace('Uwagi: ', '') || 'Dziecko';

    // Bezpieczne wstawianie nazwy (innerText chroni przed XSS)
    this.querySelector('#student-name').innerText = `Uwagi: ${childName}`;

    // Aktualizacja podświetlenia wybranego sortowania
    this.querySelector('#sort-desc').style.color = this._sortOrder === 'desc' ? 'var(--primary-color)' : 'var(--secondary-text-color)';
    this.querySelector('#sort-asc').style.color = this._sortOrder === 'asc' ? 'var(--primary-color)' : 'var(--secondary-text-color)';
  }

  renderBody(state) {
    let uwagi = [...state.attributes.uwagi || []];

    // Logika sortowania
    uwagi.sort((a, b) => {
      const da = this._parseDate(a.data);
      const db = this._parseDate(b.data);
      return this._sortOrder === 'desc' ? db - da : da - db;
    });

    if (this.config.limit > 0) uwagi = uwagi.slice(0, this.config.limit);

    let html = uwagi.length ? '' : `<div style="text-align:center;padding:20px;opacity:0.6;">Brak wpisów w dzienniku.</div>`;

    uwagi.forEach(u => {
      let color = u.typ === "pozytywna" ? "#4CAF50" : u.typ === "negatywna" ? "#F44336" : "#2196F3";

      // Skracanie przed uciekaniem XSS
      const short = u.tresc.length > 140 ? u.tresc.substring(0,137)+'...' : u.tresc;
      const displayDate = this._normalizeDateToISO(u.data);

      // ZABEZPIECZENIE XSS: wszystko otoczone this._esc()
      html += `
        <div class="uwaga-item" style="border-left: 5px solid ${color};">
          <div style="flex:1; position: relative; padding-right: 80px;">
            <div style="position: absolute; top: 10px; right: 12px;">
              <span style="font-weight: bold; color: var(--primary-color); background: var(--secondary-background-color); padding: 3px 8px; border-radius: 6px; font-size: 0.82em; white-space: nowrap;">
                ${this._esc(displayDate)}
              </span>
            </div>

            <div style="font-size:0.9em; opacity:0.85; margin-bottom:6px; padding-top: 2px;">
              <b>${this._esc(u.kategoria)}</b>
            </div>

            <div style="font-size:0.95em; line-height:1.35; margin-bottom:8px;">
              ${this._esc(short)}
            </div>

            <div style="font-size:0.78em; font-style:italic; text-align:right; opacity:0.65; margin-top:4px;">
              Wystawił: ${this._esc(u.autor)}${u.punkty ? ' • Pkt: ' + this._esc(u.punkty) : ''}
            </div>
          </div>
          <ha-icon icon="mdi:chevron-right" class="chevron"></ha-icon>
        </div>
      `;
    });

    // Podmiana HTML (tylko wewnątrz diva id="vultron-uwagi-body", modal jest wyżej i jest bezpieczny)
    this.content.innerHTML = html;

    // Dodanie eventów otwierających modal dla nowo wygenerowanych kafelków
    this.content.querySelectorAll('.uwaga-item').forEach((item, idx) => {
      const u = uwagi[idx];
      if (!u) return;
      item.addEventListener('click', () => {
        // innerText samo w sobie jest bezpieczne
        this.querySelector('#m-uwagi-title').innerText = (u.typ || 'Uwaga').charAt(0).toUpperCase() + (u.typ || 'Uwaga').slice(1);
        this.querySelector('#m-uwagi-subtitle').innerText = `Data: ${this._normalizeDateToISO(u.data)} | Wystawił: ${u.autor}${u.punkty ? ' • Pkt: '+u.punkty : ''}`;
        this.querySelector('#m-uwagi-body').innerText = u.tresc;

        // Wyświetl okno
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
