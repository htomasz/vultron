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

    const dataHash = JSON.stringify(stateObj.attributes.osiagniecia || []);
    if (dataHash === this._lastDataHash) return;
    this._lastDataHash = dataHash;

    if (!this.content) {
      // Tworzymy całą strukturę raz
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
              display: flex;
              justify-content: space-between;
              align-items: center;
              user-select: none;
            }
            .achievement-item:hover { background: var(--secondary-background-color); }
            .chevron { color: var(--divider-color); flex-shrink:0; margin-left:8px; }
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
              background:var(--ha-card-background,var(--card-background-color));
              width:90%;
              max-width:500px;
              max-height:80%;
              border-radius:12px;
              padding:20px;
              overflow-y:auto;
              border:1px solid var(--divider-color);
              user-select:text !important;
            }
            #modal-close { float:right; cursor:pointer; padding:5px; color:var(--secondary-text-color); }
            .modal-header { border-bottom:1px solid var(--divider-color); margin-bottom:15px; padding-bottom:10px; }
            .modal-title { font-size:16px; font-weight:bold; color:var(--primary-color); }
          </style>

          <div style="padding:16px;">
            <div id="header">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;border-bottom:2px solid var(--primary-color);padding-bottom:8px;">
                <div style="font-size:1.1em;font-weight:500;color:var(--primary-text-color);">
                  ${stateObj.attributes.friendly_name || 'Osiągnięcia'}
                </div>
                <div style="display:flex;gap:4px;font-size:0.75em;font-weight:bold;align-items:center;">
                  <span id="btn-sort-desc" style="cursor:pointer;color:${this._sortOrder==='desc'?'var(--primary-color)':'var(--secondary-text-color)'}">NAJNOWSZE</span>
                  <span style="opacity:0.3;">|</span>
                  <span id="btn-sort-asc" style="cursor:pointer;color:${this._sortOrder==='asc'?'var(--primary-color)':'var(--secondary-text-color)'}">NAJSTARSZE</span>
                </div>
              </div>
            </div>
            <div id="list"></div>
          </div>

          <div id="modal-overlay">
            <div id="modal-content">
              <div id="modal-close"><ha-icon icon="mdi:close"></ha-icon></div>
              <div class="modal-header">
                <div class="modal-title">Szczegóły osiągnięcia</div>
              </div>
              <div id="m-body" style="line-height:1.6;font-size:15px;color:var(--primary-text-color);white-space:pre-wrap;"></div>
              <div style="margin-top:20px;text-align:center;">
                <mwc-button raised id="btn-close">Zamknij</mwc-button>
              </div>
            </div>
          </div>
        </ha-card>
      `;

      this.content = this.querySelector('#list');
      this.headerEl = this.querySelector('#header');

      // Modal
      const overlay = this.querySelector('#modal-overlay');
      const closeIcon = this.querySelector('#modal-close');
      const closeBtn = this.querySelector('#btn-close');
      const closeModal = () => overlay.style.display='none';
      overlay.addEventListener('click', e => { if(e.target===overlay) closeModal(); });
      closeIcon.addEventListener('click', closeModal);
      closeBtn.addEventListener('click', closeModal);

      // Sortowanie
      this.querySelector('#btn-sort-desc').addEventListener('click', ()=>{
        this._sortOrder='desc'; this.hass=this._hass;
      });
      this.querySelector('#btn-sort-asc').addEventListener('click', ()=>{
        this._sortOrder='asc'; this.hass=this._hass;
      });
    }

    this.renderData();
    this.updateSortColors();
  }

  updateSortColors() {
    const desc = this.querySelector('#btn-sort-desc');
    const asc = this.querySelector('#btn-sort-asc');
    if(desc) desc.style.color = this._sortOrder==='desc'?'var(--primary-color)':'var(--secondary-text-color)';
    if(asc) asc.style.color = this._sortOrder==='asc'?'var(--primary-color)':'var(--secondary-text-color)';
  }

  _clearItemListeners(){
    this._listeners.filter(l=>l.type==='item').forEach(l=>l.el.removeEventListener('click', l.fn));
    this._listeners = this._listeners.filter(l=>l.type!=='item');
  }

  disconnectedCallback(){
    this._listeners.forEach(l=>{ if(l.fn) l.el.removeEventListener('click',l.fn); });
    this._listeners=[];
  }

  renderData(){
    this._clearItemListeners();
    const stateObj = this._hass.states[this.config.entity];
    if(!stateObj) return;

    let data = [...(stateObj.attributes.osiagniecia||[])];
    data.sort((a,b)=>{
      const idA=parseInt(a.id), idB=parseInt(b.id);
      return this._sortOrder==='desc'?idB-idA:idA-idB;
    });
    if(this.config.limit) data=data.slice(0,this.config.limit);
    this.content.innerHTML='';

    if(data.length===0){
      this.content.innerHTML=`<div style="text-align:center;opacity:.5;">Brak osiągnięć</div>`;
      return;
    }

    data.forEach(item=>{
      const el = document.createElement('div');
      el.className='achievement-item';

      const textDiv = document.createElement('div');
      textDiv.style.flex='1';
      textDiv.style.display='flex';
      textDiv.style.flexDirection='column';

      const lines = (item.tresc||'').split('\n');
      const strong = document.createElement('strong'); strong.innerText=lines[0]; textDiv.appendChild(strong);
      if(lines.length>1){
        const more=document.createElement('div');
        more.innerText='Kliknij, aby zobaczyć całość...';
        more.style.fontSize='12px'; more.style.opacity='.6'; more.style.fontStyle='italic'; more.style.marginTop='4px';
        textDiv.appendChild(more);
      }

      const chevron = document.createElement('ha-icon'); chevron.setAttribute('icon','mdi:chevron-right'); chevron.className='chevron';
      el.appendChild(textDiv); el.appendChild(chevron);

      const fn = ()=>{
        const modal=this.querySelector('#modal-overlay');
        const body=this.querySelector('#m-body');
        if(modal && body){ body.innerText=item.tresc||''; modal.style.display='flex'; }
      };
      el.addEventListener('click',fn);
      this._listeners.push({el,fn,type:'item'});
      this.content.appendChild(el);
    });
  }

  setConfig(config){ if(!config.entity) throw new Error('Entity missing'); this.config=config; }
  getCardSize(){ return 4; }
}

customElements.define('vultron-osiagniecia-card', VultronOsiagnieciaCard);
