class VultronWorkCard extends HTMLElement {
  constructor() {
    super();
    this._sortOrder = null; // 'desc' lub 'asc' – null = wczytamy z config
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
            .work-item {
              margin-bottom: 10px;
              padding: 12px 14px;
              background: var(--card-background-color);
              border-radius: 8px;
              cursor: pointer;
              transition: background 0.2s;
              border: 1px solid var(--divider-color);
              user-select: none;
              position: relative;
              min-height: 70px;
            }
            .work-item:hover {
              background: var(--secondary-background-color);
            }

            #work-modal-overlay {
              display: none;
              position: fixed;
              top: 0; left: 0; width: 100%; height: 100%;
              background: rgba(0,0,0,0.7);
              z-index: 1000;
              align-items: center;
              justify-content: center;
              backdrop-filter: blur(3px);
            }
            #work-modal-content {
              background: var(--ha-card-background, var(--card-background-color));
              width: 90%;
              max-width: 500px;
              max-height: 80%;
              border-radius: 12px;
              padding: 20px;
              overflow-y: auto;
              box-shadow: 0 10px 25px rgba(0,0,0,0.5);
              border: 1px solid var(--divider-color);
              user-select: text !important;
            }
            #work-modal-close {
              float: right;
              cursor: pointer;
              padding: 5px;
              color: var(--secondary-text-color);
            }
            .modal-header { border-bottom: 1px solid var(--divider-color); margin-bottom: 15px; padding-bottom: 10px; }
            .modal-body {
              line-height: 1.6;
              font-size: 15px;
              color: var(--primary-text-color);
              white-space: pre-wrap;
            }
            .modal-title { font-size: 16px; font-weight: bold; margin-bottom: 10px; color: var(--primary-color); }
          </style>

          <div style="padding: 16px;">
            <div id="header-area"></div>
            <div id="vultron-work-body"></div>
          </div>

          <div id="work-modal-overlay">
            <div id="work-modal-content">
              <div id="work-modal-close"><ha-icon icon="mdi:close"></ha-icon></div>
              <div class="modal-header">
                <div class="modal-title" id="m-work-title">Szczegóły wydarzenia</div>
                <div style="font-size: 13px; color: var(--secondary-text-color);" id="m-work-subtitle"></div>
              </div>
              <div id="m-work-body" class="modal-body"></div>
              <div style="margin-top: 20px; text-align: center;">
                <mwc-button raised id="work-btn-close">Zamknij</mwc-button>
              </div>
            </div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#vultron-work-body');
      this.headerArea = this.querySelector('#header-area');

      const overlay = this.querySelector('#work-modal-overlay');
      const modalBox = this.querySelector('#work-modal-content');
      const closeBtn = this.querySelector('#work-modal-close');
      const closeBtn2 = this.querySelector('#work-btn-close');

      overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.style.display = 'none'; });
      [closeBtn, closeBtn2].forEach(el => {
        el.addEventListener('click', () => { overlay.style.display = 'none'; });
      });
      modalBox.addEventListener('click', (e) => { e.stopPropagation(); });
    }

    const state = hass.states[this.config.entity];
    if (!state || !state.attributes.lista?.length) {
      this.content.innerHTML = `<div style="padding: 20px; text-align: center;">Brak nadchodzących wydarzeń.</div>`;
      return;
    }

    this.renderHeader(state);
    this.renderBody(state);
  }

  renderHeader(state) {
    const childName = (state.attributes.friendly_name || '').replace('Terminarz: ', '');

    this.headerArea.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
        <div style="font-size: 1.1em; font-weight: 500; color: var(--primary-text-color);">Terminarz: ${childName}</div>
        <div style="display: flex; gap: 10px; font-size: 0.8em; font-weight: bold;">
          <span id="sort-desc" style="cursor: pointer; color: ${this._sortOrder === 'desc' ? 'var(--primary-color)' : 'var(--secondary-text-color)'};">NAJNOWSZE</span>
          <span id="sort-asc"  style="cursor: pointer; color: ${this._sortOrder === 'asc'  ? 'var(--primary-color)' : 'var(--secondary-text-color)'};">NAJSTARSZE</span>
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
    let lista = [...state.attributes.lista];

    // Filtrujemy tylko przyszłe wydarzenia
    const today = new Date().toISOString().split('T')[0];
    lista = lista.filter(i => i.data >= today);

    // Sortowanie
    lista.sort((a, b) => {
      const dateA = new Date(a.data);
      const dateB = new Date(b.data);
      return this._sortOrder === 'desc' ? dateB - dateA : dateA - dateB;
    });

    // Opcjonalny limit
    if (this.config.limit && this.config.limit > 0) {
      lista = lista.slice(0, this.config.limit);
    }

    let html = "";
    if (lista.length === 0) {
      html = `<div style="text-align: center; padding: 20px; opacity: 0.5;">Brak zadań i sprawdzianów.</div>`;
    } else {
      lista.forEach(i => {
        const isT = i.typ.toLowerCase().includes("sprawdzian") || i.typ.toLowerCase().includes("klasówka");
        const isQ = i.typ.toLowerCase().includes("kartkówka");
        let bc = "#2196F3";
        if (isT) bc = "#f44336";
        if (isQ) bc = "#ff9800";

        const shortDesc = i.opis.length > 80 ? i.opis.substring(0, 100) + '...' : i.opis;

        // ← tutaj używamy pełnej daty YYYY-MM-DD
        const displayDate = i.data || '—';

        html += `
          <div class="work-item" style="border-left: 5px solid ${bc};">
            <div style="position: relative; padding-right: 90px;">
              <div style="position: absolute; top: 10px; right: 12px;">
                <span style="font-weight: bold; color: var(--primary-color); background: var(--secondary-background-color); padding: 3px 8px; border-radius: 6px; font-size: 0.82em; white-space: nowrap;">
                  ${displayDate}
                </span>
              </div>

              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; padding-top: 2px;">
                <span style="font-weight: bold; color: var(--primary-text-color);">${i.przedmiot}</span>
              </div>

              <div style="font-size: 0.92em; color: var(--primary-text-color); line-height: 1.35;">
                <b style="color: ${bc};">${i.typ}</b>: ${shortDesc}
              </div>
            </div>
            <ha-icon icon="mdi:chevron-right" style="position: absolute; right: 12px; top: 50%; transform: translateY(-50%); color: var(--divider-color);"></ha-icon>
          </div>
        `;
      });
    }

    this.content.innerHTML = html;

    // Ponowne podpięcie kliknięć
    this.content.querySelectorAll('.work-item').forEach((el, index) => {
      const item = lista[index];
      if (!item) return;

      el.onclick = () => {
        this.querySelector('#m-work-title').innerText = `${item.przedmiot} - ${item.typ}`;
        this.querySelector('#m-work-title').style.color = el.style.borderLeftColor;
        this.querySelector('#m-work-subtitle').innerText = `Data: ${item.data} | Nauczyciel: ${item.autor || 'Nieznany'}`;
        this.querySelector('#m-work-body').innerText = item.opis;
        this.querySelector('#work-modal-overlay').style.display = 'flex';
      };
    });
  }

  setConfig(config) {
    if (!config.entity) throw new Error('Entity missing');
    this.config = config;
  }

  getCardSize() { return 6; }
}

customElements.define('vultron-work-card', VultronWorkCard);

