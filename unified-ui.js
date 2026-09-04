(()=>{
  if(typeof state==='undefined'||typeof filtered!=='function'||typeof card!=='function') return;
  state.sources=state.sources||new Set();
  state.categories=state.categories||new Set();
  const SOURCE_VALUES=['공식','민간'];
  const CATEGORY_VALUES=['방과후·늘봄','음악·예체능','문화예술','학원강사','강사·교사','기타 민간구인'];
  const privateAliases=j=>(j.alsoSeenOn||[]).filter(x=>x&&x.source);
  const hasPrivateAlias=j=>privateAliases(j).length>0;
  const sourceKinds=j=>new Set([...(j.feedKind==='official'?['공식']:['민간']),...(hasPrivateAlias(j)?['민간']:[])]);
  const categorySet=j=>new Set(Array.isArray(j.categories)?j.categories:[]);
  const jobProvincesLocal=j=>{const a=Array.isArray(j.provinces)?j.provinces.filter(Boolean):[];return a.length?[...new Set(a)]:[province(j)]};
  const provinceLabel=j=>jobProvincesLocal(j).join('·')||province(j);

  function addFilterSection(id,title,values,key){
    const panel=document.querySelector('.filter-panel'),selected=document.getElementById('selectedBox');
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
  filtered=function(){return baseFiltered().filter(j=>{
    if(state.sources.size){const kinds=sourceKinds(j);if(![...state.sources].some(k=>kinds.has(k)))return false}
    if(state.categories.size){const cs=categorySet(j);if(![...state.categories].some(c=>cs.has(c)))return false}
    return true;
  })};

  selectionSummary=function(){const parts=[];if(state.provinces.size)parts.push(`시·도 ${[...state.provinces].join(', ')}`);if(state.regions.size)parts.push(`지역 ${state.regions.size}곳`);if(state.schools.size)parts.push(`학교급 ${[...state.schools].join(', ')}`);if(state.types.size)parts.push(`직종 ${[...state.types].join(', ')}`);if(state.sources.size)parts.push(`공고 ${[...state.sources].join('·')}`);if(state.categories.size)parts.push(`분야 ${[...state.categories].join('·')}`);const box=document.getElementById('selectedBox');if(parts.length){box.className='selected-box show';box.innerHTML='<b>선택된 조건</b><br>'+parts.map(esc).join(' · ')}else{box.className='selected-box';box.textContent=''}};

  card=function(j){const d=diffDay(j.applyEnd),today=isToday(j.registered),prov=provinceLabel(j),lvl=schoolLevel(j),typ=jobType(j);const dBadge=d===0?'오늘 마감':d!==null&&d>0&&d<=7?`D-${d}`:'';const regionText=j.location||j.region||((j.regions||[]).join('·'))||prov;const href=postingLink(j),tag=href?'a':'div',linkAttrs=href?` href="${esc(href)}" target="_blank" rel="noopener"`:'';const privateJob=j.feedKind==='private';const trust=privateJob?`민간 · ${esc(j.source||j.sourceSurfaceLabel||'민간 구인')}`:`공식 · ${esc(j.source||'공식 채용 게시판')}`;const trustStyle=privateJob?'background:#fff1dd;color:#9a5a00':'background:#eafaf0;color:#087a42';const surface=privateJob&&j.sourceSurfaceLabel&&j.sourceSurfaceLabel!==j.source?`<span class="badge">${esc(j.sourceSurfaceLabel)}</span>`:'';const aliases=privateAliases(j);const seen=aliases.length?`<span class="badge" style="background:#eef4ff;color:#295e9b">${esc(aliases[0].source)}에서도 확인됨</span>`:'';const cats=(j.categories||[]).slice(0,2).map(c=>`<span class="badge type">${esc(c)}</span>`).join('');const goText=href?'원문 공고 바로가기 ↗':'원문 링크 점검 중';return `<${tag} class="job"${linkAttrs}><div class="jobtop"><div style="min-width:0;flex:1"><div class="badges">${dBadge?`<span class="badge d">${dBadge}</span>`:''}${today?'<span class="badge today">NEW 오늘</span>':''}<span class="badge" style="${trustStyle}">${trust}</span><span class="badge prov ${prov==='서울'?'seoul':''}">${esc(prov)}</span>${surface}${cats}${seen}</div><h2>${esc(j.title||'채용 공고')}</h2><div class="school">${esc(j.school||'기관명 확인')}</div><div class="meta"><div><b>지역</b> ${esc(regionText)}</div><div><b>접수</b> ${periodText(j.applyStart,j.applyEnd)}</div><div><b>채용</b> ${periodText(j.workStart,j.workEnd)}</div><div><b>등록</b> ${fmt(j.registered)}</div></div>${j.subject?`<div class="subject">과목·직무　${esc(j.subject)}</div>`:''}<div class="source">출처 · ${esc(j.source||'채용 게시판')}${privateJob&&j.sourceSurfaceLabel&&j.sourceSurfaceLabel!==j.source?' · '+esc(j.sourceSurfaceLabel):''}${aliases.length?' · 교차확인':''}</div></div><div class="go ${href?'':'pending'}">${goText}</div></div></${tag}>`};

  stats=function(){
    const activeAll=jobs.filter(j=>{const d=diffDay(j.applyEnd);return d===null||d>=0});
    const active=scope==='official'?activeAll.filter(j=>sourceKinds(j).has('공식')):scope==='private'?activeAll.filter(j=>sourceKinds(j).has('민간')):activeAll;
    document.getElementById('countAll').textContent=active.length.toLocaleString();
    document.getElementById('countSoon').textContent=active.filter(j=>{const d=diffDay(j.applyEnd);return d!==null&&d>=0&&d<=3}).length.toLocaleString();
    document.getElementById('countToday').textContent=active.filter(j=>isToday(j.registered)).length.toLocaleString();
    const enabledPrivate=Object.values(sourceData?.privateSources||{}).filter(v=>v&&v.publicationEnabled===true).length;
    const sourceCount=scope==='official'?Number(sourceData?.officialSourceCount||38):scope==='private'?enabledPrivate:Number(sourceData?.totalSourceCount||sourceData?.officialSourceCount||38+enabledPrivate);
    document.getElementById('countSources').textContent=sourceCount.toLocaleString();
    const label=document.getElementById('countSources')?.parentElement?.querySelector('.t');
    if(label) label.textContent=scope==='official'?'공식 수집 출처':scope==='private'?'민간 수집 출처':'수집 출처';
  };

  function renderOverlapNote(data){
    const privateMeta=Object.values(data?.privateSources||{});
    const n=privateMeta.reduce((sum,x)=>sum+Number(x?.representedByOfficial||0),0);
    let note=document.getElementById('overlapNote');
    const toolbar=document.querySelector('.toolbar');
    if(!toolbar||!n){note?.remove();return}
    if(!note){note=document.createElement('div');note.id='overlapNote';note.style.cssText='margin:0 2px 10px;padding:9px 11px;border:1px solid #dbe7f7;background:#f4f8ff;border-radius:10px;color:#50647f;font-size:11px;line-height:1.55';toolbar.insertAdjacentElement('afterend',note)}
    if(scope==='private') note.innerHTML=`민간에서 확인된 공고 중 <b>${n.toLocaleString()}건</b>은 동일한 공식 원문이 확인되어 공식 공고를 대표로 표시합니다.`;
    else if(scope==='official') note.innerHTML=`공식 공고 중 <b>${n.toLocaleString()}건</b>은 민간 출처에서도 같은 공식 원문이 확인되었습니다.`;
    else note.innerHTML=`공식·민간 양쪽에서 확인된 <b>${n.toLocaleString()}건</b>은 같은 공고이므로 전체 구인에서는 한 번만 집계합니다.`;
  }

  renderSourceStatus=function(data){
    if(!data){document.getElementById('sourceStatus').style.display='none';return}
    const make=(key,label)=>{const group=(data.sources||{})[key]||{},central=group.central,offices=group.supportOffices||[];let html=`<div class="source-col"><h4>${label}</h4>`;if(central)html+=`<div class="source-line"><span>${esc(central.name)}</span><span class="${central.ok?'ok':'warn'}">${central.ok?central.count+'건':'오류'}</span></div>`;offices.forEach(s=>html+=`<div class="source-line"><span>${esc(s.name)}</span><span class="${s.ok?'ok':'warn'}">${s.ok?s.count+'건':'오류'}</span></div>`);return html+'</div>'};
    let html=make('gyeonggi','경기 · 교육청 + 25개 교육지원청')+make('seoul','서울 · 일자리포털 + 11개 교육지원청');
    Object.entries(data.privateSources||{}).forEach(([key,meta])=>{if(!meta)return;const enabled=meta.publicationEnabled===true,ok=enabled&&meta.ok===true,count=Number(meta.count||0),missing=Number(meta.missingAfterCount||0),detail=Number(meta.detailErrorCount||0);html+=`<div class="source-col"><h4>민간 구인 · ${esc(meta.name||key)}</h4><div class="source-line"><span>서울·경기 현재 구인</span><span class="${ok?'ok':'warn'}">${enabled?count.toLocaleString()+'건':'검증 대기'}</span></div><div class="source-line"><span>안정 ID 누락</span><span class="${missing===0?'ok':'warn'}">${missing}건</span></div><div class="source-line"><span>원문 링크 오류</span><span class="${detail===0?'ok':'warn'}">${detail}건</span></div></div>`});
    document.getElementById('sourceGrid').innerHTML=html;renderOverlapNote(data)
  };

  document.getElementById('resetBtn')?.addEventListener('click',()=>{state.sources.clear();state.categories.clear();document.querySelectorAll('#sourceKindFilters input,#categoryFilters input').forEach(i=>i.checked=false);render()});
  const wait=()=>{if(typeof jobs!=='undefined'&&jobs.length){stats();renderSourceStatus(sourceData);render();return}setTimeout(wait,150)};wait();
})();
