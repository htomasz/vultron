class VultronPrzedszkolePlanCard extends HTMLElement {
  constructor() {
    super();
    this._weekOffset = 0;
    this._lineUpdater = null;

    // Cache chroniący przed wyciekami CPU (Render Leak)
    this._cachedPlanState = null;
    this._cachedWeekOffset = null;
  }

  // Zabezpieczenie przed atakami XSS - dane (nazwy zajęć, prowadzący)
  // pochodzą z zewnętrznego API (Vulcan), więc traktujemy je jako
  // niezaufany input, tak samo jak w pozostałych kartach dodatku.
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

    // 1. INICJALIZACJA DOM I ZDARZEŃ (Wykona się tylko raz!)
    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <div style="padding: 16px; position: relative;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
              <ha-icon-button id="prev-week" style="--mdc-icon-button-size: 32px; cursor: pointer; color: var(--primary-color);">
                <ha-icon icon="mdi:chevron-left"></ha-icon>
              </ha-icon-button>

              <div style="text-align: center; flex: 1;">
                <div id="student-name" style="font-size: 1.1em; font-weight: 500; color: #00bcd4; text-align: center;"></div>
                <div id="week-label" style="font-weight: bold; font-size: 0.8em; color: var(--primary-color); text-transform: uppercase; letter-spacing: 1px; margin-top: 2px;"></div>
              </div>

              <ha-icon-button id="next-week" style="--mdc-icon-button-size: 32px; cursor: pointer; color: var(--primary-color);">
                <ha-icon icon="mdi:chevron-right"></ha-icon>
              </ha-icon-button>
            </div>

            <div id="table-wrapper" style="overflow-x: auto; border: 1px solid var(--divider-color); border-radius: 8px;">
              <div style="position: relative; min-width: 650px; width: 100%;">
                <table style="width: 100%; border-collapse: collapse; table-layout: fixed; min-width: 650px; border: none;">
                  <thead>
                    <tr style="background: var(--secondary-background-color);">
                      <th class="pplan-day-header" style="padding: 10px; border: 1px solid var(--divider-color);">PON</th>
                      <th class="pplan-day-header" style="padding: 10px; border: 1px solid var(--divider-color);">WT</th>
                      <th class="pplan-day-header" style="padding: 10px; border: 1px solid var(--divider-color);">ŚR</th>
                      <th class="pplan-day-header" style="padding: 10px; border: 1px solid var(--divider-color);">CZW</th>
                      <th class="pplan-day-header" style="padding: 10px; border: 1px solid var(--divider-color);">PT</th>
                    </tr>
                  </thead>
                  <tbody id="plan-body"></tbody>
                </table>
              </div>
            </div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#plan-body');
      this.weekLabel = this.querySelector('#week-label');
      this.studentLabel = this.querySelector('#student-name');
      this.dayHeaders = this.querySelectorAll('.pplan-day-header');

      // Podpinanie zdarzeń TYLKO RAZ
      this.querySelector('#prev-week').addEventListener('click', () => {
        if (this._weekOffset > -1) {
          this._weekOffset--;
          this._forceUpdate();
        }
      });

      this.querySelector('#next-week').addEventListener('click', () => {
        if (this._weekOffset < 1) {
          this._weekOffset++;
          this._forceUpdate();
        }
      });

      // Oznaczenie "TERAZ" zależy od zegara, nie od stanu encji - odświeżamy
      // je co minutę, bo sama encja zmienia się rzadko.
      this._lineUpdater = setInterval(() => this._refreshNow(), 60000);
    }

    if (!this.config || !this.config.entity) return;

    let suffix = this._weekOffset === 0 ? 'curr' : (this._weekOffset === -1 ? 'prev' : 'next');
    let baseEntity = this.config.entity.replace(/_(prev|curr|next)$/, '');
    let entityId = `${baseEntity}_${suffix}`;

    const planState = this._hass.states[entityId];

    // 2. STATE CACHING
    if (
      this._cachedPlanState === planState &&
      this._cachedWeekOffset === this._weekOffset
    ) {
      return;
    }

    this._cachedPlanState = planState;
    this._cachedWeekOffset = this._weekOffset;

    // 3. Renderowanie
    this.updatePlan(planState, suffix);
  }

  _forceUpdate() {
    this._cachedWeekOffset = null;
    this.hass = this._hass;
  }

  disconnectedCallback() {
    if (this._lineUpdater) {
      clearInterval(this._lineUpdater);
      this._lineUpdater = null;
    }
  }

  getFormattedDate(d) {
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  // Zamienia "HH:MM-HH:MM" na [start, koniec] w minutach od północy.
  // Niepoprawny format -> [null, null] (zajęcia i tak są pokazane, tylko bez "TERAZ").
  _slotMinutes(slot) {
    const m = /^(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})$/.exec(String(slot ?? '').trim());
    if (!m) return [null, null];
    return [Number(m[1]) * 60 + Number(m[2]), Number(m[3]) * 60 + Number(m[4])];
  }

  _refreshNow() {
    if (!this.isConnected || this._weekOffset !== 0 || !this._cachedPlanState) return;
    this.updatePlan(this._cachedPlanState, 'curr');
  }

  // Przedszkole ma nieregularne, nakładające się bloki godzinowe, więc zamiast
  // siatki wspólnych slotów (jak w planie szkolnym) każdy dzień to osobna
  // kolumna z chronologiczną listą zajęć.
  updatePlan(planState, suffix) {
    if (!planState || !planState.attributes.zajecia) {
      this.content.innerHTML = `<tr><td colspan="5" style="text-align: center; padding: 20px;">Brak danych planu (${suffix})</td></tr>`;
      return;
    }

    const todayISO = this.getFormattedDate(new Date()), now = new Date();
    const dayOfWeek = now.getDay() || 7;
    const monday = new Date(now);
    monday.setDate(now.getDate() - dayOfWeek + 1 + (this._weekOffset * 7));
    const weekDates = [];

    for (let i = 0; i < 5; i++) {
      const d = new Date(monday); d.setDate(monday.getDate() + i);
      const dISO = this.getFormattedDate(d); weekDates.push(dISO);
      if (this.dayHeaders[i]) {
        const isToday = dISO === todayISO;
        this.dayHeaders[i].innerHTML = `
          ${["PON", "WT", "ŚR", "CZW", "PT"][i]}<br>
          <span style="font-weight: bold; color: var(--primary-color); background: var(--secondary-background-color); padding: 2px 6px; border-radius: 6px; font-size: 0.78em; white-space: nowrap;">
            ${dISO}
          </span>
        `;
        this.dayHeaders[i].style.background = isToday ? "rgba(var(--rgb-primary-color), 0.15)" : "transparent";
        this.dayHeaders[i].style.borderBottom = isToday ? "3px solid var(--accent-color)" : "1px solid var(--divider-color)";
      }
    }

    this.studentLabel.innerText = (planState.attributes.friendly_name || '').replace(/Plan przedszkola (prev|curr|next): /, '');
    this.weekLabel.innerText = this._weekOffset === 0 ? "OBECNY TYDZIEŃ" : (this._weekOffset === -1 ? "POPRZEDNI TYDZIEŃ" : "NASTĘPNY TYDZIEŃ");

    const zajecia = planState.attributes.zajecia || [];
    if (zajecia.length === 0) {
      this.content.innerHTML = `<tr><td colspan="5" style="text-align: center; padding: 20px;">Brak zajęć</td></tr>`;
      return;
    }

    const nowM = now.getHours() * 60 + now.getMinutes();
    let html = "<tr>";

    weekDates.forEach(date => {
      const isToday = date === todayISO;
      const dayItems = zajecia
        .filter(z => z.d === date)
        .sort((a, b) => String(a.g ?? '').localeCompare(String(b.g ?? '')));

      let cellContent = "";
      dayItems.forEach(z => {
        const [sM, eM] = this._slotMinutes(z.g);
        const isCur = isToday && sM !== null && nowM >= sM && nowM < eM;
        cellContent += `
          <div style="padding: 6px 8px; margin-bottom: 6px; border-radius: 6px; border-left: 3px solid ${isCur ? 'var(--accent-color)' : 'var(--primary-color)'}; background: ${isCur ? 'rgba(var(--rgb-accent-color), 0.15)' : 'var(--secondary-background-color)'};">
            <div style="font-size: 0.75em; font-weight: bold; color: var(--primary-color);">
              ${this._esc(z.g)}
              ${isCur ? '<span style="margin-left: 4px; font-size: 0.85em; background: var(--accent-color); color: white; padding: 0 4px; border-radius: 4px;">TERAZ</span>' : ''}
            </div>
            <div style="font-weight: 600; font-size: 0.9em; line-height: 1.2; margin-top: 2px;">${this._esc(z.z)}</div>
            ${z.n ? `<div style="font-size: 0.72em; opacity: 0.7; margin-top: 1px;">${this._esc(z.n)}</div>` : ''}
          </div>`;
      });

      if (!cellContent) {
        cellContent = '<div style="text-align: center; opacity: 0.4; padding: 8px 0; font-size: 0.8em;">—</div>';
      }

      const todayBg = isToday ? `background: rgba(var(--rgb-primary-color), 0.05);` : `background: transparent;`;
      html += `
        <td style="padding: 6px; border: 1px solid var(--divider-color); vertical-align: top; ${todayBg}">
          ${cellContent}
        </td>`;
    });

    this.content.innerHTML = html + "</tr>";
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Entity missing");
    this.config = config;
  }

  getCardSize() { return 6; }
}

customElements.define("vultron-przedszkole-plan-card", VultronPrzedszkolePlanCard);
