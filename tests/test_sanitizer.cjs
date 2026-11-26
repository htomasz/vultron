// _sanitizeHTML (treść wiadomości do innerHTML) na prawdziwym parserze HTML5 (jsdom)
// oraz pilnowanie, że kopie _sanitizeHTML w kartach są identyczne.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');

// Jedna dosłowna ścieżka na plik (bez fs z ścieżką ze zmiennej).
const SOURCES = {
  'vultron-messages-card.js': fs.readFileSync(path.join(__dirname, '../vultron/vultron-messages-card.js'), 'utf8'),
  'vultron-przedszkole-wiadomosci-card.js': fs.readFileSync(path.join(__dirname, '../vultron/vultron-przedszkole-wiadomosci-card.js'), 'utf8'),
  'vultron-daily-card.js': fs.readFileSync(path.join(__dirname, '../vultron/vultron-daily-card.js'), 'utf8'),
};
// Tekst metody klasy: od nagłówka do zamykającego "  }" na poziomie metody.
const SANITIZE_RE = /\n {2}_sanitizeHTML\([^)]*\) \{\n[\s\S]*?\n {2}\}\n/;

test('_sanitizeHTML: wszystkie kopie identyczne', () => {
  const copies = Object.entries(SOURCES).map(([f, src]) => [f, (src.match(SANITIZE_RE) || [])[0]]);
  for (const [f, body] of copies) assert.ok(body, `${f}: brak _sanitizeHTML`);
  for (const [f, body] of copies.slice(1)) assert.equal(body, copies[0][1], `${f}: inna kopia _sanitizeHTML`);
});

// Klasa karty w środowisku jsdom; _sanitizeHTML nie używa stanu karty.
const dom = new JSDOM('<!DOCTYPE html><body></body>');
let Card;
vm.runInNewContext(SOURCES['vultron-messages-card.js'], {
  HTMLElement: dom.window.HTMLElement,
  customElements: { define: (_, klass) => { Card = klass; }, get: () => undefined },
  document: dom.window.document,
  window: dom.window,
  DOMParser: dom.window.DOMParser,
  Node: dom.window.Node,
  URL,
  console,
});
const sanitize = (html) => Card.prototype._sanitizeHTML.call({}, html);

// Wynik wstawiony do DOM: żadnego aktywnego elementu ani atrybutu.
function assertInert(html) {
  const out = sanitize(html);
  const box = dom.window.document.createElement('div');
  box.innerHTML = out;
  for (const el of box.querySelectorAll('*')) {
    assert.ok(['p', 'br', 'strong', 'b', 'i', 'em', 'u', 'a', 'ul', 'ol', 'li', 'span', 'div'].includes(el.localName),
      `niedozwolony element <${el.localName}> z: ${html}`);
    for (const attr of el.attributes) {
      if (el.localName === 'a' && ['href', 'target', 'rel', 'style'].includes(attr.name)) continue;
      assert.fail(`niedozwolony atrybut ${attr.name} na <${el.localName}> z: ${html}`);
    }
    if (el.localName === 'a') assert.match(el.getAttribute('href'), /^https?:\/\//);
  }
  return out;
}

const VECTORS = [
  '<script>alert(1)</script>',
  '<img src=x onerror=alert(1)>',
  '<svg onload=alert(1)><circle/></svg>',
  '<iframe src="javascript:alert(1)"></iframe>',
  '<a href="javascript:alert(1)">x</a>',
  '<a href="JaVaScRiPt:alert(1)">x</a>',
  '<a href=" javascript:alert(1)">x</a>',
  '<a href="data:text/html,<script>alert(1)</script>">x</a>',
  '<a href="https://example.com" onclick="alert(1)">x</a>',
  '<p style="background:url(javascript:alert(1))" onmouseover="alert(1)">x</p>',
  '<div><span><b onmouseover=alert(1)>x</b></span></div>',
  '<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>',
  '<noscript><p title="</noscript><img src=x onerror=alert(1)>">',
  '<form><button formaction="javascript:alert(1)">x</button></form>',
  '<object data="javascript:alert(1)"></object><embed src="javascript:alert(1)">',
  '<!--<img src=x onerror=alert(1)>-->',
  '<template><img src=x onerror=alert(1)></template>',
  '<base href="javascript:alert(1)//"><a href="/x">x</a>',
];

for (const v of VECTORS) {
  test(`_sanitizeHTML: neutralne dla ${v}`, () => { assertInert(v); });
}

test('_sanitizeHTML: zachowuje zwykłe formatowanie i bezpieczne linki', () => {
  const out = assertInert('<p>Dzień <b>dobry</b><br>Jan Kowalski <a href="https://example.com/x">link</a></p>');
  assert.match(out, /<p>Dzień <b>dobry<\/b><br>Jan Kowalski <a href="https:\/\/example.com\/x"[^>]*rel="noopener noreferrer"[^>]*>link<\/a><\/p>/);
  assert.match(out, /target="_blank"/);
});

test('_sanitizeHTML: tekst zwykły - nowe linie na <br>, znaki specjalne jako tekst', () => {
  assert.equal(sanitize('a\nb'), 'a<br>b');
  assert.equal(sanitize('1 < 2 & 3 > 2'), '1 &lt; 2 &amp; 3 &gt; 2');
  assert.equal(sanitize(''), '');
  assert.equal(sanitize(null), '');
});

test('_sanitizeHTML: zły link zostaje tekstem', () => {
  assert.equal(sanitize('<a href="javascript:alert(1)">kliknij</a>'), 'kliknij');
});
