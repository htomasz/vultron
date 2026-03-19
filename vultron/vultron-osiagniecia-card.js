class VultronOsiagnieciaCard extends HTMLElement {
  constructor() {
    super();
    this._sortOrder = 'desc'; // Domyślnie najnowsze

    // Zmienne do zapobiegania wyciekom pamięci/CPU (State Caching)
    this._cachedState = null;
    this._cachedSortOrder = null;
  }

  set hass(hass) {
    this._hass = hass;
    const entityId = this.config.entity;
    const newState = hass.states[entityId];

    // 1. Inicjalizacja DOM i Event Listenerów (Tylko raz!)
    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <style>
            .achievement-item {
              padding: 12px; border-radius: 8px; cursor: pointer; background: var(--card-background-color);
              transition: background 0.2s, transform 0.1s; margin-bottom: 8px; border: 1px solid var(--divider-color); user-select: none;
            }
            .achievement-item:hover { background: var(--secondary-background-color); }
            /* Okno modalne */
            #modal-overlay {
              display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
              background: rgba(0,0,0,0.7); z-index: 10000; align-items: center; justify-content: center;
              backdrop-filter: blur(3px); user-select: none;
            }
            #modal-content {
              background: var(--ha-card-background, var(--card-background-color)); width: 90%; max-width: 500px;
              max-height: 80%; border-radius: 12px; padding: 20px; overflow-y: auto; position: relative;
              box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid var(--divider-color); user-select: text !important; cursor: auto;
            }
            #modal-close { float: right; cursor: pointer; padding: 5px; color: var(--secondary-text-color); }
            .modal-header { border-bottom: 1px solid var(--divider-color); margin-bottom: 15px; padding-bottom: 10px; }
            .modal-body { line-height: 1.6; font-size: 15px; color: var(--primary-text-color); white-space: pre-wrap; }
            .modal-title { font-size: 16px; font-weight: bold; margin-bottom: 10px; color: var(--primary-color); }
            .sort-link { cursor: pointer; font-size: 0.75em; font-weight: bold; margin-left: 8px; transition: color 0.2s; }
          </style>

          <div id="container" style="padding: 16px;">
            <div id="header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
              <div id="title" style="font-size: 1.1em; font-weight: 500; color: var(--primary-text-color); display:flex; align-items:center; gap:8px;"><ha-icon icon="mdi:trophy-outline"></ha-icon>Osiągnięcia</div>
              <div id="sort-controls" style="display: flex; gap: 5px;">
                <span id="btn-sort-desc" class="sort-link">NAJNOWSZE</span>
                <span style="font-size: 0.75em; opacity: 0.3;">|</span>
                <span id="btn-sort-asc" class="sort-link">NAJSTARSZE</span>
              </div>
            </div>
            <div id="achievements-list"></div>
          </div>

          <div id="modal-overlay">
            <div id="modal-content">
              <div id="modal-close"><ha-icon icon="mdi:close"></ha-icon></div>
              <div class="modal-header">
                <div class="modal-title">Szczegóły osiągnięcia</div>
              </div>
              <div id="m-body" class="modal-body"></div>
              <div style="margin-top: 20px; text-align: center;">
                 <mwc-button raised id="btn-close">Zamknij</mwc-button>
              </div>
            </div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#achievements-list');
      this.titleEl = this.querySelector('#title');

      // Obsługa sortowania (podpinane raz)
      this.querySelector('#btn-sort-desc').addEventListener('click', () => { this._sortOrder = 'desc'; this._forceUpdate(); });
      this.querySelector('#btn-sort-asc').addEventListener('click', () => { this._sortOrder = 'asc'; this._forceUpdate(); });

      // Obsługa zamykania modala (Kliknięcie w 'X', 'Zamknij' lub Ciemne Tło poza okienkiem)
      const overlay = this.querySelector('#modal-overlay');
      overlay.addEventListener('click', (e) => {
        // Zamykaj tylko jeśli kliknięto bezpośrednio w ciemne tło (overlay) lub w przyciski zamknięcia
        if (
          e.target === overlay ||
          e.target.closest('#modal-close') ||
          e.target.closest('#btn-close')
        ) {
          overlay.style.display = 'none';
        }
      });
    }

    // 2. Optymalizacja wycieków CPU - renderuj tylko przy zmianie danych lub sortowania
    if (this._cachedState === newState && this._cachedSortOrder === this._sortOrder) {
      return;
    }

    this._cachedState = newState;
    this._cachedSortOrder = this._sortOrder;

    this.renderData(newState);
  }

  _forceUpdate() {
    this.hass = this._hass;
  }

  // Funkcja zabezpieczająca przed atakami XSS
  _esc(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  renderData(stateObj) {
    if (!stateObj) return;

    const rawData = stateObj.attributes.osiagniecia || [];
    // Bezpieczne wstawianie tekstu
    this.titleEl.innerText = stateObj.attributes.friendly_name || "Osiągnięcia";

    this.querySelector('#btn-sort-desc').style.color = this._sortOrder === 'desc' ? 'var(--primary-color)' : 'var(--secondary-text-color)';
    this.querySelector('#btn-sort-asc').style.color = this._sortOrder === 'asc' ? 'var(--primary-color)' : 'var(--secondary-text-color)';

    let sortedData = [...rawData].sort((a, b) => {
      const idA = parseInt(a.id);
      const idB = parseInt(b.id);
      return this._sortOrder === 'desc' ? idB - idA : idA - idB;
    });

    if (this.config.limit && this.config.limit > 0) sortedData = sortedData.slice(0, this.config.limit);

    if (sortedData.length === 0) {
      this.content.innerHTML = `<div style="text-align: center; padding: 20px; opacity: 0.5;">Brak osiągnięć</div>`;
      return;
    }

    this.content.innerHTML = '';
    sortedData.forEach((item) => {
      const el = document.createElement('div');
      el.className = `achievement-item`;

      const lines = item.tresc.split('\n');
      const firstLine = lines[0];
      const hasMore = lines.length > 1;

      // ZABEZPIECZENIE XSS: firstLine musi przejść przez this._esc()
      el.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
          <div style="font-size: 14px; color: var(--primary-text-color); line-height: 1.3; flex: 1;">
            <strong>${this._esc(firstLine)}</strong>
            ${hasMore ? `<div style="font-size: 12px; opacity: 0.6; margin-top: 4px; font-style: italic;">Kliknij, aby zobaczyć całość...</div>` : ''}
          </div>
          <ha-icon icon="mdi:chevron-right" style="color: var(--divider-color);"></ha-icon>
        </div>
      `;

      el.onclick = () => {
        // innerText samo w sobie jest bezpieczne i chroni przed XSS
        this.querySelector('#m-body').innerText = item.tresc;
        this.querySelector('#modal-overlay').style.display = 'flex';
      };

      this.content.appendChild(el);
    });
  }

  setConfig(config) { if (!config.entity) throw new Error('Entity missing'); this.config = config; }
  getCardSize() { return 4; }
}

customElements.define('vultron-osiagniecia-card', VultronOsiagnieciaCard);