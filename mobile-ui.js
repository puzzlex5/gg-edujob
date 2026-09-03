(()=>{
  const top=document.querySelector('.top');
  const brand=document.querySelector('.brand');
  if(top&&brand&&!document.getElementById('edujobTabs')){
    const nav=document.createElement('nav');
    nav.id='edujobTabs';
    nav.setAttribute('aria-label','구인공고 범위');
    nav.style.cssText='display:flex;gap:6px;align-items:center;margin-left:auto;margin-right:8px';
    const scope=new URLSearchParams(location.search).get('scope')||'all';
    const mk=(href,text,key)=>{
      const on=scope===key;
      const a=document.createElement('a');a.href=href;a.textContent=text;
      a.style.cssText=`font-size:11px;font-weight:850;padding:7px 10px;border-radius:9px;border:1px solid ${on?'#8ed9ad':'#dfe3e6'};background:${on?'#eafff2':'#fff'};color:${on?'#07863c':'#555'};white-space:nowrap`;
      return a;
    };
    nav.append(
      mk('./?scope=all','전체 구인','all'),
      mk('./?scope=official','학교·교육청','official'),
      mk('./?scope=private','학원·민간','private')
    );
    const official=top.querySelector('.official-links');top.insertBefore(nav,official||null);
    const fitNav=()=>{if(window.matchMedia('(max-width:650px)').matches){nav.style.order='3';nav.style.width='100%';nav.style.margin='4px 0 0';nav.querySelectorAll('a').forEach(a=>{a.style.flex='1';a.style.textAlign='center';a.style.padding='7px 5px'})}else{nav.style.order='';nav.style.width='';nav.style.marginLeft='auto';nav.style.marginRight='8px';nav.style.marginTop='';nav.querySelectorAll('a').forEach(a=>{a.style.flex='';a.style.textAlign='';a.style.padding='7px 10px'})}};
    window.addEventListener('resize',fitNav,{passive:true});fitNav();
  }

  const bp=900;
  const panel=document.querySelector('.filter-panel');if(!panel)return;
  const head=panel.querySelector('.filter-head');if(!head)return;
  let btn=document.getElementById('mobileFilterToggle');
  if(!btn){btn=document.createElement('button');btn.type='button';btn.id='mobileFilterToggle';btn.className='mobile-filter-toggle';btn.setAttribute('aria-controls','mobileFilters');head.appendChild(btn)}
  panel.id=panel.id||'mobileFilters';
  const apply=()=>{const mobile=window.matchMedia(`(max-width:${bp}px)`).matches;if(mobile){if(!panel.dataset.mobileInit){panel.classList.add('mobile-collapsed');panel.dataset.mobileInit='1'}btn.hidden=false;btn.setAttribute('aria-expanded',String(!panel.classList.contains('mobile-collapsed')));btn.textContent=panel.classList.contains('mobile-collapsed')?'필터 열기':'필터 닫기'}else{panel.classList.remove('mobile-collapsed');delete panel.dataset.mobileInit;btn.hidden=true;btn.setAttribute('aria-expanded','true')}};
  btn.addEventListener('click',()=>{panel.classList.toggle('mobile-collapsed');btn.setAttribute('aria-expanded',String(!panel.classList.contains('mobile-collapsed')));btn.textContent=panel.classList.contains('mobile-collapsed')?'필터 열기':'필터 닫기'});
  window.addEventListener('resize',apply,{passive:true});apply();
})();
