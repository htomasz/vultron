class VultronOsiagnieciaCard extends HTMLElement {
  constructor() {
    super();
    this._sortOrder = 'desc';
    this._listeners = [];
    this._lastDataHash = null;
  }

  set hass(hass) {
    this._hass = hass;

    const stateObj = hass.states[this.config.entity];
    if (!stateObj) return;

    const rawData = stateObj.attributes.osiagniecia || [];
    const hash = JSON.stringify(rawData);

    // jeśli dane się nie zmieniły → brak renderu
    if (hash === this._lastDataHash) return;
    this._lastDataHash = hash;

    if (!this.content) {
      this.innerHTML = `
        <ha-card>
          <style>
            .achievement-item {
              padding: 12px;
              border-radius: 8px;
              cursor: pointer;
              background: var(--card-background-color);
              transition: background 0.2s;
              margin-bottom: 8px;
              border: 1px solid var(--divider-color);
            }
            .achievement-item:hover {
              background: var(--secondary-background-color);
            }

            #modal-overlay {
              display:none;
              position:fixed;
              inset:0;
              background:rgba(0,0,0,0.7);
              z-index:1000;
              align-items:center;
              justify-content:center;
              backdrop-filter:blur(3px);
            }

            #modal-content {
              background:var(--card-background-color);
              width:90%;
              max-width:500px;
              max-height:80%;
              border-radius:12px;
              padding:20px;
              overflow-y:auto;
              border:1px solid var(--divider-color);
            }

            .modal-header {
              border-bottom:1px solid var(--divider-color);
              margin-bottom:15px;
              padding-bottom:10px;
            }

            .modal-title {
              font-weight:bold;
              color:var(--primary-color);
            }

            .sort-link{
              cursor:pointer;
              font-size:0.75em;
              font-weight:bold;
            }
          </style>

          <div style="padding:16px;">
            <div style="display:flex;justify-content:space-between;border-bottom:2px solid var(--primary-color);padding-bottom:8px;margin-bottom:12px;">
              <div id="title">Osiągnięcia</div>
              <div>
                <span id="btn-sort-desc" class="sort-link">NAJNOWSZE</span>
                |
                <span id="btn-sort-asc" class="sort-link">NAJSTARSZE</span>
              </div>
            </div>
            <div id="list"></div>
          </div>

          <div id="modal-overlay">
            <div id="modal-content">
              <div style="text-align:right">
                <ha-icon id="modal-close" icon="mdi:close"></ha-icon>
              </div>
              <div class="modal-header">
                <div class="modal-title">Szczegóły osiągnięcia</div>
              </div>
              <div id="m-body"></div>
              <div style="text-align:center;margin-top:20px;">
                <mwc-button raised id="btn-close">Zamknij</mwc-button>
              </div>
            </div>
          </div>
        </ha-card>
      `;

      this.content = this.querySelector('#list');
      this.titleEl = this.querySelector('#title');

      const overlay = this.querySelector('#modal-overlay');
      const closeIcon = this.querySelector('#modal-close');
      const closeBtn = this.querySelector('#btn-close');

      const closeModal = () => overlay.style.display = 'none';

      const overlayClick = (e) => {
        if (e.target === overlay) closeModal();
      };

      overlay.addEventListener('click', overlayClick);
      closeIcon.addEventListener('click', closeModal);
      closeBtn.addEventListener('click', closeModal);

      this._listeners.push(
        {el:overlay,fn:overlayClick,type:'modal'},
        {el:closeIcon,fn:closeModal,type:'modal'},
        {el:closeBtn,fn:closeModal,type:'modal'}
      );
    }

    this.renderData();
  }

  _clearItemListeners() {
    this._listeners
      .filter(l => l.type === 'item')
      .forEach(l => l.el.removeEventListener('click', l.fn));

    this._listeners = this._listeners.filter(l => l.type !== 'item');
  }

  disconnectedCallback() {
    this._listeners.forEach(l => l.el.removeEventListener('click', l.fn));
    this._listeners = [];
  }

  renderData() {
    this._clearItemListeners();

    const stateObj = this._hass.states[this.config.entity];
    if (!stateObj) return;

    const rawData = stateObj.attributes.osiagniecia || [];

    this.titleEl.innerText =
      stateObj.attributes.friendly_name || "Osiągnięcia";

    let data = [...rawData].sort((a,b)=>{
      const idA=parseInt(a.id);
      const idB=parseInt(b.id);
      return this._sortOrder==='desc' ? idB-idA : idA-idB;
    });

    if(this.config.limit) data=data.slice(0,this.config.limit);

    this.content.innerHTML='';

    if(data.length===0){
      this.content.innerHTML =
        `<div style="text-align:center;opacity:.5">Brak osiągnięć</div>`;
      return;
    }

    data.forEach(item=>{
      const el=document.createElement('div');
      el.className='achievement-item';

      const lines=(item.tresc||'').split('\n');
      const firstLine=lines[0];

      const title=document.createElement('strong');
      title.innerText=firstLine;

      const row=document.createElement('div');
      row.appendChild(title);

      el.appendChild(row);

      const openModal=()=>{
        this.querySelector('#m-body').innerText=item.tresc||'';
        this.querySelector('#modal-overlay').style.display='flex';
      };

      el.addEventListener('click',openModal);

      this._listeners.push({el,fn:openModal,type:'item'});

      this.content.appendChild(el);
    });
  }

  setConfig(config){
    if(!config.entity) throw new Error('Entity missing');
    this.config=config;
  }

  getCardSize(){return 4;}
}

customElements.define(
  'vultron-osiagniecia-card',
  VultronOsiagnieciaCard
);
