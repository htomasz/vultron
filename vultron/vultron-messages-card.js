class VultronMessagesCard extends HTMLElement {
  _normalizeDateToISO(dateStr) {
    if (!dateStr || typeof dateStr !== 'string') return '—';
    if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) return dateStr;
    if (/^\d{4}-\d{2}-\d{2}[\s+T]\d{1,2}:\d{2}/.test(dateStr)) return dateStr.split(/[\sT]/)[0];

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
    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <style>
            .message-item {
              padding: 12px 14px;
              border-radius: 8px;
              cursor: pointer;
              background: var(--card-background-color);
              transition: background 0.2s;
              margin-bottom: 8px;
              border: 1px solid var(--divider-color);
              user-select: none;
              display: flex;
              justify-content: space-between;
              align-items: flex-start;
            }
            .message-item:hover {
              background: var(--secondary-background-color);
            }
            .unread {
              border: 2px solid var(--error-color) !important;
              box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            }
            .chevron {
              color: var(--divider-color);
              margin-top: 6px;
              flex-shrink: 0;
            }
            #modal-overlay {
              display: none;
              position: fixed;
              top: 0; left: 0; width: 100%; height: 100%;
              background: rgba(0,0,0,0.7);
              z-index: 1000;
              align-items: center;
              justify-content: center;
              backdrop-filter: blur(3px);
              user-select: none;
            }
            #modal-content {
              background: var(--ha-card-background, var(--card-background-color));
              width: 90%;
              max-width: 600px;
              max-height: 85%;
              border-radius: 12px;
              padding: 20px;
              overflow-y: auto;
              box-shadow: 0 10px 25px rgba(0,0,0,0.5);
              border: 1px solid var(--divider-color);
              user-select: text !important;
            }
            #modal-close {
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
            }
            .modal-meta { font-size: 12px; color: var(--secondary-text-color); margin-bottom: 5px; }
            .modal-subject { font-size: 16px; font-weight: bold; margin-bottom: 10px; color: var(--primary-color); }
          </style>

          <div id="container" style="padding: 16px;">
            <div id="header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
              <div id="title" style="font-size: 1.1em; font-weight: 500; color: var(--primary-text-color);">Wiadomości</div>
              <div id="stats" style="font-size: 0.85em; font-weight: bold; color: var(--primary-color);"></div>
            </div>
            <div id="messages-list"></div>
          </div>

          <div id="modal-overlay">
            <div id="modal-content">
              <div id="modal-close"><ha-icon icon="mdi:close"></ha-icon></div>
              <div class="modal-header">
                <div id="m-meta" class="modal-meta"></div>
                <div id="m-sender" style="font-weight: bold; font-size: 15px; color: var(--primary-text-color);"></div>
                <div id="m-subject" class="modal-subject"></div>
              </div>
              <div id="m-body" class="modal-body"></div>
              <div style="margin-top: 20px; text-align: center;">
                <mwc-button raised id="btn-close">Zamknij</mwc-button>
              </div>
            </div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#messages-list');
      this.stats = this.querySelector('#stats');
      this.titleEl = this.querySelector('#title');

      const overlay = this.querySelector('#modal-overlay');
      const closeBtn = this.querySelector('#modal-close');
      const closeBtn2 = this.querySelector('#btn-close');

      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) overlay.style.display = 'none';
      });
      [closeBtn, closeBtn2].forEach(el => {
        el.addEventListener('click', () => { overlay.style.display = 'none'; });
      });
    }

    const stateObj = hass.states[this.config.entity];
    if (!stateObj) return;

    const rawMessages = stateObj.attributes.wiadomosci || [];
    this.stats.innerText = stateObj.attributes.stats || "";
    this.titleEl.innerText = stateObj.attributes.friendly_name || "Wiadomości";

    let sortedMessages = [...rawMessages];
    if (this.config.limit && this.config.limit > 0) sortedMessages = sortedMessages.slice(0, this.config.limit);

    if (sortedMessages.length === 0) {
      this.content.innerHTML = `<div style="text-align: center; padding: 20px; opacity: 0.5;">Brak wiadomości</div>`;
      return;
    }

    this.content.innerHTML = '';
    sortedMessages.forEach((msg) => {
      const isUnread = msg.przeczytana === false;
      const displayDate = this._normalizeDateToISO(msg.data);

      const item = document.createElement('div');
      item.className = `message-item${isUnread ? ' unread' : ''}`;

      item.innerHTML = `
        <div style="flex: 1; position: relative; padding-right: 80px;">
          <div style="position: absolute; top: 10px; right: 12px;">
            <span style="font-weight: 600; color: var(--primary-color); background: var(--secondary-background-color); padding: 3px 8px; border-radius: 6px; font-size: 0.81em; white-space: nowrap;">
              ${displayDate}
            </span>
          </div>
          ${isUnread ? `<ha-icon icon="mdi:circle" style="position: absolute; top: 32px; right: 14px; --mdc-icon-size: 10px; color: var(--error-color);"></ha-icon>` : ''}
          <div style="font-weight: ${isUnread ? 'bold' : 'normal'}; font-size: 1.05em; color: var(--primary-text-color); margin-bottom: 4px; padding-top: 2px;">
            ${msg.nadawca || '?'}
          </div>
          <div style="font-size: 0.93em; color: var(--primary-text-color); opacity: 0.92; line-height: 1.38;">
            ${msg.temat || '(brak tematu)'}
          </div>
        </div>
        <ha-icon icon="mdi:chevron-right" class="chevron"></ha-icon>
      `;

      item.onclick = () => {
        this.querySelector('#m-meta').innerText = displayDate;
        this.querySelector('#m-sender').innerText = msg.nadawca || '—';
        this.querySelector('#m-subject').innerText = msg.temat || '(brak tematu)';
        // === BEZPIECZNA WERSJA ===
        this.querySelector('#m-body').innerText = msg.tresc || "Treść wiadomości archiwalnej dostępna w aplikacji EduVulcan.";
        this.querySelector('#modal-overlay').style.display = 'flex';
      };

      this.content.appendChild(item);
    });
  }

  setConfig(config) { if (!config.entity) throw new Error('Entity missing'); this.config = config; }
  getCardSize() { return 4; }
}

customElements.define('vultron-messages-card', VultronMessagesCard);
