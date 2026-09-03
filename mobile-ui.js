(()=>{
  const top=document.querySelector('.top');
  const brand=document.querySelector('.brand');
  if(top&&brand&&!document.getElementById('edujobTabs')){
    const nav=document.createElement('nav');
    nav.id='edujobTabs';
    nav.setAttribute('aria-label','채용 구분');
    nav.style.cssText='display:flex;gap:6px;align-items:center;margin-left:auto;margin-right:8px';
    const mk=(href,text,on)=>{
      const a=document.createElement('a');
      a.href=href;
      a.textContent=text;
      a.style.cssText=`font-size:11px;font-weight:850;padding:7px 10px;border-radius:9px;border:1px solid ${on?'#8ed9ad':'#dfe3e6'};background:${on?'#eafff2':'#fff'};color:${on?'#07863c':'#555'};white-space:nowrap`;
      return a;
    };
    nav.append(mk('./','학교·교육청 채용',true),mk('lessoninfo-jobs.html','학원·민간 구인',false));
    const official=top.querySelector('.official-links');
    top.insertBefore(nav,official||null);
    const fitNav=()=>{
      if(window.matchMedia('(max-width:650px)').matches){
        nav.style.order='3';nav.style.width='100%';nav.style.margin='4px 0 0';
        nav.querySelectorAll('a').forEach(a=>{a.style.flex='1';a.style.textAlign='center'});
      }else{
        nav.style.order='';nav.style.width='';nav.style.marginLeft='auto';nav.style.marginRight='8px';nav.style.marginTop='';
        nav.querySelectorAll('a').forEach(a=>{a.style.flex='';a.style.textAlign=''});
      }
    };
    window.addEventListener('resize',fitNav,{passive:true});fitNav();
  }

  // Keep Lessoninfo visible in the source-status panel even though the default feed remains
  // the official 38-source feed. The actual private postings live in the separate tab.
  let lessoninfoReport=null;
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const ensureLessoninfoStatus=()=>{
    const grid=document.getElementById('sourceGrid');
    const details=document.getElementById('sourceStatus');
    if(!grid||!details||!lessoninfoReport||document.getElementById('lessoninfoSourceCol'))return;
    const r=lessoninfoReport;
    const healthy=r.healthy===true&&r.traversalComplete===true&&Number(r.missingAfterCount||0)===0;
    const per=r.perSurfaceActive||{};
    const total=Number(r.activeIdCount??r.publishedIdCount??0);
    const after=Number(per['afterschool-nulbom']||0);
    const culture=Number(per['culture-arts']||0);
    const col=document.createElement('div');
    col.className='source-col';col.id='lessoninfoSourceCol';
    col.innerHTML=`<h4>민간 구인 · 레슨인포</h4>`+
      `<div class="source-line"><span>레슨인포 현재 구인</span><span class="${healthy?'ok':'warn'}">${healthy?total.toLocaleString()+'건':'점검 중'}</span></div>`+
      `<div class="source-line"><span>방과후교사·늘봄학교 강사</span><span class="${healthy?'ok':'warn'}">${healthy?after.toLocaleString()+'건':'-'}</span></div>`+
      `<div class="source-line"><span>문화예술 채용</span><span class="${healthy?'ok':'warn'}">${healthy?culture.toLocaleString()+'건':'-'}</span></div>`+
      `<div class="source-line"><span>별도 공고 화면</span><span><a href="lessoninfo-jobs.html" style="color:#07863c;font-weight:800">학원·민간 구인 ↗</a></span></div>`;
    grid.appendChild(col);
    details.style.display='block';
  };
  fetch('lessoninfo_reconciliation_report.json',{cache:'no-cache'}).then(r=>r.ok?r.json():Promise.reject()).then(r=>{
    lessoninfoReport=r;ensureLessoninfoStatus();
    const grid=document.getElementById('sourceGrid');
    if(grid){new MutationObserver(()=>ensureLessoninfoStatus()).observe(grid,{childList:true});}
  }).catch(()=>{});

  const bp=900;
  const panel=document.querySelector('.filter-panel');
  if(!panel)return;
  const head=panel.querySelector('.filter-head');
  if(!head)return;
  let btn=document.getElementById('mobileFilterToggle');
  if(!btn){
    btn=document.createElement('button');btn.type='button';btn.id='mobileFilterToggle';btn.className='mobile-filter-toggle';btn.setAttribute('aria-controls','mobileFilters');head.appendChild(btn);
  }
  panel.id=panel.id||'mobileFilters';
  const apply=()=>{
    const mobile=window.matchMedia(`(max-width:${bp}px)`).matches;
    if(mobile){
      if(!panel.dataset.mobileInit){panel.classList.add('mobile-collapsed');panel.dataset.mobileInit='1';}
      btn.hidden=false;btn.setAttribute('aria-expanded',String(!panel.classList.contains('mobile-collapsed')));btn.textContent=panel.classList.contains('mobile-collapsed')?'필터 열기':'필터 닫기';
    }else{
      panel.classList.remove('mobile-collapsed');delete panel.dataset.mobileInit;btn.hidden=true;btn.setAttribute('aria-expanded','true');
    }
  };
  btn.addEventListener('click',()=>{panel.classList.toggle('mobile-collapsed');btn.setAttribute('aria-expanded',String(!panel.classList.contains('mobile-collapsed')));btn.textContent=panel.classList.contains('mobile-collapsed')?'필터 열기':'필터 닫기';});
  window.addEventListener('resize',apply,{passive:true});apply();
})();