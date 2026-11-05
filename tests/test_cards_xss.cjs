const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Every field of the attributes below comes from the external Vulcan API.
// The cards must never let it reach innerHTML as markup.
const PAYLOAD = 'z<img src=x onerror=alert(1)>';
const RAW = '<img src=x onerror';
const ESCAPED = '&lt;img src=x onerror';

// Every HTML and text write of every element, so a check covers the whole card.
let htmlWrites = [];
let textWrites = [];

class Element {
  constructor() {
    this.style = { setProperty() {}, removeProperty() {} };
    this.nodes = new Map();
    this.html = '';
    this.children = [];
    this.dataset = {};
    this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
    this.attrs = {};
    this.value = '';
    this.options = [];
  }
  set innerHTML(value) { this.html = String(value); htmlWrites.push(this.html); this.children = []; }
  get innerHTML() { return this.html; }
  set innerText(value) { this._text = String(value); textWrites.push(this._text); }
  get innerText() { return this._text || ''; }
  set textContent(value) { this._text = String(value); textWrites.push(this._text); }
  get textContent() { return this._text || ''; }
  insertAdjacentHTML(_, value) { htmlWrites.push(String(value)); }
  querySelector(selector) {
    if (!this.nodes.has(selector)) this.nodes.set(selector, new Element());
    return this.nodes.get(selector);
  }
  querySelectorAll() { return []; }
  addEventListener() {}
  removeEventListener() {}
  appendChild(child) { this.children.push(child); return child; }
  append(...nodes) { this.children.push(...nodes); }
  prepend(...nodes) { this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  remove() {}
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return this.attrs[name] ?? null; }
  removeAttribute(name) { delete this.attrs[name]; }
  get firstChild() { return this.children[0] || (this._first ||= new Element()); }
  get lastChild() { return this.children[this.children.length - 1] || (this._last ||= new Element()); }
  closest() { return null; }
  contains() { return false; }
  scrollTo() {}
  getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0 }; }
}

const document = {
  createElement: () => new Element(),
  createDocumentFragment: () => new Element(),
  createTextNode: (text) => { textWrites.push(String(text)); return { textContent: String(text) }; },
  body: new Element(),
};

// One literal path per card (no fs call with a variable path).
const SOURCES = {
  'vultron-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-card.js'), 'utf8'),
  'vultron-grades-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-grades-card.js'), 'utf8'),
  'vultron-messages-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-messages-card.js'), 'utf8'),
  'vultron-osiagniecia-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-osiagniecia-card.js'), 'utf8'),
  'vultron-przedszkole-informacje-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-przedszkole-informacje-card.js'), 'utf8'),
  'vultron-przedszkole-jadlospis-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-przedszkole-jadlospis-card.js'), 'utf8'),
  'vultron-przedszkole-nauczyciele-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-przedszkole-nauczyciele-card.js'), 'utf8'),
  'vultron-przedszkole-obecnosc-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-przedszkole-obecnosc-card.js'), 'utf8'),
  'vultron-przedszkole-oplaty-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-przedszkole-oplaty-card.js'), 'utf8'),
  'vultron-przedszkole-plan-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-przedszkole-plan-card.js'), 'utf8'),
  'vultron-przedszkole-wiadomosci-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-przedszkole-wiadomosci-card.js'), 'utf8'),
  'vultron-przedszkole-zebrania-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-przedszkole-zebrania-card.js'), 'utf8'),
  'vultron-stats-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-stats-card.js'), 'utf8'),
  'vultron-szczesliwy-numerek-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-szczesliwy-numerek-card.js'), 'utf8'),
  'vultron-uwagi-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-uwagi-card.js'), 'utf8'),
  'vultron-work-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-work-card.js'), 'utf8'),
  'vultron-zebrania-card.js': () => fs.readFileSync(path.join(__dirname, '../vultron/vultron-zebrania-card.js'), 'utf8'),
};

function load(file) {
  let Card;
  vm.runInNewContext(SOURCES[file](), {
    HTMLElement: Element,
    customElements: { define: (_, klass) => { Card = klass; }, get: () => undefined },
    document,
    window: { open() {}, addEventListener() {}, removeEventListener() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    requestAnimationFrame: () => 0,
    URL,
    crypto: globalThis.crypto,
    console,
  });
  const card = new Card();
  card.config = { limit: 0 };
  card.content = new Element();
  return card;
}

function assertNoMarkup(html) {
  assert.ok(!html.includes('<img'), 'payload rendered as markup');
}

test('remarks card escapes every API field including the date', () => {
  const card = load('vultron-uwagi-card.js');
  card.renderBody({ attributes: { uwagi: [{
    data: PAYLOAD, tresc: PAYLOAD, autor: PAYLOAD, kategoria: PAYLOAD, punkty: PAYLOAD, typ: 'negatywna', id: '1',
  }] } });
  assertNoMarkup(card.content.html);
});

test('homework card escapes every API field including the date', () => {
  const card = load('vultron-work-card.js');
  card.renderBody({ attributes: { lista: [{
    data: PAYLOAD, przedmiot: PAYLOAD, typ: 'Sprawdzian', opis: PAYLOAD, autor: PAYLOAD,
  }] } });
  assertNoMarkup(card.content.html);
});

test('grades card escapes grades, corrections, dates and final grades in all views', () => {
  const card = load('vultron-grades-card.js');
  const state = { attributes: { lista_przedmiotow: [{
    przedmiot: PAYLOAD, srednia: PAYLOAD, srednia_zrodlo: 'wyliczona',
    oceny: [{ w: PAYLOAD, d: PAYLOAD, i: PAYLOAD, pop: [PAYLOAD] }],
    proponowana: PAYLOAD, okresowa: PAYLOAD, proponowana_num: PAYLOAD, okresowa_num: PAYLOAD,
  }] } };
  for (const view of ['renderBySubject', 'renderByDate', 'renderFinal']) {
    card[view](state);
    assertNoMarkup(card.content.html);
  }
});

// ── all cards through their real entry point: setConfig() + hass ──────────
// Attribute shapes as published by vultron.py; every text field holds PAYLOAD.
// Each list has an item with a valid date (so date-filtered views render it)
// and one with PAYLOAD as the date.

const pad = (n) => String(n).padStart(2, '0');
const now = new Date();
const TODAY = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
const ENTITY = 'sensor.test';
// Plan cards pick the week entity by suffix and may auto-switch to next week
// (weekends), so every week entity gets an entry on each day of +-14 days.
const DAYS = Array.from({ length: 29 }, (_, i) => {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i - 14);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
});
function weeks(base, attributes) {
  const out = {};
  for (const suf of ['prev', 'curr', 'next']) out[`${base}_${suf}`] = st(attributes);
  return out;
}

function st(attributes, state = '1') {
  return { state, attributes: { friendly_name: `Test: ${PAYLOAD}`, ...attributes } };
}

const msg = (data) => ({ data, nadawca: PAYLOAD, temat: PAYLOAD, tresc: '', przeczytana: false });
const meeting = (data) => ({ data, godzina: '17:00', sala: PAYLOAD, opis: PAYLOAD, temat: PAYLOAD,
  agenda: PAYLOAD, online: PAYLOAD, id: data });

const CASES = {
  'vultron-card.js': {
    config: { entity: 'sensor.plan_curr', freq_entity: 'sensor.freq' },
    states: {
      ...weeks('sensor.plan', {
        lekcje: [...DAYS.map((d) => ({ d, g: '08:00-08:45', p: PAYLOAD, s: PAYLOAD, n: PAYLOAD, st: PAYLOAD })),
          // g is always "HH:MM-HH:MM" (vultron.py builds it from validated times).
          { d: PAYLOAD, g: '08:00-08:45', p: PAYLOAD, s: PAYLOAD, n: PAYLOAD, st: PAYLOAD }],
        dni_wolne: [{ d: TODAY, n: PAYLOAD }] }),
      'sensor.freq': st({ wpisy: DAYS.map((d) => ({ d, t: '08:00', k: 2 })) }),
    },
  },
  'vultron-grades-card.js': {
    config: { entity: ENTITY },
    states: { [ENTITY]: st({ period_number: 1, srednia_proponowanych: PAYLOAD, srednia_okresowych: PAYLOAD,
      lista_przedmiotow: [{ przedmiot: PAYLOAD, srednia: PAYLOAD, srednia_zrodlo: PAYLOAD, srednia_proc: PAYLOAD,
        oceny: [{ w: PAYLOAD, d: TODAY, i: PAYLOAD, pop: [PAYLOAD] }],
        proponowana: PAYLOAD, okresowa: PAYLOAD, proponowana_num: PAYLOAD, okresowa_num: PAYLOAD }] }) },
  },
  'vultron-messages-card.js': {
    config: { entity: ENTITY },
    states: { [ENTITY]: st({ wiadomosci: [msg(`${TODAY} 08:00`), msg(PAYLOAD)], stats: PAYLOAD }) },
  },
  'vultron-osiagniecia-card.js': {
    config: { entity: ENTITY },
    states: { [ENTITY]: st({ osiagniecia: [{ id: '1', tresc: `${PAYLOAD}\n${PAYLOAD}` }] }) },
  },
  'vultron-przedszkole-informacje-card.js': {
    config: { entity: ENTITY },
    states: { [ENTITY]: st({ dyrektor: PAYLOAD, adres: PAYLOAD, miejscowosc: PAYLOAD, tel_sluzbowy: PAYLOAD,
      tel_komorkowy: PAYLOAD, tel_domowy: PAYLOAD, mail: PAYLOAD, strona_www: PAYLOAD }, PAYLOAD) },
  },
  'vultron-przedszkole-jadlospis-card.js': {
    config: { entity: ENTITY },
    states: { [ENTITY]: st({ dni: { [TODAY]: [{ nazwa: PAYLOAD, sklad: [{ receptura: PAYLOAD }], alergeny: [PAYLOAD] }] } }) },
    // Meal details (composition, allergens) render only when expanded.
    after: (card, s) => { card._expanded.add('0'); card._renderMeals(s.attributes.dni[TODAY]); },
  },
  'vultron-przedszkole-nauczyciele-card.js': {
    config: { entity: ENTITY },
    states: { [ENTITY]: st({ nauczyciele: [{ imie: PAYLOAD, nazwisko: PAYLOAD, przedmiot: PAYLOAD, wychowawca: true }] }) },
  },
  'vultron-przedszkole-obecnosc-card.js': {
    config: { entity: ENTITY },
    states: { [ENTITY]: st({ historia: [{ data: TODAY, obecnosc: true }],
      miesiac: [{ data: TODAY, obecnosc: true, godzina_wejscia: PAYLOAD, godzina_wyjscia: PAYLOAD }] }) },
    // Hours (entry/exit times) render only on the "Godziny" tab.
    after: (card, s) => { card._activeTab = 'godziny'; card._render(s); },
  },
  'vultron-przedszkole-oplaty-card.js': {
    config: { entity: ENTITY },
    states: { [ENTITY]: st({ konta: [{ kwota_do_zaplaty: 10, aktywne: true, status_platnosci: 1, kwota_upomnien: 0,
      kwota_odsetek: 0, kwota_umorzenia: 0, data_synchronizacji: PAYLOAD }] }, PAYLOAD) },
  },
  'vultron-przedszkole-plan-card.js': {
    config: { entity: 'sensor.pplan_curr' },
    states: weeks('sensor.pplan', { zajecia: [...DAYS.map((d) => ({ d, g: '08:00-09:00', z: PAYLOAD, n: PAYLOAD })),
      { d: PAYLOAD, g: PAYLOAD, z: PAYLOAD, n: PAYLOAD }] }),
  },
  'vultron-przedszkole-wiadomosci-card.js': {
    config: { entity: ENTITY },
    states: { [ENTITY]: st({ wiadomosci: [msg(`${TODAY} 08:00`), msg(PAYLOAD)], stats: PAYLOAD }) },
  },
  'vultron-przedszkole-zebrania-card.js': {
    config: { entity: ENTITY },
    states: { [ENTITY]: st({ zebrania: [meeting(TODAY), meeting(PAYLOAD)] }) },
  },
  'vultron-stats-card.js': {
    config: { entity: ENTITY },
    states: { [ENTITY]: st({ rows: [{ k: PAYLOAD, m: { 9: PAYLOAD }, s1: PAYLOAD, s2: PAYLOAD, r: PAYLOAD }],
      przedmioty: [{ id: -1, nazwa: PAYLOAD }] }, PAYLOAD) },
  },
  'vultron-szczesliwy-numerek-card.js': {
    config: { entity: ENTITY },
    states: { [ENTITY]: st({ numer: PAYLOAD, id_numerku: PAYLOAD }, PAYLOAD) },
  },
  'vultron-uwagi-card.js': {
    config: { entity: ENTITY },
    states: { [ENTITY]: st({ uwagi: [{ data: TODAY, tresc: PAYLOAD, autor: PAYLOAD, kategoria: PAYLOAD,
      punkty: PAYLOAD, typ: 'negatywna', id: '1' }, { data: PAYLOAD, tresc: PAYLOAD, autor: PAYLOAD,
      kategoria: PAYLOAD, punkty: PAYLOAD, typ: PAYLOAD, id: '2' }] }) },
  },
  'vultron-work-card.js': {
    config: { entity: ENTITY },
    states: { [ENTITY]: st({ lista: [{ data: TODAY, przedmiot: PAYLOAD, typ: PAYLOAD, opis: PAYLOAD, autor: PAYLOAD },
      { data: PAYLOAD, przedmiot: PAYLOAD, typ: PAYLOAD, opis: PAYLOAD, autor: PAYLOAD }] }) },
  },
  'vultron-zebrania-card.js': {
    config: { entity: ENTITY },
    states: { [ENTITY]: st({ zebrania: [meeting(TODAY), meeting(PAYLOAD)] }) },
  },
};

test('every card file has an XSS case', () => {
  const cards = fs.readdirSync(path.join(__dirname, '../vultron')).filter((f) => /^vultron-.*card\.js$/.test(f));
  assert.deepEqual(cards.sort(), Object.keys(CASES).sort());
  assert.deepEqual(Object.keys(SOURCES).sort(), Object.keys(CASES).sort());
});

for (const [file, c] of Object.entries(CASES)) {
  test(`${file}: API text never becomes markup (setConfig + hass)`, () => {
    htmlWrites = [];
    textWrites = [];
    let Card;
    vm.runInNewContext(SOURCES[file](), {
      HTMLElement: Element,
      customElements: { define: (_, klass) => { Card = klass; }, get: () => undefined },
      document,
      window: { open() {}, addEventListener() {}, removeEventListener() {} },
      setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
      requestAnimationFrame: () => 0,
      URL,
      crypto: globalThis.crypto,
      console,
    });
    const card = new Card();
    card.setConfig(c.config);
    card.hass = { states: c.states, language: 'pl' };
    if (c.after) c.after(card, c.states[ENTITY]);

    const html = htmlWrites.join('\n');
    assert.ok(!html.includes(RAW), `${file}: payload rendered as markup`);
    // Guard against a vacuous pass: the payload must have reached the render,
    // escaped in HTML or as plain text.
    const reached = html.includes(ESCAPED) || textWrites.some((t) => t.includes(PAYLOAD));
    assert.ok(reached, `${file}: payload never rendered - the case does not exercise the card`);
  });
}
