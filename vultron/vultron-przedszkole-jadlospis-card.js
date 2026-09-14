class VultronPrzedszkoleJadlospisCard extends HTMLElement {
  constructor() {
    super();
    this._dayOffset = 0; // 0 = dziś, 1 = jutro
    this._expanded = new Set(); // klucze rozwiniętych posiłków
    this._cachedState = null;
    this._cachedDayOffset = null;
  }

  // Zabezpieczenie przed atakami XSS - nazwy posiłków, składniki i alergeny
  // pochodzą z zewnętrznego API (Vulcan), traktujemy je jak niezaufany input,
  // tak samo jak w pozostałych kartach dodatku.
  _esc(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  getFormattedDate(d) {
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  set hass(hass) {
    this._hass = hass;

    if (!this.config || !this.config.entity) return;

    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <div style="padding: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
              <div style="text-align: center; flex: 1;">
                <div id="student-name" style="font-size: 1.1em; font-weight: 500; color: #00bcd4;"></div>
              </div>
            </div>

            <div id="day-tabs" style="display: flex; gap: 6px; margin-bottom: 12px;">
              <button id="tab-today" style="flex:1; padding:8px; border:none; border-radius:8px; cursor:pointer; font-weight:bold; font-size:0.85em;">DZIŚ</button>
              <button id="tab-tomorrow" style="flex:1; padding:8px; border:none; border-radius:8px; cursor:pointer; font-weight:bold; font-size:0.85em;">JUTRO</button>
            </div>

            <div id="meals-list"></div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#meals-list');
      this.studentLabel = this.querySelector('#student-name');
      this.tabToday = this.querySelector('#tab-today');
      this.tabTomorrow = this.querySelector('#tab-tomorrow');

      this.tabToday.addEventListener('click', () => {
        this._dayOffset = 0;
        this._expanded.clear();
        this._forceUpdate();
      });
      this.tabTomorrow.addEventListener('click', () => {
        this._dayOffset = 1;
        this._expanded.clear();
        this._forceUpdate();
      });

      // Delegacja zdarzeń - obsługuje kliknięcia w nagłówki posiłków
      // dodawane dynamicznie przy każdym renderowaniu.
      this.content.addEventListener('click', (ev) => {
        const header = ev.target.closest('[data-meal-key]');
        if (!header) return;
        const key = header.getAttribute('data-meal-key');
        if (this._expanded.has(key)) {
          this._expanded.delete(key);
        } else {
          this._expanded.add(key);
        }
        this._renderMeals(this._lastDayMeals || []);
      });
    }

    const state = this._hass.states[this.config.entity];

    if (this._cachedState === state && this._cachedDayOffset === this._dayOffset) {
      return;
    }
    this._cachedState = state;
    this._cachedDayOffset = this._dayOffset;

    this._render(state);
  }

  _forceUpdate() {
    this._cachedDayOffset = null;
    this.hass = this._hass;
  }

  _updateTabStyles() {
    const activeStyle = 'background: var(--primary-color); color: white;';
    const inactiveStyle = 'background: var(--secondary-background-color); color: var(--primary-text-color);';
    this.tabToday.style.cssText += this._dayOffset === 0 ? activeStyle : inactiveStyle;
    this.tabTomorrow.style.cssText += this._dayOffset === 1 ? activeStyle : inactiveStyle;
  }

  _render(state) {
    this._updateTabStyles();

    if (!state) {
      this.content.innerHTML = `<div style="text-align:center; padding:20px;">Brak danych jadłospisu</div>`;
      return;
    }

    this.studentLabel.innerText = (state.attributes.friendly_name || '').replace('Jadłospis (przedszkole): ', '');

    const targetDate = new Date();
    targetDate.setDate(targetDate.getDate() + this._dayOffset);
    const targetISO = this.getFormattedDate(targetDate);

    const dni = state.attributes.dni || {};
    const meals = dni[targetISO] || [];
    this._lastDayMeals = meals;

    this._renderMeals(meals);
  }

  _renderMeals(meals) {
    if (!meals || meals.length === 0) {
      this.content.innerHTML = `<div style="text-align:center; padding:20px; opacity:0.7;">Brak jadłospisu na ten dzień</div>`;
      return;
    }

    let html = '';
    meals.forEach((posilek, idx) => {
      const key = `${idx}`;
      const isOpen = this._expanded.has(key);
      const alergeny = posilek.alergeny || [];
      const sklad = posilek.sklad || [];
      const szczegoly = posilek.szczegoly || [];

      html += `
        <div style="border: 1px solid var(--divider-color); border-radius: 8px; margin-bottom: 8px; overflow: hidden;">
          <div data-meal-key="${key}" style="padding: 10px 12px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; background: var(--card-background-color);">
            <span style="font-weight: 600; font-size: 0.95em;">${this._esc(posilek.nazwa)}</span>
            <ha-icon icon="${isOpen ? 'mdi:chevron-up' : 'mdi:chevron-down'}" style="--mdc-icon-size: 20px; opacity: 0.6;"></ha-icon>
          </div>
          ${isOpen ? this._renderMealDetails(sklad, alergeny, szczegoly) : ''}
        </div>`;
    });

    this.content.innerHTML = html;
  }

  _renderMealDetails(sklad, alergeny, szczegoly) {
    let html = `<div style="padding: 10px 12px; border-top: 1px solid var(--divider-color); background: var(--secondary-background-color);">`;

    if (sklad.length > 0) {
      html += `<div style="font-size: 0.8em; opacity: 0.8; margin-bottom: 8px;">`;
      html += sklad.map(s => this._esc(s.receptura)).join('<br>');
      html += `</div>`;
    }

    if (alergeny.length > 0) {
      html += `<div style="display:flex; flex-wrap:wrap; gap:4px; margin-bottom: 10px;">`;
      alergeny.forEach(a => {
        html += `<span style="background:#f44336; color:white; font-size:0.68em; padding:2px 7px; border-radius:10px; font-weight:600;">${this._esc(a)}</span>`;
      });
      html += `</div>`;
    }

    if (szczegoly.length > 0) {
      html += `<table style="width:100%; border-collapse:collapse; font-size:0.75em;">`;
      szczegoly.forEach(sz => {
        html += `
          <tr style="border-bottom: 1px solid var(--divider-color);">
            <td style="padding: 3px 0; opacity: 0.75;">${this._esc(sz.label)}</td>
            <td style="padding: 3px 0; text-align: right; font-weight: 600;">${this._esc(sz.wartosc)}</td>
          </tr>`;
      });
      html += `</table>`;
    }

    html += `</div>`;
    return html;
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Entity missing");
    this.config = config;
  }

  getCardSize() { return 6; }
}

customElements.define("vultron-przedszkole-jadlospis-card", VultronPrzedszkoleJadlospisCard);
