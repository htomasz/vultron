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

                <!-- ŻÓŁTA KRESKA CZASU -->
                <div id="time-line" style="display: none; position: absolute; left: 85px; right: 0; height: 2px; background: #ffff00; z-index: 1000; pointer-events: none; box-shadow: 0 0 4px rgba(255, 255, 0, 0.6);">
                  <div id="time-label" style="position: absolute; left: -85px; top: -10px; width: 85px; height: 20px; background: #ffff00; color: #000 !important; font-size: 12px; font-weight: 900; text-align: center; line-height: 20px; border-radius: 0 10px 10px 0; box-shadow: 2px 0 5px rgba(0,0,0,0.3); z-index: 1001;">--:--</div>
                </div>

                <table style="width: 100%; border-collapse: collapse; table-layout: fixed; min-width: 650px; border: none;">
                  <thead>
                    <tr style="background: var(--secondary-background-color);">
                      <th style="width: 85px; padding: 10px; border: 1px solid var(--divider-color); font-size: 0.8em;">GODZINA</th>
                      <th class="day-header" style="padding: 10px; border: 1px solid var(--divider-color);">PON</th>
                      <th class="day-header" style="padding: 10px; border: 1px solid var(--divider-color);">WT</th>
                      <th class="day-header" style="padding: 10px; border: 1px solid var(--divider-color);">ŚR</th>
                      <th class="day-header" style="padding: 10px; border: 1px solid var(--divider-color);">CZW</th>
                      <th class="day-header" style="padding: 10px; border: 1px solid var(--divider-color);">PT</th>
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
      this.dayHeaders = this.querySelectorAll('.day-header');
      this.timeLine = this.querySelector('#time-line');
      this.timeLabel = this.querySelector('#time-label');

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

      this._lineUpdater = setInterval(() => this.positionLine(), 10000);
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
      // Pilnujemy, by kreska nie znikała przy innych odświeżeniach HA
      this.positionLine();
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

  // Rysowanie żółtej kreski aktualnego czasu (identyczna logika jak w
  // karcie planu ucznia szkoły - przedszkole ma ten sam model godzinowy).
  positionLine() {
    if (!this.isConnected) return;
    if (this._weekOffset !== 0 || !this.content || !this.timeLine) {
      if (this.timeLine) this.timeLine.style.display = 'none';
      return;
    }

    const rows = Array.from(this.content.querySelectorAll('tr'));
    if (rows.length === 0 || rows[0].innerText.includes('Brak')) {
      this.timeLine.style.display = 'none';
      return;
    }

    const now = new Date();
    const h = now.getHours(), m = String(now.getMinutes()).padStart(2, '0');
    if (this.timeLabel) this.timeLabel.innerText = `${h}:${m}`;
    const cur = h * 60 + now.getMinutes();

    let pos = -1;
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      const timeCell = row.querySelector('td');
      if (!timeCell) continue;

      // Jeśli komórki jeszcze nie wyrenderowały swoich wymiarów, przerwij - złapie na kolejnym ticku
      if (timeCell.offsetHeight === 0) return;

      const slot = timeCell.innerText;
      const p = slot.split(/[-–—]/); if (p.length < 2) continue;

      const s = parseInt(p[0].split(':')[0], 10) * 60 + parseInt(p[0].split(':')[1], 10);
      const e = parseInt(p[1].split(':')[0], 10) * 60 + parseInt(p[1].split(':')[1], 10);

      if (cur >= s && cur <= e) {
        pos = timeCell.offsetTop + (timeCell.offsetHeight * ((cur - s) / (e - s)));
        break;
      }
      if (i < rows.length - 1) {
        const nextRow = rows[i + 1], nextSlotCell = nextRow.querySelector('td');
        if (!nextSlotCell) continue;

        const nextSlot = nextSlotCell.innerText;
        const nextS = parseInt(nextSlot.split(/[-–—]/)[0].split(':')[0], 10) * 60 + parseInt(nextSlot.split(/[-–—]/)[0].split(':')[1], 10);

        if (cur > e && cur < nextS) {
          pos = (timeCell.offsetTop + timeCell.offsetHeight) + ((nextSlotCell.offsetTop - (timeCell.offsetTop + timeCell.offsetHeight)) * ((cur - e) / (nextS - e)));
          break;
        }
      }
    }

    if (pos !== -1) {
      this.timeLine.style.top = pos + "px";
      this.timeLine.style.display = 'block';
    } else {
      this.timeLine.style.display = 'none';
    }
  }

  updatePlan(planState, suffix) {
    if (!planState || !planState.attributes.zajecia) {
      this.content.innerHTML = `<tr><td colspan="6" style="text-align: center; padding: 20px;">Brak danych planu (${suffix})</td></tr>`;
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
    const slots = [...new Set(zajecia.map(z => z.g))].sort();

    let html = "";

    slots.forEach((slot) => {
      const [sT, eT] = slot.split(/[-–—]/);
      const sM = parseInt(sT.split(':')[0]) * 60 + parseInt(sT.split(':')[1]);
      const eM = parseInt(eT.split(':')[0]) * 60 + parseInt(eT.split(':')[1]);
      const nowM = now.getHours() * 60 + now.getMinutes();
      const isNow = this._weekOffset === 0 && nowM >= sM && nowM < eM;

      html += `<tr><td style="padding: 10px 5px; text-align: center; border: 1px solid var(--divider-color); font-size: 0.8em; background: ${isNow ? 'var(--accent-color)' : 'var(--card-background-color)'}; color: ${isNow ? 'white' : 'inherit'}; font-weight: bold;">${this._esc(slot)}</td>`;

      weekDates.forEach(date => {
        const isToday = date === todayISO, isCur = isToday && isNow;
        const lessons = zajecia.filter(z => z.d === date && z.g === slot);
        let cellContent = "";
        lessons.forEach((z, idx) => {
          const sep = idx > 0 ? "border-top: 1px dashed var(--divider-color); margin-top: 5px; padding-top: 5px;" : "";
          cellContent += `
            <div style="${sep} position: relative; min-height: 45px; padding: 4px; border-radius: 4px;">
              <div style="font-weight: 600; font-size: 0.9em; line-height: 1.2;">${this._esc(z.z)}</div>
              <div style="font-size: 0.72em; opacity: 0.7; margin-top: 1px;">${z.n ? this._esc(z.n) : ''}</div>
            </div>`;
        });
        const highlightStyle = isCur ? `box-shadow: inset 0 0 0 2px var(--accent-color); z-index: 5; background: rgba(var(--rgb-accent-color), 0.1) !important;` : '';
        const todayBg = isToday ? `background: rgba(var(--rgb-primary-color), 0.05);` : `background: transparent;`;
        html += `
          <td style="padding: 4px; border: 1px solid var(--divider-color); vertical-align: top; position: relative; ${todayBg} ${highlightStyle}">
            ${isCur ? '<div style="position: absolute; top: 0; right: 0; font-size: 0.5em; background: var(--accent-color); color: white; padding: 1px 4px; font-weight: bold; border-bottom-left-radius: 4px; z-index: 6;">TERAZ</div>' : ''}
            ${cellContent}
          </td>`;
      });
      html += `</tr>`;
    });

    this.content.innerHTML = html || `<tr><td colspan="6" style="text-align: center; padding: 20px;">Brak zajęć</td></tr>`;

    // Potrójny strzał do układu graficznego (niweluje opóźnienia HA)
    if (this.isConnected) {
      this.positionLine(); // Próba 1 (natychmiast)
      setTimeout(() => this.positionLine(), 100); // Próba 2 (po 0.1s)
      setTimeout(() => this.positionLine(), 400); // Próba 3 (po ułożeniu kafelków przez HA)
    }
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Entity missing");
    this.config = config;
  }

  getCardSize() { return 6; }
}

customElements.define("vultron-przedszkole-plan-card", VultronPrzedszkolePlanCard);