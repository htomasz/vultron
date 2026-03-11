class VultronMessagesCard extends HTMLElement {
  constructor() {
    super();
    this._cachedState = null;
  }

  // 1. Zwykły escape - neutralizuje wszystko. Używamy tego do tytułów i nadawców.
  _esc(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // 2. Zaawansowany, natywny Sanitizer HTML - tylko do treści wiadomości
  _sanitizeHTML(htmlString) {
    if (!htmlString) return "";

    // Lista dozwolonych tagów (z wielkich liter, bo tak przetwarza je DOM)
    const allowedTags = ['P', 'BR', 'STRONG', 'B', 'I', 'EM', 'U', 'A', 'UL', 'OL', 'LI', 'SPAN', 'DIV'];

    // Tworzymy wirtualny dokument w pamięci (bezpieczne parsowanie)
    const parser = new DOMParser();
    const doc = parser.parseFromString(htmlString, 'text/html');

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

  _normalizeDateToISO(dateStr) {
    if (!dateStr || typeof dateStr !== 'string') return '—';
    if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) return dateStr;
    if (/^\d{4}-\d{2}-\d{2}[\s+T]\d{1,2}:\d{2}/.test(dateStr)) return dateStr.split(/[\sT]/)[0];

    const parts = dateStr.split('.').map(p => p.trim());
    if (parts.length === 2 || parts.length === 3) {
      const day   = parts[0].padStart(2, '0');
      const month = parts[1].padStart(2, '0');
      let year    = parts[2] || new Date().getFullYear().toString();
      if (year.length === 2) year = (parseInt(year, 10) < 70 ? '20' : '19') + year;
      if (year.length === 4 && !isNaN(parseInt(day)) && !isNaN(parseInt(month))) {
        return `${year}-${month}-${day}`;
      }
    }
    return dateStr;
  }

  set hass(hass) {
    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <style>
            .message-item {
              padding: 12px 14px;
              border-radius: 8px;
              cursor: pointer;
              background: var(--card-background-color);
              transition: background 0.2s;
              margin-bottom: 8px;
              border: 1px solid var(--divider-color);
              user-select: none;
              display: flex;
              justify-content: space-between;
              align-items: flex-start;
            }
            .message-item:hover {
              background: var(--secondary-background-color);
            }
            .unread {
              border: 2px solid var(--error-color) !important;
              box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            }
            .chevron {
              color: var(--divider-color);
              margin-top: 6px;
              flex-shrink: 0;
            }
            #modal-overlay {
              display: none;
              position: fixed;
              top: 0; left: 0; width: 100%; height: 100%;
              background: rgba(0,0,0,0.7);
              z-index: 10000;
              align-items: center;
              justify-content: center;
              backdrop-filter: blur(3px);
              user-select: none;
            }
            #modal-content {
              background: var(--ha-card-background, var(--card-background-color));
              width: 90%;
              max-width: 600px;
              max-height: 85%;
              border-radius: 12px;
              padding: 20px;
              overflow-y: auto;
              box-shadow: 0 10px 25px rgba(0,0,0,0.5);
              border: 1px solid var(--divider-color);
              user-select: text !important;
            }
            #modal-close {
              float: right;
              cursor: pointer;
              padding: 5px;
              color: var(--secondary-text-color);
            }
            .modal-header { border-bottom: 1px solid var(--divider-color); margin-bottom: 15px; padding-bottom: 10px; }
            .modal-body {
              line-height: 1.6;
              font-size: 15px;
              color: var(--primary-text-color);
            }
            .modal-body p { margin-top: 0; margin-bottom: 10px; }
            .modal-meta { font-size: 12px; color: var(--secondary-text-color); margin-bottom: 5px; }
            .modal-subject { font-size: 16px; font-weight: bold; margin-bottom: 10px; color: var(--primary-color); }
          </style>

          <div id="container" style="padding: 16px;">
            <div id="header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">
              <div id="title" style="font-size: 1.1em; font-weight: 500; color: var(--primary-text-color);">Wiadomości</div>
              <div id="stats" style="font-size: 0.85em; font-weight: bold; color: var(--primary-color);"></div>
            </div>
            <div id="messages-list"></div>
          </div>

          <div id="modal-overlay">
            <div id="modal-content">
              <div id="modal-close"><ha-icon icon="mdi:close"></ha-icon></div>
              <div class="modal-header">
                <div id="m-meta" class="modal-meta"></div>
                <div id="m-sender" style="font-weight: bold; font-size: 15px; color: var(--primary-text-color);"></div>
                <div id="m-subject" class="modal-subject"></div>
              </div>
              <div id="m-body" class="modal-body"></div>
              <div style="margin-top: 20px; text-align: center;">
                <mwc-button raised id="btn-close">Zamknij</mwc-button>
              </div>
            </div>
          </div>
        </ha-card>
      `;
      this.content = this.querySelector('#messages-list');
      this.stats = this.querySelector('#stats');
      this.titleEl = this.querySelector('#title');

      const overlay = this.querySelector('#modal-overlay');
      overlay.addEventListener('click', (e) => {
        if (
          e.target === overlay ||
          e.target.closest('#modal-close') ||
          e.target.closest('#btn-close')
        ) {
          overlay.style.display = 'none';
        }
      });
    }

    const stateObj = hass.states[this.config.entity];
    if (!stateObj) return;

    if (this._cachedState === stateObj) return;
    this._cachedState = stateObj;

    const rawMessages = stateObj.attributes.wiadomosci || [];
    this.stats.innerText = stateObj.attributes.stats || "";
    this.titleEl.innerText = stateObj.attributes.friendly_name || "Wiadomości";

    let sortedMessages = [...rawMessages];
    if (this.config.limit && this.config.limit > 0) sortedMessages = sortedMessages.slice(0, this.config.limit);

    if (sortedMessages.length === 0) {
      this.content.innerHTML = `<div style="text-align: center; padding: 20px; opacity: 0.5;">Brak wiadomości</div>`;
      return;
    }

    this.content.innerHTML = '';
    sortedMessages.forEach((msg) => {
      const isUnread = msg.przeczytana === false;
      const displayDate = this._normalizeDateToISO(msg.data);

      const item = document.createElement('div');
      item.className = `message-item${isUnread ? ' unread' : ''}`;

      item.innerHTML = `
        <div style="flex: 1; position: relative; padding-right: 80px;">
          <div style="position: absolute; top: 10px; right: 12px;">
            <span style="font-weight: 600; color: var(--primary-color); background: var(--secondary-background-color); padding: 3px 8px; border-radius: 6px; font-size: 0.81em; white-space: nowrap;">
              ${this._esc(displayDate)}
            </span>
          </div>
          ${isUnread ? `<ha-icon icon="mdi:circle" style="position: absolute; top: 32px; right: 14px; --mdc-icon-size: 10px; color: var(--error-color);"></ha-icon>` : ''}
          <div style="font-weight: ${isUnread ? 'bold' : 'normal'}; font-size: 1.05em; color: var(--primary-text-color); margin-bottom: 4px; padding-top: 2px;">
            ${this._esc(msg.nadawca || '?')}
          </div>
          <div style="font-size: 0.93em; color: var(--primary-text-color); opacity: 0.92; line-height: 1.38;">
            ${this._esc(msg.temat || '(brak tematu)')}
          </div>
        </div>
        <ha-icon icon="mdi:chevron-right" class="chevron"></ha-icon>
      `;

      item.onclick = () => {
        // Tytuł i nadawca to zwykły tekst (innerText) = pełne bezpieczeństwo
        this.querySelector('#m-meta').innerText = displayDate;
        this.querySelector('#m-sender').innerText = msg.nadawca || '—';
        this.querySelector('#m-subject').innerText = msg.temat || '(brak tematu)';

        // Treść przepuszczamy przez nasz nowy, bezpieczny system!
        this.querySelector('#m-body').innerHTML = msg.tresc
          ? this._sanitizeHTML(msg.tresc)
          : "Treść wiadomości archiwalnej dostępna w aplikacji EduVulcan.";

        this.querySelector('#modal-overlay').style.display = 'flex';
      };

      this.content.appendChild(item);
    });
  }

  setConfig(config) { if (!config.entity) throw new Error('Entity missing'); this.config = config; }
  getCardSize() { return 4; }
}

customElements.define('vultron-messages-card', VultronMessagesCard);
