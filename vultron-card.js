class VultronPlanCard extends HTMLElement {
  constructor() {
    super();
    this._weekOffset = 0; // 0 = obecny, 1 = następny, -1 = poprzedni itd.
  }

  set hass(hass) {
    this._hass = hass;
    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <div style="padding: 16px;">
            <!-- Nagłówek i Nawigacja -->
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
              <ha-icon-button id="prev-week" style="cursor: pointer;">
                <ha-icon icon="hass:chevron-left"></ha-icon>
              </ha-icon-button>
              
              <div style="text-align: center;">
                <div id="student-name" style="font-size: 0.85em; opacity: 0.6; text-transform: uppercase; letter-spacing: 1px;"></div>
                <div id="week-label" style="font-weight: bold; font-size: 1.1em; color: var(--primary-color);"></div>
              </div>
              
              <ha-icon-button id="next-week" style="cursor: pointer;">
                <ha-icon icon="hass:chevron-right"></ha-icon>
              </ha-icon-button>
            </div>

            <!-- Tabela Planu -->
            <div style="overflow-x: auto;">
              <table style="width: 100%; border-collapse: collapse; table-layout: fixed; min-width: 650px; border: 1px solid var(--divider-color);">
                <thead>
                  <tr style="background: var(--secondary-background-color);">
                    <th style="width: 80px; padding: 10px; border: 1px solid var(--divider-color); font-size: 0.8em;">GODZINA</th>
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
      
      this.querySelector('#prev-week').addEventListener('click', () => { this._weekOffset--; this.updatePlan(); });
      this.querySelector('#next-week').addEventListener('click', () => { this._weekOffset++; this.updatePlan(); });
    }
    this.updatePlan();
  }

  updatePlan() {
    if (!this._hass || !this.config.entity) return;
    const state = this._hass.states[this.config.entity];
    if (!state || !state.attributes.lekcje) return;

    // 1. Obliczanie zakresu dat dla wybranego tygodnia
    const now = new Date();
    const dayOfWeek = now.getDay() || 7; // 1-7 (Pon-Nie)
    const monday = new Date(now);
    // Ustawienie na poniedziałek wybranego tygodnia
    monday.setDate(now.getDate() - dayOfWeek + 1 + (this._weekOffset * 7));
    
    const weekDates = [];
    for(let i=0; i<5; i++) {
        const d = new Date(monday);
        d.setDate(monday.getDate() + i);
        weekDates.push(d.toISOString().split('T')[0]);
    }

    // 2. Nagłówki dat
    const dayLabels = ["PON", "WT", "ŚR", "CZW", "PT"];
    this.querySelectorAll('.day-header').forEach((el, i) => {
        const d = weekDates[i].split('-');
        el.innerHTML = `${dayLabels[i]}<br><span style="font-size: 0.75em; font-weight: normal; opacity: 0.6;">${d[2]}.${d[1]}</span>`;
    });

    // 3. Etykieta tygodnia
    const firstDay = weekDates[0].split('-').reverse().slice(0,2).join('.');
    const lastDay = weekDates[4].split('-').reverse().slice(0,2).join('.');
    this.weekLabel.innerText = this._weekOffset === 0 ? "OBECNY TYDZIEŃ" : `${firstDay} - ${lastDay}`;
    this.studentLabel.innerText = state.attributes.friendly_name.replace('Plan: ', '');

    // 4. Budowanie wierszy (Godziny)
    const lekcje = state.attributes.lekcje;
    // Wyciągamy wszystkie sloty godzinowe które występują w tym konkretnym tygodniu
    const slots = [...new Set(lekcje.filter(l => weekDates.includes(l.d)).map(l => l.g))].sort();

    if (slots.length === 0) {
        this.content.innerHTML = `<tr><td colspan="6" style="text-align: center; padding: 40px; opacity: 0.5;">Brak zaplanowanych lekcji w tym tygodniu.</td></tr>`;
        return;
    }

    let html = "";
    slots.forEach(slot => {
        html += `<tr>`;
        // Kolumna godziny
        html += `<td style="padding: 12px 5px; text-align: center; font-weight: bold; color: var(--primary-color); border: 1px solid var(--divider-color); font-size: 0.85em; background: var(--card-background-color);">${slot}</td>`;
        
        // Kolumny dni
        weekDates.forEach(date => {
            const l = lekcje.find(lek => lek.g === slot && lek.d === date);
            if (l) {
                let cellStyle = "padding: 8px 4px; border: 1px solid var(--divider-color); vertical-align: top; position: relative; overflow: hidden;";
                let contentStyle = "font-weight: 500; font-size: 0.85em; line-height: 1.2;";
                let bg = "transparent";
                let tag = "";

                if (l.st === 'ODWOL') {
                    contentStyle += " text-decoration: line-through; opacity: 0.4;";
                    tag = "<div style='color: var(--error-color); font-size: 0.7em; font-weight: bold; margin-top: 2px;'>ODWOŁANE</div>";
                } else if (l.st === 'ZAST') {
                    bg = "rgba(255, 165, 0, 0.12)";
                    tag = "<div style='color: #ffa500; font-size: 0.7em; font-weight: bold; margin-top: 2px;'>ZASTĘPSTWO</div>";
                }

                html += `
                    <td style="${cellStyle} background-color: ${bg};">
                        <div style="${contentStyle}">${l.p}</div>
                        <div style="font-size: 0.75em; opacity: 0.6; margin-top: 2px;">${l.s || ''}</div>
                        ${tag}
                    </td>`;
            } else {
                html += `<td style="border: 1px solid var(--divider-color); background: rgba(0,0,0,0.02);"></td>`;
            }
        });
        html += `</tr>`;
    });

    this.content.innerHTML = html;
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Musisz podać encję (entity)");
    this.config = config;
  }

  getCardSize() { return 10; }
}
customElements.define("vultron-card", VultronPlanCard);
