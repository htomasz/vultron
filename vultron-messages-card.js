class VultronMessagesCard extends HTMLElement {
  set hass(hass) {
    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <div id="container" style="padding: 16px;">
            <div id="header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
              <div id="title" style="font-size: 1.1em; font-weight: 500; color: var(--primary-text-color);">Wiadomości</div>
              <div id="stats" style="font-size: 0.8em; font-weight: bold;"></div>
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
    if (!stateObj) return;

    const rawMessages = stateObj.attributes.wiadomosci || [];
    const unreadCount = stateObj.state || 0;

    let sortedMessages = [...rawMessages].sort((a, b) => {
      const aUnread = a.przeczytana === false;
      const bUnread = b.przeczytana === false;
      if (aUnread && !bUnread) return -1;
      if (!aUnread && bUnread) return 1;
      return b.data.localeCompare(a.data);
    });

    if (this.config.limit && this.config.limit > 0) sortedMessages = sortedMessages.slice(0, this.config.limit);

    this.titleEl.innerText = stateObj.attributes.friendly_name || "Wiadomości";
    this.stats.innerHTML = unreadCount > 0 
      ? `<b style="color: var(--error-color);">NOWE: ${unreadCount}</b>`
      : `<span style="opacity: 0.6; font-size: 0.85em; text-transform: uppercase;">Brak nowych</span>`;

    if (sortedMessages.length === 0) {
      this.content.innerHTML = `<div style="text-align: center; padding: 20px; opacity: 0.5;">Brak wiadomości</div>`;
      return;
    }

    this.content.innerHTML = sortedMessages.map(msg => {
      const isUnread = msg.przeczytana === false;
      return `
        <div style="padding: 10px; border-radius: 8px; border: ${isUnread ? '2px solid var(--error-color)' : '1px solid var(--divider-color)'}; opacity: ${isUnread ? '1' : '0.7'}; background: var(--card-background-color); box-shadow: ${isUnread ? '0 2px 5px rgba(0,0,0,0.1)' : 'none'}; transition: 0.3s;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <span style="font-size: 11px; color: var(--secondary-text-color);">${msg.data}</span>
            ${isUnread ? `<ha-icon icon="mdi:circle" style="--mdc-icon-size: 10px; color: var(--error-color);"></ha-icon>` : ''}
          </div>
          <div style="font-weight: ${isUnread ? 'bold' : 'normal'}; font-size: 14px; color: var(--primary-text-color); line-height: 1.2;">${msg.nadawca}</div>
          <div style="font-size: 13px; color: var(--primary-text-color); margin-top: 3px; opacity: 0.9;">${msg.temat}</div>
        </div>`;
    }).join('');
  }
  setConfig(config) { if (!config.entity) throw new Error('Entity missing'); this.config = config; }
  getCardSize() { return 4; }
}
customElements.define('vultron-messages-card', VultronMessagesCard);