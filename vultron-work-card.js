class VultronWorkCard extends HTMLElement {
  set hass(hass) {
    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <div id="vultron-work-body" style="padding: 16px;"></div>
        </ha-card>
      `;
      this.content = this.querySelector('#vultron-work-body');
    }

    const entityId = this.config.entity;
    const state = hass.states[entityId];

    if (!state || !state.attributes.lista) {
      this.content.innerHTML = `<div style="padding: 20px; text-align: center;">Brak nadchodzących wydarzeń.</div>`;
      return;
    }

    const childName = state.attributes.friendly_name ? state.attributes.friendly_name.replace('Terminarz: ', '') : 'Dziecko';

    let html = `
      <div style="font-size: 1.2em; font-weight: 500; margin-bottom: 12px; border-bottom: 2px solid #f44336; padding-bottom: 5px; color: var(--primary-text-color);">
        Terminarz: ${childName}
      </div>
    `;

    if (state.attributes.lista.length === 0) {
        html += `<div style="text-align: center; padding: 20px; opacity: 0.5;">Czyste konto! Brak zadań i sprawdzianów.</div>`;
    }

    state.attributes.lista.forEach(i => {
      // Formatowanie daty (z YYYY-MM-DD na DD.MM)
      const dateParts = i.data.split('-');
      const formattedDate = `${dateParts[2]}.${dateParts[1]}`;
      
      const isTest = i.typ.toLowerCase().includes("sprawdzian") || i.typ.toLowerCase().includes("klasówka");
      const isQuiz = i.typ.toLowerCase().includes("kartkówka");
      
      let borderColor = "#2196F3"; // Zadanie domowe
      if (isTest) borderColor = "#f44336"; // Sprawdzian
      if (isQuiz) borderColor = "#ff9800"; // Kartkówka

      html += `
        <div style="margin-bottom: 10px; padding: 10px; background: var(--secondary-background-color); border-radius: 8px; border-left: 5px solid ${borderColor}; position: relative;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <span style="font-weight: bold; color: var(--primary-text-color);">${i.przedmiot}</span>
            <span style="font-weight: bold; color: var(--primary-color); background: var(--card-background-color); padding: 2px 6px; border-radius: 4px; font-size: 0.85em;">${formattedDate}</span>
          </div>
          <div style="font-size: 0.9em; color: var(--primary-text-color);">
            <b style="color: ${borderColor};">${i.typ}</b>: ${i.opis}
          </div>
          <div style="font-size: 0.75em; opacity: 0.5; margin-top: 6px; font-style: italic;">
            Nauczyciel: ${i.autor || 'Nieznany'}
          </div>
        </div>
      `;
    });

    this.content.innerHTML = html;
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Musisz zdefiniować encję (entity)");
    this.config = config;
  }

  getCardSize() { return 6; }
}
customElements.define("vultron-work-card", VultronWorkCard);
