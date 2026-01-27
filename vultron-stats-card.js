class VultronStatsCard extends HTMLElement {
  set hass(hass) {
    const state = hass.states[this.config.entity];
    if (!state || !state.attributes.rows) return;
    
    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <div style="padding: 16px;">
            <!-- NOWY NAGŁÓWEK Z IMIENIEM I NAZWISKIEM -->
            <div style="text-align: center; margin-bottom: 20px;">
              <div id="student-display-name" style="font-size: 1.5em; font-weight: 500; color: var(--primary-text-color); line-height: 1.2;"></div>
              <div style="font-size: 0.9em; font-weight: 400; color: var(--secondary-text-color); margin-top: 4px; text-transform: uppercase; letter-spacing: 1px;">Statystyki frekwencji</div>
            </div>

            <div style="display:flex; flex-wrap:wrap; align-items:center; gap:20px;">
              <div style="flex:1; min-width:160px; text-align:center; position:relative;">
                 <svg viewBox="0 0 36 36" style="width:140px; height:140px; transform:rotate(-90deg);">
                   <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="var(--divider-color)" stroke-width="2.5" />
                   <path id="arc" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="var(--accent-color)" stroke-width="2.5" stroke-dasharray="0, 100" stroke-linecap="round" />
                 </svg>
                 <div style="position:absolute; top:50%; left:50%; transform:translate(-50%, -50%); text-align:center;">
                   <div style="font-size:1.8em; font-weight:bold;"><span id="perc">0</span>%</div>
                   <div style="font-size:0.6em; opacity:0.6; line-height:1;">Od początku<br>roku</div>
                 </div>
              </div>
              <div style="flex:4; overflow-x:auto;">
                <table style="width:100%; border-collapse:collapse; font-size:0.72em; text-align:right;">
                  <thead><tr id="h-row"></tr></thead>
                  <tbody id="b-rows"></tbody>
                </table>
              </div>
            </div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#b-rows');
      this.studentDisplayName = this.querySelector('#student-display-name');
    }
    
    // Wyciąganie imienia i nazwiska z friendly_name (usuwa prefix "Statystyki: ")
    const rawName = (state.attributes.friendly_name || '').replace('Statystyki: ', '');
    this.studentDisplayName.innerText = rawName;

    this.querySelector('#arc').setAttribute('stroke-dasharray', `${state.state}, 100`);
    this.querySelector('#perc').innerText = state.state;
    
    const mKeys = [9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8];
    const mLabels = ["IX","X","XI","XII","I","II","III","IV","V","VI","VII","VIII"];
    
    this.querySelector('#h-row').innerHTML = 
        `<th style="text-align:left; padding-bottom:8px;">Kategoria</th>` + 
        mLabels.map(m => `<th style="padding:0 3px;">${m}</th>`).join('') +
        `<th style="padding:0 5px;">S1</th><th style="padding:0 5px;">S2</th><th style="padding:0 5px; font-weight:bold;">Razem</th>`;

    this.content.innerHTML = state.attributes.rows.map(r => `
      <tr style="border-top:1px solid var(--divider-color);">
        <td style="text-align:left; padding:8px 4px; font-weight:500; color:var(--primary-color);">${r.k}</td>
        ${mKeys.map(m => `<td style="opacity:${r.m[m] ? 1 : 0.3};">${r.m[m] || 0}</td>`).join('')}
        <td style="padding:0 5px;">${r.s1 || 0}</td>
        <td style="padding:0 5px;">${r.s2 || 0}</td>
        <td style="padding:0 5px; font-weight:bold;">${r.r || 0}</td>
      </tr>`).join('');
  }
  setConfig(config) { this.config = config; }
}
customElements.define("vultron-stats-card", VultronStatsCard);
