const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { setImmediate: tick } = require('node:timers/promises');

// Minimalny DOM wystarczy: logika nowości jest w statycznej metodzie.
class Element {
  constructor() { this.nodes = new Map(); this.style = {}; this.dataset = {}; }
  set innerHTML(v) { this.html = String(v); }
  get innerHTML() { return this.html || ''; }
  set textContent(v) { this.text = String(v); }
  querySelector(sel) {
    if (!this.nodes.has(sel)) this.nodes.set(sel, new Element());
    return this.nodes.get(sel);
  }
  querySelectorAll() { return []; }
  addEventListener() {}
}

function load() {
  let Card;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../vultron/vultron-daily-card.js'), 'utf8'), {
    HTMLElement: Element,
    customElements: { define: (_, klass) => { Card = klass; } },
  });
  return Card;
}

const Card = load();
const T0 = 1763500000; // sekundy, jak "lu" w historii HA
const uwaga = (id) => ({ id, data: '2026-11-19', tresc: 'x', autor: 'Jan Kowalski', kategoria: 'Zachowanie', typ: 'negatywna' });

test('pierwszy stan z danymi to punkt odniesienia, nie nowość', () => {
  const out = Card.nowosci('uwagi', [{ s: '1', a: { uwagi: [uwaga('1')] }, lu: T0 }]);
  assert.equal(out.length, 0);
});

test('nowy element dostaje czas stanu, w którym się pojawił', () => {
  const out = Card.nowosci('uwagi', [
    { s: '1', a: { uwagi: [uwaga('1')] }, lu: T0 },
    { s: '2', a: { uwagi: [uwaga('2'), uwaga('1')] }, lu: T0 + 60 },
  ]);
  assert.equal(out.length, 1);
  assert.equal(out[0].ts, (T0 + 60) * 1000);
  assert.equal(out[0].tekst, 'Uwaga');
});

test('restart HA (unavailable, potem ta sama lista) nie daje nowości', () => {
  const out = Card.nowosci('uwagi', [
    { s: 'unavailable', a: {}, lu: T0 - 10 },
    { s: '1', a: { uwagi: [uwaga('1')] }, lu: T0 },
    { s: 'unavailable', lu: T0 + 10 },
    { s: '1', a: { uwagi: [uwaga('1')] }, lu: T0 + 20 },
  ]);
  assert.equal(out.length, 0);
});

test('element, który zniknął z przyciętej listy i wrócił, nie jest nowy', () => {
  const out = Card.nowosci('uwagi', [
    { s: '2', a: { uwagi: [uwaga('1'), uwaga('2')] }, lu: T0 },
    { s: '2', a: { uwagi: [uwaga('1')] }, lu: T0 + 10 },
    { s: '2', a: { uwagi: [uwaga('1'), uwaga('2')] }, lu: T0 + 20 },
  ]);
  assert.equal(out.length, 0);
});

test('zmiana statusu przeczytania wiadomości nie jest nowością', () => {
  const m = (przeczytana) => ({ id: 'a1', data: '2026-11-19 08:00', nadawca: 'Jan Kowalski', temat: 'Wycieczka', tresc: '', przeczytana });
  const out = Card.nowosci('wiadomosci', [
    { s: '1', a: { wiadomosci: [m(false)] }, lu: T0 },
    { s: '0', a: { wiadomosci: [m(true)] }, lu: T0 + 10 },
  ]);
  assert.equal(out.length, 0);
});

test('oceny: nowa cząstkowa i proponowana, terminarz z terminem', () => {
  const przedmiot = (oceny, proponowana) => ({ lista_przedmiotow: [{ przedmiot: 'Fizyka', oceny, proponowana }] });
  const o1 = { w: '4', d: '18.11', i: 'Kartkówka: ruch' };
  const o2 = { w: '5', d: '19.11', i: 'Sprawdzian: siły' };
  const oc = Card.nowosci('oceny', [
    { s: '0', a: przedmiot([o1], null), lu: T0 },
    { s: '1', a: przedmiot([o1, o2], '5'), lu: T0 + 10 },
  ]);
  assert.deepEqual([...oc.map((z) => z.tekst)], ['Ocena 5 — Fizyka', 'Ocena proponowana 5 — Fizyka']);

  const t = { data: '2026-11-26', przedmiot: 'Matematyka', typ: 'Sprawdzian', opis: 'x', autor: 'Jan Kowalski' };
  const term = Card.nowosci('terminarz', [
    { s: '0', a: { lista: [] }, lu: T0 },
    { s: '1', a: { lista: [t] }, lu: T0 + 10 },
  ]);
  assert.equal(term[0].tekst, 'Sprawdzian — Matematyka');
  assert.equal(term[0].opis, 'na 2026-11-26');
});

test('karta pyta historię tylko o encje dziecka i renderuje nowości', async () => {
  const card = new Card();
  card.setConfig({ slug: 'jan_kowalski', days: 3 });
  let zapytanie;
  const states = {
    'sensor.vultron_uwagi_jan_kowalski': { last_updated: 'a' },
    'sensor.vultron_oceny_jan_kowalski_p1': { last_updated: 'b' },
    'sensor.vultron_uwagi_anna_kowalska': { last_updated: 'c' },
  };
  const teraz = Math.floor(Date.now() / 1000);
  const hass = {
    states,
    callWS: async (msg) => {
      zapytanie = msg;
      return { 'sensor.vultron_uwagi_jan_kowalski': [
        { s: '1', a: { uwagi: [uwaga('1')] }, lu: teraz - 120 },
        { s: '2', a: { uwagi: [uwaga('2'), uwaga('1')] }, lu: teraz - 60 },
      ] };
    },
  };
  card.hass = hass;
  await tick();
  assert.deepEqual([...zapytanie.entity_ids], ['sensor.vultron_uwagi_jan_kowalski', 'sensor.vultron_oceny_jan_kowalski_p1']);
  assert.equal(zapytanie.no_attributes, false);
  assert.equal(zapytanie.significant_changes_only, false);
  assert.ok(card._body.innerHTML.includes('Uwaga'));
  assert.ok(card._body.innerHTML.includes('Zachowanie'));
  // Oceny bez historii (np. wykluczone z recordera): karta mówi to wprost.
  assert.ok(card._body.innerHTML.includes('Bez historii HA (recorder): oceny'));
});
