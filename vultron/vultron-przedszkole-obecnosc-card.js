class VultronPrzedszkoleObecnoscCard extends HTMLElement {
  constructor() {
    super();
    this._monthOffset = 0;
    this._activeTab = 'kalendarz'; // 'kalendarz' | 'godziny'
    this._cachedState = null;
    this._cachedMonthOffset = null;
    this._cachedTab = null;
  }

  // Zabezpieczenie przed atakami XSS - friendly_name pochodzi z konfiguracji
  // dodatku (imię i nazwisko dziecka), traktujemy je tak samo jak dane z
  // zewnętrznego API w pozostałych kartach.
  _esc(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  set hass(hass) {
    this._hass = hass;

    if (!this.config || !this.config.entity) return;

    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <div style="padding: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
              <ha-icon-button id="prev-month" style="--mdc-icon-button-size: 32px; cursor: pointer; color: var(--primary-color);">
                <ha-icon icon="mdi:chevron-left"></ha-icon>
              </ha-icon-button>

              <div style="text-align: center; flex: 1;">
                <div id="student-name" style="font-size: 1.1em; font-weight: 500; color: #00bcd4;"></div>
                <div id="month-label" style="font-weight: bold; font-size: 0.8em; color: var(--primary-color); text-transform: uppercase; letter-spacing: 1px; margin-top: 2px;"></div>
              </div>

              <ha-icon-button id="next-month" style="--mdc-icon-button-size: 32px; cursor: pointer; color: var(--primary-color);">
                <ha-icon icon="mdi:chevron-right"></ha-icon>
              </ha-icon-button>
            </div>

            <div id="today-status" style="text-align: center; margin-bottom: 14px; font-size: 0.95em; font-weight: 600;"></div>

            <div id="tab-row" style="display: flex; gap: 6px; margin-bottom: 12px;">
              <button id="tab-kalendarz" style="flex:1; padding:8px; border:none; border-radius:8px; cursor:pointer; font-weight:bold; font-size:0.85em;">KALENDARZ</button>
              <button id="tab-godziny" style="flex:1; padding:8px; border:none; border-radius:8px; cursor:pointer; font-weight:bold; font-size:0.85em;">GODZINY</button>
            </div>

            <div id="weekday-row" style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px; margin-bottom: 4px;">
              ${["PON", "WT", "ŚR", "CZW", "PT"].map(d =>
                `<div style="text-align:center; font-size:0.68em; opacity:0.6; font-weight:bold;">${d}</div>`
              ).join('')}
            </div>

            <div id="calendar-grid" style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px;"></div>

            <div id="hours-list" style="display: none;"></div>

            <div id="calendar-legend" style="display:flex; gap:14px; justify-content:center; margin-top:14px; font-size:0.75em; opacity:0.75;">
              <span><span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:#4caf50; margin-right:4px;"></span>Obecny</span>
              <span><span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:#f44336; margin-right:4px;"></span>Nieobecny</span>
              <span><span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:var(--divider-color); margin-right:4px;"></span>Brak danych</span>
            </div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#calendar-grid');
      this.hoursList = this.querySelector('#hours-list');
      this.weekdayRow = this.querySelector('#weekday-row');
      this.legend = this.querySelector('#calendar-legend');
      this.studentLabel = this.querySelector('#student-name');
      this.monthLabel = this.querySelector('#month-label');
      this.todayStatus = this.querySelector('#today-status');
      this.tabKalendarz = this.querySelector('#tab-kalendarz');
      this.tabGodziny = this.querySelector('#tab-godziny');

      this.tabKalendarz.addEventListener('click', () => {
        this._activeTab = 'kalendarz';
        this._forceUpdate();
      });
      this.tabGodziny.addEventListener('click', () => {
        this._activeTab = 'godziny';
        this._forceUpdate();
      });

      this.querySelector('#prev-month').addEventListener('click', () => {
        this._monthOffset--;
        this._forceUpdate();
      });
      this.querySelector('#next-month').addEventListener('click', () => {
        if (this._monthOffset < 0) {
          this._monthOffset++;
          this._forceUpdate();
        }
      });
    }

    const state = this._hass.states[this.config.entity];

    if (this._cachedState === state && this._cachedMonthOffset === this._monthOffset && this._cachedTab === this._activeTab) {
      return;
    }
    this._cachedState = state;
    this._cachedMonthOffset = this._monthOffset;
    this._cachedTab = this._activeTab;

    this._render(state);
  }

  _forceUpdate() {
    this._cachedTab = null;
    this.hass = this._hass;
  }

  getFormattedDate(d) {
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  _render(state) {
    if (!state) {
      this.content.innerHTML = `<div style="grid-column: 1 / -1; text-align: center; padding: 20px;">Brak danych obecności</div>`;
      this.todayStatus.innerText = '';
      return;
    }

    const historia = state.attributes.historia || [];
    const byDate = {};
    historia.forEach(h => { byDate[h.data] = h.obecnosc; });

    const now = new Date();
    const monthNames = ["Styczeń", "Luty", "Marzec", "Kwiecień", "Maj", "Czerwiec",
                        "Lipiec", "Sierpień", "Wrzesień", "Październik", "Listopad", "Grudzień"];

    this.studentLabel.innerText = (state.attributes.friendly_name || '').replace('Obecność (przedszkole): ', '');

    const todayISO = this.getFormattedDate(now);
    const todayVal = byDate[todayISO];
    if (todayVal === undefined) {
      this.todayStatus.innerHTML = `<span style="color: var(--secondary-text-color);">Dziś: brak jeszcze danych</span>`;
    } else if (todayVal) {
      this.todayStatus.innerHTML = `<span style="color: #4caf50;">Dziś: obecny</span>`;
    } else {
      this.todayStatus.innerHTML = `<span style="color: #f44336;">Dziś: nieobecny</span>`;
    }

    // Style zakładek
    const activeStyle = 'background: var(--primary-color); color: white;';
    const inactiveStyle = 'background: var(--secondary-background-color); color: var(--primary-text-color);';
    this.tabKalendarz.style.cssText += this._activeTab === 'kalendarz' ? activeStyle : inactiveStyle;
    this.tabGodziny.style.cssText += this._activeTab === 'godziny' ? activeStyle : inactiveStyle;

    if (this._activeTab === 'godziny') {
      // Zakładka "Godziny" pokazuje zawsze BIEŻĄCY miesiąc (tak dostarcza
      // backend - atrybut "miesiac" nie jest przesuwany strzałkami), więc
      // strzałki nawigacji kalendarza i sama siatka/legenda są tu ukryte.
      this.querySelector('#prev-month').style.visibility = 'hidden';
      this.querySelector('#next-month').style.visibility = 'hidden';
      this.monthLabel.innerText = `${monthNames[now.getMonth()]} ${now.getFullYear()}`;
      this.content.style.display = 'none';
      this.weekdayRow.style.display = 'none';
      this.legend.style.display = 'none';
      this.hoursList.style.display = 'block';
      this._renderHours(state.attributes.miesiac || []);
      return;
    }

    this.querySelector('#prev-month').style.visibility = 'visible';
    this.querySelector('#next-month').style.visibility = 'visible';
    this.content.style.display = 'grid';
    this.weekdayRow.style.display = 'grid';
    this.legend.style.display = 'flex';
    this.hoursList.style.display = 'none';

    const viewDate = new Date(now.getFullYear(), now.getMonth() + this._monthOffset, 1);
    this.monthLabel.innerText = `${monthNames[viewDate.getMonth()]} ${viewDate.getFullYear()}`;

    // Siatka kalendarza: puste komórki na początek (dopasowanie do dnia
    // tygodnia 1-go dnia miesiąca w 5-dniowym układzie Pon-Pt), potem po
    // jednej komórce na każdy dzień ROBOCZY miesiąca - soboty i niedziele
    // są pomijane całkowicie (przedszkole nie ma w te dni zajęć, więc nie
    // ma tam sensu pokazywać pustej/szarej komórki).
    const firstDay = new Date(viewDate.getFullYear(), viewDate.getMonth(), 1);
    const firstDayIdx = (firstDay.getDay() + 6) % 7; // 0 = poniedziałek ... 6 = niedziela
    const leadingBlanks = firstDayIdx > 4 ? 0 : firstDayIdx; // jeśli 1. dzień to weekend, brak wcięcia
    const daysInMonth = new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 0).getDate();

    let html = '';
    for (let i = 0; i < leadingBlanks; i++) {
      html += `<div></div>`;
    }

    for (let day = 1; day <= daysInMonth; day++) {
      const d = new Date(viewDate.getFullYear(), viewDate.getMonth(), day);
      const dayOfWeek = d.getDay(); // 0 = niedziela, 6 = sobota
      if (dayOfWeek === 0 || dayOfWeek === 6) continue; // pomiń weekend

      const dISO = this.getFormattedDate(d);
      const isToday = dISO === todayISO;
      const val = byDate[dISO];

      let dotColor = 'var(--divider-color)';
      if (val === true) dotColor = '#4caf50';
      else if (val === false) dotColor = '#f44336';

      const todayRing = isToday ? 'box-shadow: 0 0 0 2px var(--primary-color);' : '';

      html += `
        <div style="aspect-ratio: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; border-radius: 8px; ${todayRing} background: ${isToday ? 'rgba(var(--rgb-primary-color), 0.08)' : 'transparent'};">
          <div style="font-size: 0.75em; margin-bottom: 2px;">${day}</div>
          <div style="width: 9px; height: 9px; border-radius: 50%; background: ${dotColor};"></div>
        </div>`;
    }

    this.content.innerHTML = html;
  }

  _renderHours(miesiacWpisy) {
    const dniZObecnoscia = miesiacWpisy.filter(m => m.obecnosc);

    if (dniZObecnoscia.length === 0) {
      this.hoursList.innerHTML = `<div style="text-align:center; padding:20px; opacity:0.7;">Brak danych o godzinach w tym miesiącu</div>`;
      return;
    }

    let html = '';
    // Najnowsze na górze
    [...dniZObecnoscia].reverse().forEach(d => {
      const wej = d.godzina_wejscia || '—';
      const wyj = d.godzina_wyjscia || '—';
      let czasPobytu = '';
      if (d.godzina_wejscia && d.godzina_wyjscia) {
        const [wh, wm] = d.godzina_wejscia.split(':').map(Number);
        const [xh, xm] = d.godzina_wyjscia.split(':').map(Number);
        const minuty = (xh * 60 + xm) - (wh * 60 + wm);
        if (minuty > 0) {
          czasPobytu = `${Math.floor(minuty / 60)}h ${minuty % 60}min`;
        }
      }

      html += `
        <div style="display:flex; justify-content:space-between; align-items:center; padding:8px 10px; border-bottom:1px solid var(--divider-color);">
          <div style="font-weight:600; font-size:0.85em;">${this._esc(d.data)}</div>
          <div style="font-size:0.82em; opacity:0.85;">
            <ha-icon icon="mdi:login" style="--mdc-icon-size:14px; opacity:0.6;"></ha-icon> ${this._esc(wej)}
            &nbsp;→&nbsp;
            <ha-icon icon="mdi:logout" style="--mdc-icon-size:14px; opacity:0.6;"></ha-icon> ${this._esc(wyj)}
          </div>
          <div style="font-size:0.78em; font-weight:600; color:var(--primary-color); min-width:70px; text-align:right;">
            ${this._esc(czasPobytu)}
          </div>
        </div>`;
    });

    this.hoursList.innerHTML = html;
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Entity missing");
    this.config = config;
  }

  getCardSize() { return 5; }
}

customElements.define("vultron-przedszkole-obecnosc-card", VultronPrzedszkoleObecnoscCard);
