class VultronPlanCard extends HTMLElement {
  constructor() {
    super();
    this._weekOffset = 0; 
    this._updateInterval = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <div style="padding: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
              <ha-icon-button id="prev-week" style="cursor: pointer;"><ha-icon icon="hass:chevron-left"></ha-icon></ha-icon-button>
              <div style="text-align: center;">
                <div id="student-name" style="font-size: 0.85em; opacity: 0.6; text-transform: uppercase;"></div>
                <div id="week-label" style="font-weight: bold; font-size: 1.1em; color: var(--primary-color);"></div>
              </div>
              <ha-icon-button id="next-week" style="cursor: pointer;"><ha-icon icon="hass:chevron-right"></ha-icon></ha-icon-button>
            </div>
            <div style="overflow-x: auto;">
              <table style="width: 100%; border-collapse: collapse; table-layout: fixed; min-width: 650px; border: 1px solid var(--divider-color);">
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
        </ha-card>
      `;
      this.content = this.querySelector('#plan-body');
      this.weekLabel = this.querySelector('#week-label');
      this.studentLabel = this.querySelector('#student-name');
      this.dayHeaders = this.querySelectorAll('.day-header');
      
      this.querySelector('#prev-week').addEventListener('click', () => { this._weekOffset--; this.updatePlan(); });
      this.querySelector('#next-week').addEventListener('click', () => { this._weekOffset++; this.updatePlan(); });
    }
    
    if (!this._updateInterval) {
      this._updateInterval = setInterval(() => this.updatePlan(), 30000);
    }
    this.updatePlan();
  }

  normalizeDate(d) {
    if (!d) return "";
    return d.toString().replace(/[\.\-]/g, "");
  }

  isCurrentLesson(dateStr, slot) {
    const now = new Date();
    const todayNum = now.getFullYear() + String(now.getMonth() + 1).padStart(2, '0') + String(now.getDate()).padStart(2, '0');
    if (this.normalizeDate(dateStr) !== todayNum) return false;

    try {
      const times = slot.split(/[-–—]/).map(t => t.trim());
      const [startH, startM] = times[0].split(':').map(Number);
      const [endH, endM] = times[1].split(':').map(Number);
      const currentMin = (now.getHours() * 60) + now.getMinutes();
      return currentMin >= (startH * 60 + startM) && currentMin < (endH * 60 + endM);
    } catch (e) { return false; }
  }

  updatePlan() {
    if (!this._hass || !this.config.entity) return;
    const state = this._hass.states[this.config.entity];
    if (!state || !state.attributes.lekcje) return;

    const now = new Date();
    const dayOfWeek = now.getDay() || 7;
    const monday = new Date(now);
    monday.setDate(now.getDate() - dayOfWeek + 1 + (this._weekOffset * 7));
    
    const weekDates = [];
    const dayNames = ["PON", "WT", "ŚR", "CZW", "PT"];

    for(let i=0; i<5; i++) {
        const d = new Date(monday);
        d.setDate(monday.getDate() + i);
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        weekDates.push(`${y}-${m}-${day}`);
        if (this.dayHeaders[i]) {
            this.dayHeaders[i].innerHTML = `${dayNames[i]}<br><span style="font-size: 0.75em; font-weight: normal; opacity: 0.7;">${day}.${m}</span>`;
        }
    }

    this.studentLabel.innerText = (state.attributes.friendly_name || '').replace('Plan: ', '');
    this.weekLabel.innerText = this._weekOffset === 0 ? "OBECNY TYDZIEŃ" : `OKRES ${weekDates[0]} - ${weekDates[4]}`;

    const lekcje = state.attributes.lekcje;
    // Pobierz wszystkie unikalne godziny (sloty) dla obecnego widoku tygodnia
    const slots = [...new Set(lekcje.filter(l => weekDates.includes(l.d)).map(l => l.g))].sort();

    let html = "";
    slots.forEach(slot => {
        const currentDayIdx = now.getDay() - 1;
        const isCurrentRow = this._weekOffset === 0 && this.isCurrentLesson(weekDates[currentDayIdx], slot);
        
        html += `<tr>`;
        let hrStyle = `padding: 10px 5px; text-align: center; border: 1px solid var(--divider-color); font-size: 0.8em; `;
        if (isCurrentRow) {
            hrStyle += `background: var(--accent-color) !important; color: white !important; font-weight: bold;`;
        } else {
            hrStyle += `background: var(--card-background-color); color: var(--primary-color);`;
        }
        html += `<td style="${hrStyle}">${slot}</td>`;
        
        weekDates.forEach(date => {
            // Filtrujemy WSZYSTKIE lekcje dla tego slotu i tego dnia
            const lessonsInSlot = lekcje.filter(lek => lek.g === slot && this.normalizeDate(lek.d) === this.normalizeDate(date));
            
            if (lessonsInSlot.length > 0) {
                const isCurrent = this._weekOffset === 0 && this.isCurrentLesson(date, slot);
                let cellContent = "";
                
                lessonsInSlot.forEach((l, index) => {
                    let textStyle = "font-weight: 500; font-size: 0.9em; line-height: 1.2;";
                    let statusTag = "";
                    let blockBg = "transparent";

                    if (l.st === 'ODWOL') {
                        textStyle += " text-decoration: line-through; opacity: 0.5;";
                        statusTag = "<div style='color: var(--error-color); font-size: 0.7em; font-weight: bold;'>ODWOŁANE</div>";
                    } else if (l.st === 'ZWOL') {
                        blockBg = "rgba(76, 175, 80, 0.1)";
                        textStyle += " text-decoration: line-through;";
                        statusTag = "<div style='color: #2e7d32; font-size: 0.7em; font-weight: bold;'>DO DOMU</div>";
                    } else if (l.st === 'ZAST') {
                        blockBg = "rgba(255, 165, 0, 0.1)";
                        statusTag = "<div style='color: #ef6c00; font-size: 0.7em; font-weight: bold;'>ZASTĘPSTWO</div>";
                    }

                    // Separator jeśli jest więcej niż jedna lekcja
                    const separator = index > 0 ? "border-top: 1px dashed var(--divider-color); margin-top: 5px; padding-top: 5px;" : "";

                    cellContent += `
                        <div style="${separator} background-color: ${blockBg};">
                            <div style="${textStyle}">${l.p}</div>
                            <div style="font-size: 0.7em; opacity: 0.7;">${l.s} ${l.n ? ' • ' + l.n : ''}</div>
                            ${statusTag}
                        </div>`;
                });

                let tdStyle = `padding: 6px 4px; border: ${isCurrent ? '2px solid var(--accent-color) !important' : '1px solid var(--divider-color)'}; vertical-align: top; position: relative;`;
                if (isCurrent) tdStyle += " background-color: rgba(33, 150, 243, 0.05) !important;";

                html += `
                    <td style="${tdStyle}">
                        ${cellContent}
                        ${isCurrent ? '<div style="position: absolute; top: 0; right: 0; font-size: 0.5em; background: var(--accent-color); color: white; padding: 1px 3px; font-weight: bold; border-bottom-left-radius: 4px; z-index: 5;">TERAZ</div>' : ''}
                    </td>`;
            } else {
                html += `<td style="border: 1px solid var(--divider-color); background: rgba(0,0,0,0.02);"></td>`;
            }
        });
        html += `</tr>`;
    });

    this.content.innerHTML = html || `<tr><td colspan="6" style="text-align: center; padding: 20px;">Brak danych w tym tygodniu</td></tr>`;
  }

  setConfig(config) { this.config = config; }
  getCardSize() { return 10; }
}
customElements.define("vultron-card", VultronPlanCard);

