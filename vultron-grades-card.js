class VultronGradesCard extends HTMLElement {
  set hass(hass) {
    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <div id="vultron-grades-body" style="padding: 16px;"></div>
        </ha-card>
      `;
      this.content = this.querySelector('#vultron-grades-body');
    }

    const entityId = this.config.entity;
    const state = hass.states[entityId];

    if (!state || !state.attributes.lista_przedmiotow) {
      this.content.innerHTML = `<div style="padding: 20px; text-align: center;">Oczekiwanie na dane ocen...</div>`;
      return;
    }

    const childName = state.attributes.friendly_name ? state.attributes.friendly_name.replace('Oceny: ', '') : 'Dziecko';

    let html = `
      <div style="font-size: 1.2em; font-weight: 500; margin-bottom: 12px; border-bottom: 2px solid #2196F3; padding-bottom: 5px; color: var(--primary-text-color);">
        Oceny: ${childName}
      </div>
      <table style="width: 100%; border-collapse: collapse;">
    `;

    state.attributes.lista_przedmiotow.forEach(p => {
      const oceny = p.oceny_ciag.split('  '); // Rozdzielone podwójną spacją z vulo.py

      html += `
        <tr style="border-bottom: 1px solid var(--divider-color);">
          <td style="padding: 12px 0; width: 35%; font-weight: 500; color: var(--primary-text-color); vertical-align: top;">
            ${p.przedmiot}
          </td>
          <td style="padding: 8px 0; display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end;">
            ${oceny.map(o => {
              const val = o.split(' ')[0];
              const date = o.split(' ')[1] ? o.split(' ')[1].replace('(','').replace(')','') : '';
              
              let color = "var(--primary-text-color)";
              if ("56".includes(val[0])) color = "#4CAF50";
              if ("12".includes(val[0])) color = "#F44336";
              if ("3".includes(val[0])) color = "#FF9800";

              return `
                <div style="background: var(--secondary-background-color); border: 1px solid var(--divider-color); border-radius: 6px; padding: 4px 8px; text-align: center; min-width: 40px;">
                  <div style="font-weight: bold; color: ${color}; font-size: 1.1em;">${val}</div>
                  <div style="font-size: 0.65em; opacity: 0.6; margin-top: -2px;">${date}</div>
                </div>
              `;
            }).join('')}
          </td>
        </tr>
      `;
    });

    this.content.innerHTML = html + `</table>`;
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Musisz zdefiniować encję (entity)");
    this.config = config;
  }

  getCardSize() { return 8; }
}
customElements.define("vultron-grades-card", VultronGradesCard);
