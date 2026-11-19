// SPDX-License-Identifier: GPL-3.0-or-later
// Nowości dnia: co pojawiło się w encjach Vultrona (oceny, terminarz, uwagi,
// osiągnięcia, wiadomości). Źródło: historia HA (recorder), bez zmian w dodatku.
// Nowy = element, którego nie było w żadnym wcześniejszym stanie encji w oknie.

const DAILY_DNI = [1, 3, 7];

// Typ encji -> klucze i opisy elementów. Krótko: szczegóły są na innych kartach.
const DAILY_TYPY = {
  oceny: (a) => {
    const out = [];
    for (const p of a.lista_przedmiotow || []) {
      for (const o of p.oceny || []) {
        out.push([`o|${p.przedmiot}|${o.w}|${o.d}|${o.i}`,
          { ikona: 'mdi:numeric', tekst: `Ocena ${o.w} — ${p.przedmiot}`, opis: o.i }]);
      }
      if (p.proponowana) {
        out.push([`p|${p.przedmiot}|${p.proponowana}`,
          { ikona: 'mdi:star-outline', tekst: `Ocena proponowana ${p.proponowana} — ${p.przedmiot}`, opis: '' }]);
      }
      if (p.okresowa) {
        out.push([`k|${p.przedmiot}|${p.okresowa}`,
          { ikona: 'mdi:star', tekst: `Ocena okresowa ${p.okresowa} — ${p.przedmiot}`, opis: '' }]);
      }
    }
    return out;
  },
  terminarz: (a) => (a.lista || []).map((t) => [`${t.data}|${t.przedmiot}|${t.typ}`,
    { ikona: t.typ === 'Zadanie domowe' ? 'mdi:book-open-variant' : 'mdi:clipboard-text',
      tekst: `${t.typ} — ${t.przedmiot}`, opis: `na ${t.data}` }]),
  uwagi: (a) => (a.uwagi || []).map((u) => [String(u.id),
    { ikona: u.typ === 'pozytywna' ? 'mdi:thumb-up' : u.typ === 'negatywna' ? 'mdi:alert-circle' : 'mdi:information',
      tekst: u.typ === 'pozytywna' ? 'Pochwała' : u.typ === 'negatywna' ? 'Uwaga' : 'Wpis', opis: u.kategoria }]),
  osiagniecia: (a) => (a.osiagniecia || []).map((o) => [String(o.id),
    { ikona: 'mdi:trophy', tekst: 'Osiągnięcie', opis: String(o.tresc || '').split('\n')[0].slice(0, 80) }]),
  wiadomosci: (a) => (a.wiadomosci || []).map((m) => [String(m.id),
    { ikona: 'mdi:email', tekst: `Wiadomość — ${m.nadawca}`, opis: m.temat }]),
};

class VultronDailyCard extends HTMLElement {
  constructor() {
    super();
    this._dni = null;
    this._podpis = null;   // last_updated encji: zmiana -> ponowne pobranie historii
    this._wTrakcie = false;
    this._zdarzenia = null;
    this._blad = '';
    this._bezHistorii = [];
  }

  _esc(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  setConfig(config) {
    if (!config.slug) throw new Error('Musisz podać slug dziecka (slug), np. jan_kowalski');
    this.config = config;
    this._dni = DAILY_DNI.includes(Number(config.days)) ? Number(config.days) : 1;
  }

  // Encje dziecka: [entity_id, typ]. Oceny: wszystkie okresy (_p1, _p2...).
  _encje(hass) {
    const s = this.config.slug;
    const out = [];
    for (const typ of ['terminarz', 'uwagi', 'osiagniecia', 'wiadomosci']) {
      const id = `sensor.vultron_${typ}_${s}`;
      if (hass.states[id]) out.push([id, typ]);
    }
    const prefix = `sensor.vultron_oceny_${s}_p`;
    for (const id of Object.keys(hass.states).sort()) {
      if (id.startsWith(prefix) && /^\d+$/.test(id.slice(prefix.length))) out.push([id, 'oceny']);
    }
    return out;
  }

  // Stany jednej encji (od najstarszego) -> nowe elementy z czasem pojawienia się.
  // Pierwszy stan z danymi to punkt odniesienia, nie nowość. Stany bez atrybutów
  // (unavailable po restarcie HA) pomijamy; "widziane" się kumulują, więc element,
  // który zniknął (np. przycięta lista) i wrócił, nie jest nowy.
  static nowosci(typ, stany) {
    const wyciag = DAILY_TYPY[typ];
    const widziane = new Set();
    const out = [];
    let start = true;
    for (const st of stany || []) {
      const a = st && st.a;
      if (!a || typeof a !== 'object' || st.s === 'unavailable' || st.s === 'unknown') continue;
      for (const [klucz, el] of wyciag(a)) {
        if (widziane.has(klucz)) continue;
        widziane.add(klucz);
        if (!start) out.push({ ...el, ts: Number(st.lu) * 1000 });
      }
      start = false;
    }
    return out;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._body) this._init();
    const encje = this._encje(hass);
    const podpis = `${this._dni}|` + encje.map(([id]) => hass.states[id].last_updated).join('|');
    if (podpis !== this._podpis && !this._wTrakcie) {
      this._podpis = podpis;
      this._pobierz(hass, encje);
    }
  }

  async _pobierz(hass, encje) {
    if (!encje.length) { this._blad = 'Brak encji Vultrona dla tego slugu.'; this._render(); return; }
    if (typeof hass.callWS !== 'function') return;
    const od = new Date();
    od.setHours(0, 0, 0, 0);
    od.setDate(od.getDate() - (this._dni - 1));
    this._wTrakcie = true;
    try {
      const wynik = await hass.callWS({
        type: 'history/history_during_period',
        start_time: od.toISOString(),
        entity_ids: encje.map(([id]) => id),
        include_start_time_state: true,
        significant_changes_only: false,
        minimal_response: false,
        no_attributes: false,
      });
      const typy = Object.fromEntries(encje);
      this._zdarzenia = Object.entries(wynik || {})
        .flatMap(([id, stany]) => (typy[id] ? VultronDailyCard.nowosci(typy[id], stany) : []))
        .filter((z) => z.ts >= od.getTime());
      // Encja bez historii: zwykle wykluczona z recordera (sekcja Prywatność w README).
      this._bezHistorii = encje.filter(([id]) => !(wynik || {})[id]?.length).map(([, typ]) => typ);
      this._blad = '';
    } catch {
      // Bez treści błędu (może zawierać szczegóły instalacji).
      this._zdarzenia = null;
      this._blad = 'Nie udało się pobrać historii HA (recorder).';
    } finally {
      this._wTrakcie = false;
    }
    this._render();
    // Zmiana zakresu dni w trakcie pobierania -> pobierz jeszcze raz.
    if (this._hass) this.hass = this._hass;
  }

  _init() {
    this.innerHTML = `
      <ha-card>
        <style>
          .vd-dzien { font-weight: 500; margin: 12px 0 6px; color: var(--primary-color); }
          .vd-el { display: flex; gap: 10px; align-items: flex-start; padding: 6px 0;
                   border-bottom: 1px solid var(--divider-color); }
          .vd-el ha-icon { color: var(--secondary-text-color); flex-shrink: 0; }
          .vd-tekst { flex: 1; font-size: 0.95em; }
          .vd-opis { font-size: 0.82em; opacity: 0.7; }
          .vd-czas { font-size: 0.78em; opacity: 0.6; white-space: nowrap; }
          .vd-pusto { text-align: center; padding: 20px; opacity: 0.6; }
        </style>
        <div style="padding: 16px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
            <div id="vd-tytul" style="font-size: 1.1em; font-weight: 500;"></div>
            <div style="display: flex; gap: 10px; font-size: 0.8em; font-weight: bold;">
              ${DAILY_DNI.map((d) => `<span class="vd-dni" data-dni="${d}" style="cursor: pointer;">${d === 1 ? 'DZIŚ' : d + ' DNI'}</span>`).join('')}
            </div>
          </div>
          <div id="vd-body"><div class="vd-pusto">Wczytywanie…</div></div>
        </div>
      </ha-card>`;
    this._body = this.querySelector('#vd-body');
    this.querySelector('#vd-tytul').textContent = this.config.title || 'Co nowego';
    this.querySelectorAll('.vd-dni').forEach((el) => {
      el.addEventListener('click', () => {
        this._dni = Number(el.dataset.dni);
        this._podpis = null;
        this._oznaczDni();
        if (this._hass) this.hass = this._hass;
      });
    });
    this._oznaczDni();
  }

  _oznaczDni() {
    this.querySelectorAll('.vd-dni').forEach((el) => {
      el.style.color = Number(el.dataset.dni) === this._dni ? 'var(--primary-color)' : 'var(--secondary-text-color)';
    });
  }

  _etykietaDnia(d) {
    const dzis = new Date();
    dzis.setHours(0, 0, 0, 0);
    const roznica = Math.round((dzis - d) / 86400000);
    if (roznica === 0) return 'Dziś';
    if (roznica === 1) return 'Wczoraj';
    const dn = ['Nd', 'Pn', 'Wt', 'Śr', 'Cz', 'Pt', 'So'][d.getDay()];
    return `${dn} ${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}`;
  }

  _render() {
    if (!this._body) return;
    if (this._blad) { this._body.innerHTML = `<div class="vd-pusto">${this._esc(this._blad)}</div>`; return; }
    const lista = [...(this._zdarzenia || [])].sort((a, b) => b.ts - a.ts);
    const brak = [...new Set(this._bezHistorii || [])];
    const uwaga = brak.length
      ? `<div class="vd-opis">Bez historii HA (recorder): ${this._esc(brak.join(', '))}</div>` : '';
    if (!lista.length) { this._body.innerHTML = `<div class="vd-pusto">Brak nowości.</div>${uwaga}`; return; }
    let html = uwaga;
    let dzien = '';
    for (const z of lista) {
      const t = new Date(z.ts);
      const d = new Date(t.getFullYear(), t.getMonth(), t.getDate());
      const etykieta = this._etykietaDnia(d);
      if (etykieta !== dzien) { dzien = etykieta; html += `<div class="vd-dzien">${this._esc(etykieta)}</div>`; }
      const czas = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
      html += `<div class="vd-el"><ha-icon icon="${this._esc(z.ikona)}"></ha-icon>
        <div class="vd-tekst">${this._esc(z.tekst)}${z.opis ? `<div class="vd-opis">${this._esc(z.opis)}</div>` : ''}</div>
        <div class="vd-czas">${this._esc(czas)}</div></div>`;
    }
    this._body.innerHTML = html;
  }

  getCardSize() { return 4; }
}

customElements.define('vultron-daily-card', VultronDailyCard);
