class VultronMessagesCard extends HTMLElement {
  set hass(hass) {
    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <style>
            .message-item {
              padding: 10px;
              border-radius: 8px;
              cursor: pointer;
              background: var(--card-background-color);
              transition: background 0.2s, transform 0.1s;
              margin-bottom: 8px;
              border: 1px solid var(--divider-color);
              user-select: none;
            }
            .message-item:hover {
              background: var(--secondary-background-color);
            }
            .unread {
              border: 2px solid var(--error-color) !important;
              box-shadow: 0 2px 5px rgba(0,0,0,0.1);
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
              position: relative;
              box-shadow: 0 10px 25px rgba(0,0,0,0.5);
              border: 1px solid var(--divider-color);
              user-select: text !important;
              cursor: auto;
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

    // Obsługa limitu z karty
    let sortedMessages = [...rawMessages];
    if (this.config.limit && this.config.limit > 0) sortedMessages = sortedMessages.slice(0, this.config.limit);

    if (sortedMessages.length === 0) {
      this.content.innerHTML = `<div style="text-align: center; padding: 20px; opacity: 0.5;">Brak wiadomości</div>`;
      return;
    }

    this.content.innerHTML = '';
    sortedMessages.forEach((msg) => {
      const isUnread = msg.przeczytana === false;
      const item = document.createElement('div');
      item.className = `message-item ${isUnread ? 'unread' : ''}`;

      item.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
          <span style="font-size: 11px; color: var(--secondary-text-color);">${msg.data}</span>
          ${isUnread ? `<ha-icon icon="mdi:circle" style="--mdc-icon-size: 10px; color: var(--error-color);"></ha-icon>` : ''}
        </div>
        <div style="font-weight: ${isUnread ? 'bold' : 'normal'}; font-size: 14px; color: var(--primary-text-color); line-height: 1.2;">${msg.nadawca}</div>
        <div style="font-size: 13px; color: var(--primary-text-color); margin-top: 3px; opacity: 0.9; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${msg.temat}</div>
      `;

      item.onclick = () => {
        this.querySelector('#m-meta').innerText = msg.data;
        this.querySelector('#m-sender').innerText = msg.nadawca;
        this.querySelector('#m-subject').innerText = msg.temat;
        this.querySelector('#m-body').innerHTML = msg.tresc || "<div style='opacity:0.6; padding: 10px; background: rgba(var(--rgb-primary-color), 0.1); border-radius: 5px;'>Treść wiadomości archiwalnej dostępna w aplikacji Vulcan.</div>";
        this.querySelector('#modal-overlay').style.display = 'flex';
      };

      this.content.appendChild(item);
    });
  }

  setConfig(config) { if (!config.entity) throw new Error('Entity missing'); this.config = config; }
  getCardSize() { return 4; }
}

customElements.define('vultron-messages-card', VultronMessagesCard);