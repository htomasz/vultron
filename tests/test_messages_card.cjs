const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// A minimal DOM double keeps these behavior tests dependency-free.
class Element {
  constructor() {
    this.children = [];
    this.nodes = new Map();
    this.style = {};
  }
  set innerHTML(value) { this.html = value; this.children = []; }
  get innerHTML() { return this.html; }
  querySelector(selector) {
    if (!this.nodes.has(selector)) this.nodes.set(selector, new Element());
    return this.nodes.get(selector);
  }
  addEventListener() {}
  appendChild(child) { this.children.push(child); }
}

function setup() {
  const opened = [];
  let Card;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../vultron/vultron-messages-card.js'), 'utf8'), {
    HTMLElement: Element,
    customElements: { define: (_, klass) => { Card = klass; } },
    document: { createElement: () => new Element() },
    window: { open: (...args) => opened.push(args) },
    URL,
  });
  const card = new Card();
  card.setConfig({ entity: 'sensor.messages' });
  return { card, opened };
}

function message(subject, extra = {}) {
  return { temat: subject, nadawca: 'Test sender', data: '2026-09-20 12:00', przeczytana: true, ...extra };
}

function state(messages, extra = {}) {
  return { attributes: { wiadomosci: messages, ...extra } };
}

test('all pages participate in unread sorting and the configured limit', () => {
  const { card } = setup();
  card.setConfig({ entity: 'sensor.messages', limit: 1 });
  card.hass = { states: {
    'sensor.messages': state([message('Read')], { page_entities: ['sensor.page2'] }),
    'sensor.page2': state([message('Unread on page 2', { przeczytana: false })]),
  } };
  assert.equal(card.content.children.length, 1);
  assert.match(card.content.children[0].innerHTML, /Unread on page 2/);
});

test('a page arriving or changing refreshes an unchanged parent', () => {
  const { card } = setup();
  const root = state([message('First')], { page_entities: ['sensor.page2'] });
  card.hass = { states: { 'sensor.messages': root } };
  assert.equal(card.content.children.length, 1);
  card.hass = { states: {
    'sensor.messages': root, 'sensor.page2': state([message('Second')]),
  } };
  assert.equal(card.content.children.length, 2);
  card.hass = { states: {
    'sensor.messages': root, 'sensor.page2': state([message('Updated second')]),
  } };
  assert.match(card.content.children[1].innerHTML, /Updated second/);
  card.hass = { states: { 'sensor.messages': state([message('First')]) } };
  assert.equal(card.content.children.length, 1);
});

test('header-only GPE messages open the HTTPS inbox without modifying read status', () => {
  const { card, opened } = setup();
  const url = 'https://uonetplus-wiadomosciplus.edu.gdansk.pl/gdansk/App/odebrane';
  const msg = message('Subject', { url, przeczytana: false });
  card.hass = { states: { 'sensor.messages': state([msg]) } };
  card.content.children[0].onclick();
  assert.deepEqual(opened, [[url, '_blank', 'noopener,noreferrer']]);
  assert.equal(msg.przeczytana, false);
});

test('unsafe and malformed links stay in the local preview', () => {
  for (const url of ['javascript:alert(1)', 'http://example.com', 'https://user:secret@example.com', 'not a URL']) {
    const { card, opened } = setup();
    card.hass = { states: { 'sensor.messages': state([message('Subject', { url })]) } };
    card.content.children[0].onclick();
    assert.equal(opened.length, 0);
    assert.equal(card.querySelector('#modal-overlay').style.display, 'flex');
    assert.equal(card.querySelector('#m-subject').innerText, 'Subject');
  }
});

test('existing single-sensor messages still use the sanitized body preview', () => {
  const { card, opened } = setup();
  const body = '<p>Example body</p>';
  let sanitized;
  card._sanitizeHTML = text => { sanitized = text; return 'Sanitized body'; };
  card.hass = { states: { 'sensor.messages': state([message('Existing', { tresc: body, url: 'https://example.com' })]) } };
  card.content.children[0].onclick();
  assert.equal(opened.length, 0);
  assert.equal(sanitized, body);
  assert.equal(card.querySelector('#m-body').innerHTML, 'Sanitized body');
  assert.equal(card.querySelector('#modal-overlay').style.display, 'flex');
});
