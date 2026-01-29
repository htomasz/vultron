class VultronWorkCard extends HTMLElement {
  constructor() { super(); this._sortDir = null; }
  set hass(hass) {
    this._hass = hass;
    if (this._sortDir === null) this._sortDir = this.config.default_sort === 'desc' ? -1 : 1;
    if (!this.content) {
      this.innerHTML = `<ha-card><div id="vultron-work-body" style="padding: 16px;"></div></ha-card>`;
      this.content = this.querySelector('#vultron-work-body');
    }
    const state = hass.states[this.config.entity];
    if (!state || !state.attributes.lista?.length) {
      this.content.innerHTML = `<div style="padding: 20px; text-align: center;">Brak nadchodzących wydarzeń.</div>`;
      return;
    }
    const childName = (state.attributes.friendly_name || '').replace('Terminarz: ', '');
    let html = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 2px solid var(--primary-color);">
        <div style="font-size: 1.1em; font-weight: 500; color: var(--primary-text-color);">Terminarz: ${childName}</div>
        <div style="display: flex; gap: 4px;">
          <ha-icon-button id="sort-asc" style="color: ${this._sortDir === 1 ? 'var(--primary-color)' : 'var(--disabled-text-color)'};">
            <ha-icon icon="hass:arrow-up"></ha-icon>
          </ha-icon-button>
          <ha-icon-button id="sort-desc" style="color: ${this._sortDir === -1 ? 'var(--primary-color)' : 'var(--disabled-text-color)'};">
            <ha-icon icon="hass:arrow-down"></ha-icon>
          </ha-icon-button>
        </div>
      </div>`;
    const today = new Date().toISOString().split('T')[0];
    const sortedLista = [...state.attributes.lista].filter(i => i.data >= today).sort((a, b) => (new Date(a.data) - new Date(b.data)) * this._sortDir);
    if (sortedLista.length === 0) {
      html += `<div style="text-align: center; padding: 20px; opacity: 0.5;">Brak zadań i sprawdzianów.</div>`;
    } else {
      sortedLista.forEach(i => {
        const isT = i.typ.toLowerCase().includes("sprawdzian") || i.typ.toLowerCase().includes("klasówka");
        const isQ = i.typ.toLowerCase().includes("kartkówka");
        let bc = "#2196F3"; if (isT) bc = "#f44336"; if (isQ) bc = "#ff9800";
        html += `
          <div style="margin-bottom: 10px; padding: 10px; background: var(--secondary-background-color); border-radius: 8px; border-left: 5px solid ${bc};">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
              <span style="font-weight: bold; color: var(--primary-text-color);">${i.przedmiot}</span>
              <span style="font-weight: bold; color: var(--primary-color); background: var(--card-background-color); padding: 2px 6px; border-radius: 4px; font-size: 0.85em;">${i.data.split('-').reverse().slice(0,2).join('.')}</span>
            </div>
            <div style="font-size: 0.9em;"><b style="color: ${bc};">${i.typ}</b>: ${i.opis}</div>
            <div style="font-size: 0.75em; opacity: 0.5; margin-top: 6px; font-style: italic;">Nauczyciel: ${i.autor || 'Nieznany'}</div>
          </div>`;
      });
    }
    this.content.innerHTML = html;
    this.content.querySelector('#sort-asc').addEventListener('click', () => { this._sortDir = 1; this.hass = this._hass; });
    this.content.querySelector('#sort-desc').addEventListener('click', () => { this._sortDir = -1; this.hass = this._hass; });
  }
  setConfig(config) { this.config = config; }
}
customElements.define("vultron-work-card", VultronWorkCard);