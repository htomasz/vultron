const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// getGradeColor bez DOM: wystarczy klasa karty.
let Card;
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../vultron/vultron-grades-card.js'), 'utf8'), {
  HTMLElement: class {},
  customElements: { define: (_, klass) => { Card = klass; }, get: () => undefined },
  document: {},
  window: {},
  console,
});
const color = (val) => Card.prototype.getGradeColor.call({}, val);

const BLUE = '#2196F3';
const GREEN = '#4CAF50';
const ORANGE = '#FF9800';
const RED = '#F44336';
const GREY = '#9E9E9E';
const DEFAULT = 'var(--primary-text-color)';

test('ocena procentowa jest niebieska', () => {
  for (const v of ['47%', '47 (%)', '47(%)', '47 ( % )', '100.5%', '12,5 %']) {
    assert.equal(color(v), BLUE, v);
  }
});

test('adnotacja procentowa nie zmienia koloru oceny', () => {
  assert.equal(color('3 (56%)'), ORANGE);
  assert.equal(color('3  ( 56,5 % )'), ORANGE);
  assert.equal(color('5+ (100%)'), GREEN);
  assert.equal(color('1 (12%)'), RED);
});

test('oceny bez procentów', () => {
  assert.equal(color('6'), GREEN);
  assert.equal(color('2-'), RED);
  assert.equal(color('NB'), GREY);
  assert.equal(color('np'), GREY);
  assert.equal(color(''), DEFAULT);
  assert.equal(color(null), DEFAULT);
});

test('długi tekst nie powoduje kwadratowego czasu wyrażeń regularnych', () => {
  const pad = ' '.repeat(50000);
  const start = Date.now();
  color(`1${pad}x`);
  color(`3${pad}(`);
  color('('.repeat(50000));
  // Przed poprawką ok. 8 s; po niej kilka ms. Duży zapas na wolny runner.
  assert.ok(Date.now() - start < 1000, 'getGradeColor za wolne dla długiego wejścia');
});
