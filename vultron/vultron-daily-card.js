// SPDX-License-Identifier: GPL-3.0-or-later
// Nowości dnia: co pojawiło się w encjach Vultrona (oceny, terminarz, uwagi,
// osiągnięcia, wiadomości). Źródło: historia HA (recorder), bez zmian w dodatku.
// Nowy = element, którego nie było w żadnym wcześniejszym stanie encji w oknie.

const DAILY_DNI = [1, 3, 7];

// Typ encji -> klucze i opisy elementów; "dane" to cały element (szczegóły po kliknięciu).
const DAILY_TYPY = {
  oceny: (a) => {
    const out = [];
    for (const p of a.lista_przedmiotow || []) {
      for (const o of p.oceny || []) {
        out.push([`o|${p.przedmiot}|${o.w}|${o.d}|${o.i}`,
          { typ: 'oceny', ikona: 'mdi:numeric', tekst: `Ocena ${o.w} — ${p.przedmiot}`, opis: o.i, dane: o }]);
      }
      if (p.proponowana) {
        out.push([`p|${p.przedmiot}|${p.proponowana}`,
          { typ: 'oceny', ikona: 'mdi:star-outline', tekst: `Ocena proponowana ${p.proponowana} — ${p.przedmiot}`, opis: '', dane: {} }]);
      }
      if (p.okresowa) {
        out.push([`k|${p.przedmiot}|${p.okresowa}`,
          { typ: 'oceny', ikona: 'mdi:star', tekst: `Ocena okresowa ${p.okresowa} — ${p.przedmiot}`, opis: '', dane: {} }]);
      }
    }
    return out;
  },
  terminarz: (a) => (a.lista || []).map((t) => [`${t.data}|${t.przedmiot}|${t.typ}`,
    { typ: 'terminarz', ikona: t.typ === 'Zadanie domowe' ? 'mdi:book-open-variant' : 'mdi:clipboard-text',
      tekst: `${t.typ} — ${t.przedmiot}`, opis: `na ${t.data}`, dane: t }]),
  uwagi: (a) => (a.uwagi || []).map((u) => [String(u.id),
    { typ: 'uwagi', ikona: u.typ === 'pozytywna' ? 'mdi:thumb-up' : u.typ === 'negatywna' ? 'mdi:alert-circle' : 'mdi:information',
      tekst: u.typ === 'pozytywna' ? 'Pochwała' : u.typ === 'negatywna' ? 'Uwaga' : 'Wpis', opis: u.kategoria, dane: u }]),
  // Klucz po treści: w GPE id to numer na liście, przesuwa się po nowym wpisie.
  osiagniecia: (a) => (a.osiagniecia || []).map((o) => [String(o.tresc),
    { typ: 'osiagniecia', ikona: 'mdi:trophy', tekst: 'Osiągnięcie', opis: String(o.tresc || '').split('\n')[0].slice(0, 80), dane: o }]),
  // GPE: wiadomości bez id.
  wiadomosci: (a) => (a.wiadomosci || []).map((m) => [m.id != null ? String(m.id) : `${m.data}|${m.nadawca}|${m.temat}`,
    { typ: 'wiadomosci', ikona: 'mdi:email', tekst: `Wiadomość — ${m.nadawca}`, opis: m.temat, dane: m }]),
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
    this._oczekuje = null;  // żądanie treści wiadomości: { nonce, timer }
  }

  _esc(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Kopia z vultron-messages-card.js (karty są osobnymi plikami bez importów).
  _sanitizeHTML(htmlString) {
    if (!htmlString) return "";

    // Lista dozwolonych tagów (z wielkich liter, bo tak przetwarza je DOM)
    const allowedTags = ['P', 'BR', 'STRONG', 'B', 'I', 'EM', 'U', 'A', 'UL', 'OL', 'LI', 'SPAN', 'DIV'];

    // Tworzymy wirtualny dokument w pamięci (bezpieczne parsowanie)
    const parser = new DOMParser();

    // Funkcja rekurencyjnie czyszcząca węzły
    const cleanNode = (node) => {
      // Jeśli to zwykły tekst - przepuszczamy
      if (node.nodeType === Node.TEXT_NODE) {
        return document.createTextNode(node.textContent);
      }
      // Ignorujemy komentarze i inne dziwne twory
      if (node.nodeType !== Node.ELEMENT_NODE) {
        return document.createDocumentFragment();
      }

      const tagName = node.tagName.toUpperCase();

      // Jeśli tag NIE JEST na naszej białej liście, ignorujemy go, ale wyciągamy z niego sam tekst
      if (!allowedTags.includes(tagName)) {
        const frag = document.createDocumentFragment();
        for (const child of node.childNodes) {
          frag.appendChild(cleanNode(child));
        }
        return frag;
      }

      // Jeśli tag jest dozwolony - TWORZYMY GO CAŁKOWICIE OD NOWA
      // Dzięki temu pozbywamy się wszystkich złośliwych atrybutów (np. onload, onclick, style)
      const el = document.createElement(tagName.toLowerCase());

      // SPECJALNA OBSŁUGA LINKÓW (<a>)
      if (tagName === 'A') {
        const href = node.getAttribute('href');
        // Pozwalamy tylko na bezpieczne linki (żadnego javascript: itp.)
        if (href && (href.startsWith('http://') || href.startsWith('https://'))) {
          el.setAttribute('href', href);
          el.setAttribute('target', '_blank'); // Wymusza otwarcie w nowej karcie
          el.setAttribute('rel', 'noopener noreferrer'); // Standard bezpieczeństwa
          el.style.color = 'var(--primary-color)'; // Ładne kolorowanie linków pod motyw HA
          el.style.textDecoration = 'underline';
        } else {
          // Jeśli link był zły, zamieniamy go w zwykły span
          return document.createTextNode(node.textContent);
        }
      }

      // Kopiujemy dzieci węzła
      for (const child of node.childNodes) {
        el.appendChild(cleanNode(child));
      }

      return el;
    };

    // Budujemy czysty wynik
    const wrapper = document.createElement('div');
    // Zamiana klasycznych enterów na <br> w razie gdyby vulcan wysłał plain-text
    const preProcessedHtml = htmlString.replace(/\n/g, '<br>');
    const doc2 = parser.parseFromString(preProcessedHtml, 'text/html');

    for (const child of doc2.body.childNodes) {
      wrapper.appendChild(cleanNode(child));
    }

    return wrapper.innerHTML;
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
    // GPE dzieli skrzynkę na części (page_entities); liczymy je razem z główną.
    for (const id of hass.states[`sensor.vultron_wiadomosci_${s}`]?.attributes?.page_entities || []) {
      if (hass.states[id]) out.push([id, 'wiadomosci']);
    }
    const prefix = `sensor.vultron_oceny_${s}_p`;
    for (const id of Object.keys(hass.states).sort()) {
      if (id.startsWith(prefix) && /^\d+$/.test(id.slice(prefix.length))) out.push([id, 'oceny']);
    }
    return out;
  }

  // Historia encji jednego typu (każda od najstarszego stanu) -> nowe elementy
  // z czasem pojawienia się. Pierwszy stan z danymi każdej encji to punkt
  // odniesienia, nie nowość. Stany bez atrybutów (unavailable po restarcie HA)
  // pomijamy; "widziane" są wspólne i się kumulują, więc element, który zniknął
  // (przycięta lista) albo przeszedł do innej części skrzynki, nie jest nowy.
  // Szczegóły z najnowszego stanu, w którym element jest (np. poprawiony opis).
  static nowosci(typ, ...strumienie) {
    const wyciag = DAILY_TYPY[typ];
    const dane = (st) => st && st.a && typeof st.a === 'object' && st.s !== 'unavailable' && st.s !== 'unknown';
    const widziane = new Set();
    const ostatnie = new Map();
    const nowe = new Map();
    const reszta = [];
    for (const stany of strumienie) {
      const zDanymi = (stany || []).filter(dane);
      for (const [klucz, el] of wyciag(zDanymi[0]?.a || {})) {
        widziane.add(klucz);
        ostatnie.set(klucz, el);
      }
      reszta.push(...zDanymi.slice(1));
    }
    reszta.sort((a, b) => Number(a.lu) - Number(b.lu));
    for (const st of reszta) {
      for (const [klucz, el] of wyciag(st.a)) {
        ostatnie.set(klucz, el);
        if (widziane.has(klucz)) continue;
        widziane.add(klucz);
        nowe.set(klucz, Number(st.lu) * 1000);
      }
    }
    return [...nowe].map(([klucz, ts]) => ({ ...ostatnie.get(klucz), ts }));
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._body) this._init();
    this._sprawdzOdpowiedz(hass);
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
      const wgTypu = {};
      for (const [id, typ] of encje) (wgTypu[typ] ||= []).push((wynik || {})[id] || []);
      this._zdarzenia = Object.entries(wgTypu)
        .flatMap(([typ, strumienie]) => VultronDailyCard.nowosci(typ, ...strumienie))
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
          .vd-el { cursor: pointer; }
          #vd-m { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
                  background: rgba(0,0,0,0.7); z-index: 1000; align-items: center; justify-content: center;
                  backdrop-filter: blur(3px); }
          #vd-m-okno { background: var(--ha-card-background, var(--card-background-color)); width: 90%;
                       max-width: 500px; max-height: 80%; border-radius: 12px; padding: 20px; overflow-y: auto;
                       box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid var(--divider-color);
                       user-select: text !important; }
          #vd-m-x { float: right; cursor: pointer; padding: 5px; color: var(--secondary-text-color); }
          #vd-m-tytul { font-size: 16px; font-weight: bold; margin-bottom: 10px; color: var(--primary-color); }
          #vd-m-pod { font-size: 13px; color: var(--secondary-text-color); border-bottom: 1px solid var(--divider-color);
                      margin-bottom: 15px; padding-bottom: 10px; }
          #vd-m-tresc { line-height: 1.6; font-size: 15px; color: var(--primary-text-color); white-space: pre-wrap; }
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
        <div id="vd-m">
          <div id="vd-m-okno">
            <div id="vd-m-x"><ha-icon icon="mdi:close"></ha-icon></div>
            <div id="vd-m-tytul"></div>
            <div id="vd-m-pod"></div>
            <div id="vd-m-tresc"></div>
            <div style="margin-top: 20px; text-align: center;"><mwc-button raised id="vd-m-zamknij">Zamknij</mwc-button></div>
          </div>
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
    this._body.addEventListener('click', (e) => {
      const el = e.target.closest('.vd-el');
      const z = el && this._lista?.[Number(el.dataset.i)];
      if (z) this._pokaz(z);
    });
    const okno = this.querySelector('#vd-m');
    const zamknij = () => { okno.style.display = 'none'; };
    okno.addEventListener('click', (e) => { if (e.target === okno) zamknij(); });
    this.querySelector('#vd-m-x').addEventListener('click', zamknij);
    this.querySelector('#vd-m-zamknij').addEventListener('click', zamknij);
  }

  // Szczegóły elementu: wszystko przez textContent, treść wiadomości przez _sanitizeHTML.
  _pokaz(z) {
    const d = z.dane || {};
    if (this._oczekuje) clearTimeout(this._oczekuje.timer);
    this._oczekuje = null;
    let tytul = z.tekst;
    let pod = [];
    let tresc = '';
    if (z.typ === 'terminarz') {
      pod = [`Termin: ${d.data}`, `Nauczyciel: ${d.autor || 'Nieznany'}`];
      tresc = d.opis;
    } else if (z.typ === 'oceny') {
      pod = [d.d && `Data: ${d.d}`, d.pop?.length && `Poprawa z: ${d.pop.join(', ')}`];
      tresc = d.i;
    } else if (z.typ === 'uwagi') {
      if (d.kategoria) tytul += ` — ${d.kategoria}`;
      pod = [`Data: ${d.data}`, `Nauczyciel: ${d.autor || 'Nieznany'}`];
      tresc = d.tresc;
    } else if (z.typ === 'osiagniecia') {
      tresc = d.tresc;
    } else if (z.typ === 'wiadomosci') {
      tytul = d.temat || '(brak tematu)';
      pod = [d.data, d.nadawca || '—'];
    }
    this.querySelector('#vd-m-tytul').textContent = tytul;
    this.querySelector('#vd-m-pod').textContent = pod.filter(Boolean).join(' | ');
    const body = this.querySelector('#vd-m-tresc');
    if (z.typ === 'wiadomosci') this._trescWiadomosci(d, body);
    else body.textContent = tresc || '';
    this.querySelector('#vd-m').style.display = 'flex';
  }

  // Jak w karcie wiadomości: treść z sensora, link do skrzynki (GPE) albo
  // treść na żądanie z bazy Vultrona przez input_text.vultron_zadanie (eduVULCAN).
  // Otwarcie nie zmienia statusu przeczytania.
  _trescWiadomosci(m, body) {
    if (m.tresc) { body.innerHTML = this._sanitizeHTML(m.tresc); return; }
    if (m.url) {
      try {
        const url = new URL(m.url);
        if (url.protocol === 'https:' && !url.username && !url.password) {
          body.textContent = '';
          const a = document.createElement('a');
          a.href = url.href;
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          a.style.color = 'var(--primary-color)';
          a.textContent = 'Otwórz skrzynkę wiadomości';
          body.appendChild(a);
          return;
        }
      } catch {
        // Zły adres: niżej zwykły komunikat.
      }
    }
    if (!m.id) { body.textContent = 'Treść wiadomości dostępna w dzienniku.'; return; }
    if (!this._hass.states['input_text.vultron_zadanie']) {
      body.textContent = 'Treść przeczytanych wiadomości wymaga pomocnika input_text.vultron_zadanie (README → Wiadomości).';
      return;
    }
    const nonce = Array.from({ length: 8 }, () => Math.floor(Math.random() * 16).toString(16)).join('');
    body.textContent = 'Wczytywanie treści…';
    const timer = setTimeout(() => {
      if (this._oczekuje?.nonce !== nonce) return;
      this._oczekuje = null;
      body.textContent = 'Vultron nie odpowiada. Spróbuj za chwilę.';
    }, 5000);
    this._oczekuje = { nonce, timer };
    Promise.resolve(this._hass.callService('input_text', 'set_value', {
      entity_id: 'input_text.vultron_zadanie', value: `otworz:${m.id}:${nonce}`,
    })).catch(() => {
      if (this._oczekuje?.nonce !== nonce) return;
      clearTimeout(timer);
      this._oczekuje = null;
      body.textContent = 'Nie udało się wysłać żądania do Home Assistanta.';
    });
  }

  _sprawdzOdpowiedz(hass) {
    const p = this._oczekuje;
    const a = hass.states['sensor.vultron_wiadomosc_otwarta']?.attributes;
    if (!p || !a || a.nonce !== p.nonce) return;
    clearTimeout(p.timer);
    this._oczekuje = null;
    const body = this.querySelector('#vd-m-tresc');
    if (a.tresc) body.innerHTML = this._sanitizeHTML(a.tresc);
    else body.textContent = a.blad ? 'Nie ma tej wiadomości w bazie Vultrona.' : '(brak treści)';
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
    this._lista = lista;
    let html = uwaga;
    let dzien = '';
    for (const [i, z] of lista.entries()) {
      const t = new Date(z.ts);
      const d = new Date(t.getFullYear(), t.getMonth(), t.getDate());
      const etykieta = this._etykietaDnia(d);
      if (etykieta !== dzien) { dzien = etykieta; html += `<div class="vd-dzien">${this._esc(etykieta)}</div>`; }
      const czas = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
      html += `<div class="vd-el" data-i="${i}"><ha-icon icon="${this._esc(z.ikona)}"></ha-icon>
        <div class="vd-tekst">${this._esc(z.tekst)}${z.opis ? `<div class="vd-opis">${this._esc(z.opis)}</div>` : ''}</div>
        <div class="vd-czas">${this._esc(czas)}</div></div>`;
    }
    this._body.innerHTML = html;
  }

  getCardSize() { return 4; }
}

customElements.define('vultron-daily-card', VultronDailyCard);
