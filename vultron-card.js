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
        <ha-card>
          <div style="padding: 16px; position: relative;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
              <ha-icon-button id="prev-week" style="cursor: pointer;"><ha-icon icon="hass:chevron-left"></ha-icon></ha-icon-button>
              <div style="text-align: center;">
                <div id="student-name" style="font-size: 0.85em; opacity: 0.7; text-transform: uppercase; color: var(--secondary-text-color); font-weight: 500;"></div>
                <div id="week-label" style="font-weight: bold; font-size: 1.1em; color: var(--primary-text-color);"></div>
              </div>
              <ha-icon-button id="next-week" style="cursor: pointer;"><ha-icon icon="hass:chevron-right"></ha-icon></ha-icon-button>
            </div>
            <div id="table-wrapper" style="position: relative; overflow-x: auto; border: 1px solid var(--divider-color); border-radius: 8px;">
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
        </ha-card>
      `;
      this.content = this.querySelector('#plan-body');
      this.weekLabel = this.querySelector('#week-label');
      this.studentLabel = this.querySelector('#student-name');
      this.dayHeaders = this.querySelectorAll('.day-header');
      this.timeLine = this.querySelector('#time-line');
      this.timeLabel = this.querySelector('#time-label');
      
      this.querySelector('#prev-week').addEventListener('click', () => { this._weekOffset--; this.updatePlan(); });
      this.querySelector('#next-week').addEventListener('click', () => { this._weekOffset++; this.updatePlan(); });
    }
    this.updatePlan();
    if (!this._lineUpdater) this._lineUpdater = setInterval(() => this.positionLine(), 10000);
  }

  getFormattedDate(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }

  positionLine() {
    if (this._weekOffset !== 0 || !this.content || !this.timeLine) { if(this.timeLine) this.timeLine.style.display = 'none'; return; }
    const now = new Date();
    const h = now.getHours(), m = String(now.getMinutes()).padStart(2, '0');
    if(this.timeLabel) this.timeLabel.innerText = `${h}:${m}`;
    const cur = h * 60 + now.getMinutes();
    const rows = Array.from(this.content.querySelectorAll('tr'));
    let pos = -1;
    for (let i = 0; i < rows.length; i++) {
        const row = rows[i];
        const slot = row.querySelector('td').innerText;
        const p = slot.split(/[-–—]/); if(p.length < 2) continue;
        const s = parseInt(p[0].split(':')[0])*60 + parseInt(p[0].split(':')[1]), e = parseInt(p[1].split(':')[0])*60 + parseInt(p[1].split(':')[1]);
        if (cur >= s && cur <= e) { pos = row.offsetTop + (row.offsetHeight * ((cur-s)/(e-s))); break; }
        if (i < rows.length - 1) {
            const nextRow = rows[i+1], nextSlot = nextRow.querySelector('td').innerText;
            const nextS = parseInt(nextSlot.split(/[-–—]/)[0].split(':')[0])*60 + parseInt(nextSlot.split(/[-–—]/)[0].split(':')[1]);
            if (cur > e && cur < nextS) { pos = (row.offsetTop + row.offsetHeight) + ((nextRow.offsetTop - (row.offsetTop + row.offsetHeight)) * ((cur-e)/(nextS-e))); break; }
        }
    }
    if (pos !== -1) { this.timeLine.style.top = pos + "px"; this.timeLine.style.display = 'block'; } else this.timeLine.style.display = 'none';
  }

  updatePlan() {
    if (!this._hass || !this.config.entity) return;
    const planState = this._hass.states[this.config.entity];
    const freqState = this.config.freq_entity ? this._hass.states[this.config.freq_entity] : null;
    if (!planState || !planState.attributes.lekcje) return;

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

    this.studentLabel.innerText = (planState.attributes.friendly_name || '').replace('Plan: ', '');
    this.weekLabel.innerText = this._weekOffset === 0 ? "OBECNY TYDZIEŃ" : `OKRES ${weekDates[0]} / ${weekDates[4]}`;

    const lekcje = planState.attributes.lekcje;
    const slots = [...new Set(lekcje.filter(l => weekDates.includes(l.d)).map(l => l.g))].sort();

    let html = "";
    slots.forEach(slot => {
        const [sT, eT] = slot.split(/[-–—]/);
        const sM = parseInt(sT.split(':')[0])*60 + parseInt(sT.split(':')[1]), eM = parseInt(eT.split(':')[0])*60 + parseInt(eT.split(':')[1]), nowM = now.getHours()*60 + now.getMinutes();
        const isNow = this._weekOffset === 0 && nowM >= sM && nowM < eM;

        html += `<tr><td style="padding: 10px 5px; text-align: center; border: 1px solid var(--divider-color); font-size: 0.8em; background: ${isNow ? 'var(--accent-color)' : 'var(--card-background-color)'}; color: ${isNow ? 'white' : 'inherit'}; font-weight: bold;">${slot}</td>`;
        
        weekDates.forEach(date => {
            const isToday = date === todayISO, isCur = isToday && isNow, lessons = lekcje.filter(lek => lek.d === date && lek.g === slot);
            let cellContent = "";
            lessons.forEach((l, idx) => {
                let statusTag = "", textStyle = "font-weight: 600; font-size: 0.9em; line-height: 1.2;", blockBg = "transparent";
                if (l.st === 'ODWOL') { textStyle += " text-decoration: line-through; opacity: 0.5;"; statusTag = "<div style='color: var(--error-color); font-size: 0.7em; font-weight: bold;'>ODWOŁANE</div>"; }
                else if (l.st === 'ZWOL') { blockBg = "rgba(76, 175, 80, 0.1)"; textStyle += " text-decoration: line-through;"; statusTag = "<div style='color: #2e7d32; font-size: 0.7em; font-weight: bold;'>DO DOMU</div>"; }
                else if (l.st === 'ZAST') { blockBg = "rgba(255, 165, 0, 0.1)"; statusTag = "<div style='color: #ef6c00; font-size: 0.7em; font-weight: bold;'>ZASTĘPSTWO</div>"; }

                let marker = "";
                if (freqState && freqState.attributes.wpisy) {
                    const planStart = l.g.split('-')[0].trim().replace(/^0/, "");
                    const record = freqState.attributes.wpisy.find(f => f.d === date && f.t.trim().replace(/^0/, "") === planStart);
                    if (record) {
                        const b = "cursor: help; padding: 0 2px; border-radius: 3px; font-size: 0.95em;";
                        if (record.k === 1) marker = `<b title="Obecność" style="color: #4caf50; background: rgba(76,175,80,0.1); ${b}">[o]</b>`;
                        else if (record.k === 2) marker = `<b title="Nieobecność" style="color: #f44336; background: rgba(244,67,54,0.1); ${b}">[n]</b>`;
                        else if (record.k === 3) marker = `<b title="Usprawiedliwiona" style="color: #2196f3; background: rgba(33,150,243,0.1); ${b}">[nu]</b>`;
                        else if (record.k === 4) marker = `<b title="Spóźnienie" style="color: #ff9800; background: rgba(255,152,0,0.1); ${b}">[s]</b>`;
                        else if (record.k === 5) marker = `<b title="Spóźnienie uspraw." style="color: #00bcd4; background: rgba(0,188,212,0.1); ${b}">[su]</b>`;
                        else if (record.k === 6) marker = `<b title="Przyczyny szkolne" style="color: #9c27b0; background: rgba(156,39,176,0.1); ${b}">[sz]</b>`;
                        else if (record.k === 7) marker = `<b title="Zwolnienie" style="color: #607d8b; background: rgba(96,125,139,0.1); ${b}">[zw]</b>`;
                    }
                }

                const sep = idx > 0 ? "border-top: 1px dashed var(--divider-color); margin-top: 4px; padding-top: 4px;" : "";
                cellContent += `<div style="${sep} position: relative; min-height: 42px; padding: 2px; background: ${blockBg};"><div style="${textStyle}">${l.p}</div><div style="font-size: 0.75em; opacity: 0.7; margin-top: 2px;">${l.s} ${l.n ? ' • ' + l.n : ''}</div>${statusTag}<div style="position: absolute; bottom: -2px; right: -2px;">${marker}</div></div>`;
            });

            html += `<td style="padding: 5px; border: ${isCur ? '2px solid var(--accent-color)' : '1px solid var(--divider-color)'}; vertical-align: top; position: relative; background: ${isToday ? 'rgba(var(--rgb-primary-color), 0.08)' : 'transparent'};">${isCur ? '<div style="position: absolute; top: 0; right: 0; font-size: 0.5em; background: var(--accent-color); color: white; padding: 1px 4px; font-weight: bold; border-bottom-left-radius: 4px; z-index: 5;">TERAZ</div>' : ''}${cellContent}</td>`;
        });
        html += `</tr>`;
    });
    this.content.innerHTML = html || `<tr><td colspan="6" style="text-align: center; padding: 20px;">Brak zajęć</td></tr>`;
    requestAnimationFrame(() => this.positionLine());
  }
  setConfig(config) { this.config = config; }
}
customElements.define("vultron-card", VultronPlanCard);