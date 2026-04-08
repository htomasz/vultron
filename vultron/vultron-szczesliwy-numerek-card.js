class VultronSzczesliwyNumerekCard extends HTMLElement {
  constructor() {
    super();
    this._lastNumer = null;           // cache ostatniej wartości numerka
  }

  set hass(hass) {
    this._hass = hass;

    // Szybkie wyjście jeśli numer się nie zmienił
    const stateObj = hass.states[this.config.entity];
    if (!stateObj) return;

    const numerData = stateObj.state;
    if (numerData === this._lastNumer) {
      return;  // nic się nie zmieniło → nie rerenderujemy
    }
    this._lastNumer = numerData;

    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <style>
            .lucky-container {
              padding: 16px;
            }
            .lucky-header {
              display: flex;
              justify-content: space-between;
              align-items: center;
              margin-bottom: 12px;
              border-bottom: 2px solid var(--primary-color);
              padding-bottom: 8px;
            }
            .lucky-title {
              font-size: 1.1em;
              font-weight: 500;
              color: var(--primary-text-color);
            }
            .lucky-icon {
              color: var(--primary-color);
              transition: transform 0.3s ease-in-out;
            }
            .lucky-icon:hover {
              transform: scale(1.2) rotate(15deg);
            }
            .lucky-content {
              text-align: center;
              padding: 20px 0;
            }
            .lucky-number {
              font-size: 4em;
              font-weight: bold;
              color: var(--primary-text-color);
              text-shadow: 2px 2px 4px rgba(0,0,0,0.1);
            }
            .lucky-none {
              font-size: 1.5em;
              color: var(--secondary-text-color);
              font-style: italic;
            }
          </style>

          <div class="lucky-container">
            <div class="lucky-header">
              <div id="title" class="lucky-title">Szczęśliwy Numerek</div>
              <ha-icon class="lucky-icon" icon="mdi:clover"></ha-icon>
            </div>
            <div class="lucky-content">
              <div id="number-display">--</div>
            </div>
          </div>
        </ha-card>
      `;
      this.titleEl = this.querySelector('#title');
      this.numberEl = this.querySelector('#number-display');
      this.content = true;
    }

    this.renderData();
  }

  renderData() {
    const entityId = this.config.entity;
    const stateObj = this._hass.states[entityId];
    if (!stateObj) return;

    this.titleEl.innerText = stateObj.attributes.friendly_name || "Szczęśliwy Numerek";

    const numerData = stateObj.state;

    if (!numerData || numerData === "0" || numerData === "unknown" || numerData === "unavailable") {
      this.numberEl.className = "lucky-none";
      this.numberEl.innerText = "Brak";
    } else {
      this.numberEl.className = "lucky-number";
      this.numberEl.innerText = numerData;
    }
  }

  disconnectedCallback() {
    // Na wszelki wypadek – choć karta nie ma timerów ani listenerów
    this._lastNumer = null;
  }

  setConfig(config) {
    if (!config.entity) throw new Error('Entity missing. Please specify an entity.');
    this.config = config;
  }

  getCardSize() {
    return 2;
  }
}

customElements.define('vultron-szczesliwy-numerek-card', VultronSzczesliwyNumerekCard);

