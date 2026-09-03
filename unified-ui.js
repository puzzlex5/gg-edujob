(()=>{
  if(typeof state==='undefined'||typeof filtered!=='function'||typeof card!=='function') return;
  state.sources=state.sources||new Set();
  state.categories=state.categories||new Set();
  const SOURCE_VALUES=['공식','민간'];
  const CATEGORY_VALUES=['방과후·늘봄','음악·예체능','문화예술','학원강사','강사·교사','기타 민간구인'];
  const sourceLabel=j=>j.feedKind==='private'?'민간':'공식';
  const categorySet=j=>new Set(Array.isArray(j.categories)?j.categories:[]);

  function addFilterSection(id,title,values,key){
    const panel=document.querySelector('.filter-panel');
    const selected=document.getElementById('selectedBox');
    if(!panel||!selected||document.getElementById(id)) return;
    const section=document.createElement('div');section.className='filter-section';section.id=id;
    section.innerHTML=`<div class="filter-title"><h3>${title}</h3><span class="hint">중복 선택 가능</span></div><div class="checks one unified-checks"></div>`;
    const box=section.querySelector('.unified-checks');
    values.forEach(v=>{const label=document.createElement('label');label.className='check';const input=document.createElement('input');input.type='checkbox';input.value=v;input.checked=state[key].has(v);const span=document.createElement('span');span.textContent=v;input.addEventListener('change',()=>{input.checked?state[key].add(v):state[key].delete(v);render()});label.append(input,span);box.appendChild(label)});
    panel.insertBefore(section,selected);
  }
  addFilterSection('sourceKindFilters','공고 구분',SOURCE_VALUES,'sources');
  addFilterSection('categoryFilters','구인 분야',CATEGORY_VALUES,'categories');

  const scope=new URLSearchParams(location.search).get('scope');
  if(scope==='official') state.sources=new Set(['공식']);
  if(scope==='private') state.sources=new Set(['민간']);
  document.querySelectorAll('#sourceKindFilters input').forEach(i=>i.checked=state.sources.has(i.value));

  const baseFiltered=filtered;
  filtered=function(){
    return baseFiltered().filter(j=>{
      if(state.sources.size&&!state.sources.has(sourceLabel(j))) return false;
      if(state.categories.size){const cs=categorySet(j);if(![...state.categories].some(c=>cs.has(c))) return false;}
      return true;
    });
  };

  selectionSummary=function(){
    const parts=[];
    if(state.provinces.size)parts.push(`시·도 ${[...state.provinces].join(', ')}`);
    if(state.regions.size)parts.push(`지역 ${state.regions.size}곳`);
    if(state.schools.size)parts.push(`학교급 ${[...state.schools].join(', ')}`);
    if(state.types.size)parts.push(`직종 ${[...state.types].join(', ')}`);
    if(state.sources.size)parts.push(`공고 ${[...state.sources].join('·')}`);
    if(state.categories.size)parts.push(`분야 ${[...state.categories].join('·')}`);
    const box=document.getElementById('selectedBox');
    if(parts.length){box.className='selected-box show';box.innerHTML='<b>선택된 조건</b><br>'+parts.map(esc).join(' · ')}
    else{box.className='selected-box';box.textContent=''}
  };

  card=function(j){
    const d=diffDay(j.applyEnd),today=isToday(j.registered),prov=province(j),lvl=schoolLevel(j),typ=jobType(j);
    const dBadge=d===0?'오늘 마감':d!==null&&d>0&&d<=7?`D-${d}`:'';
    const regionText=j.location||j.region||((j.regions||[]).join('·'))||prov;
    const href=postingLink(j),tag=href?'a':'div',linkAttrs=href?` href="${esc(href)}" target="_blank" rel="noopener"`:'';
    const privateJob=j.feedKind==='private';
    const trust=privateJob?'민간 · 레슨인포':`공식 · ${esc(j.source||'공식 채용 게시판')}`;
    const trustStyle=privateJob?'background:#fff1dd;color:#9a5a00':'background:#eafaf0;color:#087a42';
    const surface=privateJob&&j.sourceSurfaceLabel?`<span class="badge">${esc(j.sourceSurfaceLabel)}</span>`:'';
    const seen=(j.alsoSeenOn||[]).length?'<span class="badge" style="background:#eef4ff;color:#295e9b">다른 출처에서도 확인됨</span>':'';
    const cats=(j.categories||[]).slice(0,2).map(c=>`<span class="badge type">${esc(c)}</span>`).join('');
    const goText=href?'원문 공고 바로가기 ↗':'원문 링크 점검 중';
    return `<${tag} class="job"${linkAttrs}><div class="jobtop"><div style="min-width:0;flex:1"><div class="badges">${dBadge?`<span class="badge d">${dBadge}</span>`:''}${today?'<span class="badge today">NEW 오늘</span>':''}<span class="badge" style="${trustStyle}">${trust}</span><span class="badge prov ${prov==='서울'?'seoul':''}">${esc(prov)}</span>${surface}${cats}${seen}</div><h2>${esc(j.title||'채용 공고')}</h2><div class="school">${esc(j.school||'기관명 확인')}</div><div class="meta"><div><b>지역</b> ${esc(regionText)}</div><div><b>접수</b> ${periodText(j.applyStart,j.applyEnd)}</div><div><b>채용</b> ${periodText(j.workStart,j.workEnd)}</div><div><b>등록</b> ${fmt(j.registered)}</div></div>${j.subject?`<div class="subject">과목·직무　${esc(j.subject)}</div>`:''}<div class="source">출처 · ${esc(j.source||'채용 게시판')}${privateJob&&j.sourceSurfaceLabel?' · '+esc(j.sourceSurfaceLabel):''}</div></div><div class="go ${href?'':'pending'}">${goText}</div></div></${tag}>`;
  };

  stats=function(){
    const active=jobs.filter(j=>{const d=diffDay(j.applyEnd);return d===null||d>=0});
    document.getElementById('countAll').textContent=active.length.toLocaleString();
    document.getElementById('countSoon').textContent=active.filter(j=>{const d=diffDay(j.applyEnd);return d!==null&&d>=0&&d<=3}).length.toLocaleString();
    document.getElementById('countToday').textContent=active.filter(j=>isToday(j.registered)).length.toLocaleString();
    if(sourceData?.totalSourceCount)document.getElementById('countSources').textContent=Number(sourceData.totalSourceCount).toLocaleString();
  };

  renderSourceStatus=function(data){
    if(!data){document.getElementById('sourceStatus').style.display='none';return}
    const make=(key,label)=>{const group=(data.sources||{})[key]||{},central=group.central,offices=group.supportOffices||[];let html=`<div class="source-col"><h4>${label}</h4>`;if(central)html+=`<div class="source-line"><span>${esc(central.name)}</span><span class="${central.ok?'ok':'warn'}">${central.ok?central.count+'건':'오류'}</span></div>`;offices.forEach(s=>html+=`<div class="source-line"><span>${esc(s.name)}</span><span class="${s.ok?'ok':'warn'}">${s.ok?s.count+'건':'오류'}</span></div>`);return html+'</div>'};
    let html=make('gyeonggi','경기 · 교육청 + 25개 교육지원청')+make('seoul','서울 · 일자리포털 + 11개 교육지원청');
    const li=data.privateSources?.lessoninfo;
    if(li){const ok=li.ok===true;const s=li.surfaces||{};html+=`<div class="source-col"><h4>민간 구인 · 레슨인포</h4><div class="source-line"><span>서울·경기 현재 구인</span><span class="${ok?'ok':'warn'}">${Number(li.count||0).toLocaleString()}건</span></div><div class="source-line"><span>방과후·늘봄</span><span>${Number(s['afterschool-nulbom']||0).toLocaleString()}건</span></div><div class="source-line"><span>문화예술 채용</span><span>${Number(s['culture-arts']||0).toLocaleString()}건</span></div><div class="source-line"><span>안정 ID 누락</span><span class="${Number(li.missingAfterCount||0)===0?'ok':'warn'}">${Number(li.missingAfterCount||0)}건</span></div></div>`}
    document.getElementById('sourceGrid').innerHTML=html;
  };

  document.getElementById('resetBtn')?.addEventListener('click',()=>{state.sources.clear();state.categories.clear();document.querySelectorAll('#sourceKindFilters input,#categoryFilters input').forEach(i=>i.checked=false);render()});

  const wait=()=>{if(typeof jobs!=='undefined'&&jobs.length){stats();renderSourceStatus(sourceData);render();return}setTimeout(wait,150)};wait();
})();
