class VultronWorkCard extends HTMLElement {
  constructor() {
    super();
    this._sortDir = null; // Inicjalizacja jako null, aby sprawdzić config
  }

  set hass(hass) {
    this._hass = hass;
    
    // Pobranie domyślnego sortowania z configu (domyślnie 'asc' - rosnąco)
    if (this._sortDir === null) {
      this._sortDir = this.config.default_sort === 'desc' ? -1 : 1;
    }

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

    if (!state || !state.attributes.lista?.length) {
      this.content.innerHTML = `<div style="padding: 20px; text-align: center;">Brak nadchodzących wydarzeń.</div>`;
      return;
    }

    const childName = state.attributes.friendly_name ? state.attributes.friendly_name.replace('Terminarz: ', '') : 'Dziecko';

    // Nagłówek z przyciskami sortowania - kolory zmieniają się zależnie od stanu _sortDir
    let html = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 2px solid #f44336;">
        <div style="font-size: 1.2em; font-weight: 500; color: var(--primary-text-color);">
          Terminarz: ${childName}
        </div>
        <div style="display: flex; gap: 4px;">
          <ha-icon-button id="sort-asc" style="color: ${this._sortDir === 1 ? 'var(--primary-color)' : 'var(--disabled-text-color)'};" title="Sortuj rosnąco (najbliższe pierwsze)">
            <ha-icon icon="hass:arrow-up"></ha-icon>
          </ha-icon-button>
          <ha-icon-button id="sort-desc" style="color: ${this._sortDir === -1 ? 'var(--primary-color)' : 'var(--disabled-text-color)'};" title="Sortuj malejąco (najdalsze pierwsze)">
            <ha-icon icon="hass:arrow-down"></ha-icon>
          </ha-icon-button>
        </div>
      </div>
    `;

    // Sortowanie i filtrowanie przyszłych wydarzeń
    const today = new Date().toISOString().split('T')[0];
    const sortedLista = [...state.attributes.lista] // Kopia tablicy, żeby nie mutować oryginału
      .filter(item => item.data >= today)
      .sort((a, b) => (new Date(a.data) - new Date(b.data)) * this._sortDir);

    if (sortedLista.length === 0) {
      html += `<div style="text-align: center; padding: 20px; opacity: 0.5;">Czyste konto! Brak zadań i sprawdzianów.</div>`;
    } else {
      sortedLista.forEach(i => {
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
    }

    this.content.innerHTML = html;

    // Event listenery dla przycisków
    this.content.querySelector('#sort-asc').addEventListener('click', () => { 
      this._sortDir = 1; 
      this.hass = this._hass; 
    });
    this.content.querySelector('#sort-desc').addEventListener('click', () => { 
      this._sortDir = -1; 
      this.hass = this._hass; 
    });
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Musisz zdefiniować encję (entity)");
    this.config = config;
  }

  getCardSize() { return 6; }
}
customElements.define("vultron-work-card", VultronWorkCard);
