class VultronPlanCard extends HTMLElement {
  constructor() {
    super();
    this._weekOffset = 0;
    this._lineUpdater = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this.content) {
      this.innerHTML = `
        <style>
          /* Style dla Glassmorphism Tooltip */
          .marker-wrapper {
            position: relative;
            display: inline-block;
            cursor: help;
          }
          .vultron-tooltip {
            visibility: hidden;
            opacity: 0;
            background: rgba(var(--rgb-card-background-color, 255, 255, 255), 0.7);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            text-align: center;
            border-radius: 6px;
            padding: 5px 10px;
            position: absolute;
            z-index: 100;
            bottom: 125%;
            right: 0;
            transform: translateY(10px);
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
            border: 1px solid var(--divider-color);
            transition: all 0.2s ease-in-out;
            pointer-events: none;
            font-size: 0.8em;
            white-space: nowrap;
            font-weight: bold;
          }
          .marker-wrapper:hover .vultron-tooltip {
            visibility: visible;
            opacity: 1;
            transform: translateY(0);
          }
        </style>
        <ha-card>
          <div style="padding: 16px; position: relative;">

            <!-- NAGŁÓWEK IDENTYCZNY JAK W OCENACH -->
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

                <!-- KRESKA CZASU -->
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

      this.querySelector('#prev-week').addEventListener('click', () => {
        if (this._weekOffset > -1) { this._weekOffset--; this.updatePlan(); }
      });
      this.querySelector('#next-week').addEventListener('click', () => {
        if (this._weekOffset < 1) { this._weekOffset++; this.updatePlan(); }
      });
    }
    this.updatePlan();
    if (!this._lineUpdater) this._lineUpdater = setInterval(() => this.positionLine(), 10000);
  }

  getFormattedDate(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }

  positionLine() {
    if (this._weekOffset !== 0 || !this.content || !this.timeLine) {
        if(this.timeLine) this.timeLine.style.display = 'none';
        return;
    }
    const now = new Date();
    const h = now.getHours(), m = String(now.getMinutes()).padStart(2, '0');
    if(this.timeLabel) this.timeLabel.innerText = `${h}:${m}`;
    const cur = h * 60 + now.getMinutes();
    const rows = Array.from(this.content.querySelectorAll('tr'));
    let pos = -1;
    for (let i = 0; i < rows.length; i++) {
        const row = rows[i];
        const timeCell = row.querySelector('td');
        if (!timeCell) continue;
        const slot = timeCell.innerText;
        const p = slot.split(/[-–—]/); if(p.length < 2) continue;
        const s = parseInt(p[0].split(':')[0])*60 + parseInt(p[0].split(':')[1]), e = parseInt(p[1].split(':')[0])*60 + parseInt(p[1].split(':')[1]);
        if (cur >= s && cur <= e) { pos = row.offsetTop + (row.offsetHeight * ((cur-s)/(e-s))); break; }
        if (i < rows.length - 1) {
            const nextRow = rows[i+1], nextSlotCell = nextRow.querySelector('td');
            if (!nextSlotCell) continue;
            const nextSlot = nextSlotCell.innerText;
            const nextS = parseInt(nextSlot.split(/[-–—]/)[0].split(':')[0])*60 + parseInt(nextSlot.split(/[-–—]/)[0].split(':')[1]);
            if (cur > e && cur < nextS) { pos = (row.offsetTop + row.offsetHeight) + ((nextRow.offsetTop - (row.offsetTop + row.offsetHeight)) * ((cur-e)/(nextS-e))); break; }
        }
    }
    if (pos !== -1) {
        this.timeLine.style.top = pos + "px";
        this.timeLine.style.display = 'block';
    } else {
        this.timeLine.style.display = 'none';
    }
  }

  updatePlan() {
    if (!this._hass || !this.config.entity) return;

    // --- LOGIKA WYBORU ENCJI NA PODSTAWIE OFFSETU ---
    let suffix = this._weekOffset === 0 ? 'curr' : (this._weekOffset === -1 ? 'prev' : 'next');
    let baseEntity = this.config.entity.replace(/_(prev|curr|next)$/, '');
    let entityId = `${baseEntity}_${suffix}`;

    const planState = this._hass.states[entityId];
    const freqState = this.config.freq_entity ? this._hass.states[this.config.freq_entity] : null;

    if (!planState || !planState.attributes.lekcje) {
        this.content.innerHTML = `<tr><td colspan="6" style="text-align: center; padding: 20px;">Brak danych planu (${suffix})</td></tr>`;
        return;
    }

    const todayISO = this.getFormattedDate(new Date()), now = new Date();
    const dayOfWeek = now.getDay() || 7;
    const monday = new Date(now);
    monday.setDate(now.getDate() - dayOfWeek + 1 + (this._weekOffset * 7));

    const weekDates = [];
    for(let i=0; i<5; i++) {
        const d = new Date(monday); d.setDate(monday.getDate() + i);
        const dISO = this.getFormattedDate(d); weekDates.push(dISO);
        if (this.dayHeaders[i]) {
            const isToday = dISO === todayISO;
            this.dayHeaders[i].innerHTML = `${["PON","WT","ŚR","CZW","PT"][i]}<br><span style="font-size: 0.75em; opacity: 0.7;">${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}</span>`;
            this.dayHeaders[i].style.background = isToday ? "rgba(var(--rgb-primary-color), 0.15)" : "transparent";
            this.dayHeaders[i].style.borderBottom = isToday ? "3px solid var(--accent-color)" : "1px solid var(--divider-color)";
        }
    }

    this.studentLabel.innerText = (planState.attributes.friendly_name || '').replace(/Plan (prev|curr|next): /, '').replace('Plan: ', '');
    this.weekLabel.innerText = this._weekOffset === 0 ? "OBECNY TYDZIEŃ" : (this._weekOffset === -1 ? "POPRZEDNI TYDZIEŃ" : "NASTĘPNY TYDZIEŃ");

    const lekcje = planState.attributes.lekcje;
    const slots = [...new Set(lekcje.map(l => l.g))].sort();

    let html = "";
    slots.forEach(slot => {
        const [sT, eT] = slot.split(/[-–—]/);
        const sM = parseInt(sT.split(':')[0])*60 + parseInt(sT.split(':')[1]);
        const eM = parseInt(eT.split(':')[0])*60 + parseInt(eT.split(':')[1]);
        const nowM = now.getHours()*60 + now.getMinutes();
        const isNow = this._weekOffset === 0 && nowM >= sM && nowM < eM;

        html += `<tr><td style="padding: 10px 5px; text-align: center; border: 1px solid var(--divider-color); font-size: 0.8em; background: ${isNow ? 'var(--accent-color)' : 'var(--card-background-color)'}; color: ${isNow ? 'white' : 'inherit'}; font-weight: bold;">${slot}</td>`;

        weekDates.forEach(date => {
            const isToday = date === todayISO, isCur = isToday && isNow, lessons = lekcje.filter(lek => lek.d === date && lek.g === slot);

            let cellContent = "";
            lessons.forEach((l, idx) => {
                let statusTag = "", textStyle = "font-weight: 600; font-size: 0.9em; line-height: 1.2;", blockBg = "transparent";
                const pillStyle = "display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.65em; font-weight: 900; color: white; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px;";

                if (l.st === 'ODWOL') {
                    textStyle += " text-decoration: line-through; opacity: 0.5;";
                    statusTag = `<div style="${pillStyle} background: #d32f2f;">Odwołane</div>`;
                }
                else if (l.st === 'ZWOL') {
                    blockBg = "rgba(76, 175, 80, 0.08)";
                    textStyle += " text-decoration: line-through; opacity: 0.6;";
                    statusTag = `<div style="${pillStyle} background: #388e3c;">Zwolnienie</div>`;
                }
                else if (l.st === 'ZAST') {
                    blockBg = "rgba(255, 165, 0, 0.12)";
                    statusTag = `<div style="${pillStyle} background: #ef6c00;">Zastępstwo</div>`;
                }
                else if (l.st === 'PRZEN') {
                    blockBg = "rgba(33, 150, 243, 0.1)";
                    statusTag = `<div style="${pillStyle} background: #1976d2;">Przeniesione</div>`;
                }
                else if (l.st === 'NIEOB') {
                    blockBg = "rgba(156, 39, 176, 0.1)";
                    statusTag = `<div style="${pillStyle} background: #7b1fa2;">Nieobecni</div>`;
                }

                let marker = "";
                if (freqState && freqState.attributes.wpisy) {
                    const planStart = l.g.split('-')[0].trim().replace(/^0/, "");
                    const record = freqState.attributes.wpisy.find(f => f.d === date && f.t.trim().replace(/^0/, "") === planStart);
                    if (record) {
                        const b = "padding: 0 2px; border-radius: 3px; font-size: 0.9em; font-weight: bold;";
                        let color = "", text = "", desc = "";

                        if (record.k === 1) { color = "#4caf50"; text = "[o]"; desc = "Obecność"; }
                        else if (record.k === 2) { color = "#f44336"; text = "[n]"; desc = "Nieobecność"; }
                        else if (record.k === 3) { color = "#2196f3"; text = "[nu]"; desc = "Usprawiedliwiona"; }
                        else if (record.k === 4) { color = "#ff9800"; text = "[s]"; desc = "Spóźnienie"; }
                        else if (record.k === 5) { color = "#00bcd4"; text = "[su]"; desc = "Spóźnienie uspraw."; }
                        else if (record.k === 6) { color = "#9c27b0"; text = "[sz]"; desc = "Przyczyny szkolne"; }
                        else if (record.k === 7) { color = "#607d8b"; text = "[zw]"; desc = "Zwolnienie"; }

                        if (text) {
                          marker = `
                            <div class="marker-wrapper">
                              <b style="color: ${color}; background: ${color}1A; ${b}">${text}</b>
                              <div class="vultron-tooltip" style="color: ${color};">${desc}</div>
                            </div>`;
                        }
                    }
                }

                const sep = idx > 0 ? "border-top: 1px dashed var(--divider-color); margin-top: 5px; padding-top: 5px;" : "";
                cellContent += `
                    <div style="${sep} position: relative; min-height: 45px; padding: 4px; background: ${blockBg}; border-radius: 4px;">
                        <div style="${textStyle}">${l.p}</div>
                        <div style="font-size: 0.72em; opacity: 0.7; margin-top: 1px;">${l.s} ${l.n ? ' • ' + l.n : ''}</div>
                        ${statusTag}
                        <div style="position: absolute; top: 2px; right: 2px;">${marker}</div>
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
    requestAnimationFrame(() => this.positionLine());
  }
  setConfig(config) { this.config = config; }
}
customElements.define("vultron-card", VultronPlanCard);

