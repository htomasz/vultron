class VultronUwagiCard extends HTMLElement {
  set hass(hass) {
    if (!this.content) {
      this.innerHTML = `<ha-card><div id="body" style="padding: 16px;"></div></ha-card>`;
      this.content = this.querySelector('#body');
    }

    const state = hass.states[this.config.entity];
    if (!state || !state.attributes.uwagi) {
      this.content.innerHTML = "Brak danych o uwagach.";
      return;
    }

    const childName = state.attributes.friendly_name.replace('Uwagi: ', '');
    let html = `
      <div style="font-size: 1.2em; font-weight: 500; margin-bottom: 12px; border-bottom: 2px solid var(--primary-color); padding-bottom: 5px;">
        Uwagi i Pochwały: ${childName}
      </div>`;

    state.attributes.uwagi.forEach(u => {
      let color = "#2196F3"; // info
      if (u.typ === "pozytywna") color = "#4CAF50"; // pochwała
      if (u.typ === "negatywna") color = "#F44336"; // uwaga

      html += `
        <div style="margin-bottom: 10px; padding: 10px; background: var(--secondary-background-color); border-radius: 8px; border-left: 5px solid ${color};">
          <div style="display: flex; justify-content: space-between; font-size: 0.85em; opacity: 0.7; margin-bottom: 4px;">
            <span>${u.data} | <b>${u.kategoria}</b></span>
            <span>${u.punkty ? `Pkt: ${u.punkty}` : ''}</span>
          </div>
          <div style="font-size: 0.95em; line-height: 1.3; margin-bottom: 6px;">${u.tresc}</div>
          <div style="font-size: 0.75em; font-style: italic; text-align: right; opacity: 0.6;">
            Wystawił: ${u.autor}
          </div>
        </div>
      `;
    });

    this.content.innerHTML = html || "Brak wpisów w dzienniku.";
  }
  setConfig(config) { this.config = config; }
}
customElements.define("vultron-uwagi-card", VultronUwagiCard);
