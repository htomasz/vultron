class VultronMessagesCard extends HTMLElement {
  set hass(hass) {
    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <div id="container" style="padding: 16px;">
            <div id="header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
              <div id="title" style="font-size: 18px; font-weight: bold; color: var(--primary-text-color);">Wiadomości</div>
              <div id="stats" style="font-size: 12px; color: var(--secondary-text-color);"></div>
            </div>
            <div id="messages-list" style="display: flex; flex-direction: column; gap: 8px;"></div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#messages-list');
      this.stats = this.querySelector('#stats');
      this.titleEl = this.querySelector('#title');
    }

    const entityId = this.config.entity;
    const stateObj = hass.states[entityId];

    if (!stateObj) {
      this.content.innerHTML = `<div style="color: red;">Nie znaleziono encji: ${entityId}</div>`;
      return;
    }

    const messages = stateObj.attributes.wiadomosci || [];
    const unreadCount = stateObj.state || 0;

    // Nagłówek i statystyka nieprzeczytanych
    this.titleEl.innerText = stateObj.attributes.friendly_name || "Wiadomości";
    this.stats.innerHTML = unreadCount > 0 
      ? `<b style="color: var(--error-color);">Nowe: ${unreadCount}</b>`
      : `<span style="opacity: 0.6;">Brak nowych</span>`;

    if (messages.length === 0) {
      this.content.innerHTML = `<div style="text-align: center; padding: 20px; opacity: 0.5;">Brak wiadomości w historii</div>`;
      return;
    }

    // Generowanie listy
    this.content.innerHTML = messages.map(msg => {
      const isUnread = msg.przeczytana === false;
      
      // Style dla wiadomości nieodczytanej vs odczytanej
      const opacity = isUnread ? '1' : '0.6';
      const fontWeight = isUnread ? 'bold' : 'normal';
      const borderStyle = isUnread ? '2px solid var(--error-color)' : '1px solid var(--divider-color)';
      const shadow = isUnread ? '0 2px 5px rgba(0,0,0,0.1)' : 'none';

      return `
        <div style="padding: 10px; border-radius: 8px; border: ${borderStyle}; opacity: ${opacity}; background: var(--card-background-color); box-shadow: ${shadow}; transition: 0.3s;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <span style="font-size: 11px; color: var(--secondary-text-color);">${msg.data}</span>
            ${isUnread ? `<ha-icon icon="mdi:circle" style="--mdc-icon-size: 10px; color: var(--error-color);"></ha-icon>` : ''}
          </div>
          <div style="font-weight: ${fontWeight}; font-size: 14px; color: var(--primary-text-color); line-height: 1.2;">
            ${msg.nadawca}
          </div>
          <div style="font-size: 13px; color: var(--primary-text-color); margin-top: 3px; opacity: 0.9;">
            ${msg.temat}
          </div>
        </div>
      `;
    }).join('');
  }

  setConfig(config) {
    if (!config.entity) throw new Error('Musisz podać encję (entity)');
    this.config = config;
  }

  getCardSize() { return 4; }
}

customElements.define('vultron-messages-card', VultronMessagesCard);

// Rejestracja karty
window.customCards = window.customCards || [];
window.customCards.push({
  type: "vultron-messages-card",
  name: "Vultron Messages Card",
  description: "Karta do wyświetlania wiadomości z Vulcan (Odebrane)"
});
