(()=>{
  if(typeof postingLink!=='function') return;
  const basePostingLink=postingLink;

  // Seoul support-office JOV11 details accept a persistent GET with job_seq.
  // Prefer that browser-safe exact URL over the legacy POST intermediary, which can
  // stall in mobile in-app browsers even though the server-side POST itself is valid.
  const seoulSupportGet=j=>{
    if(!j) return '';
    const paramSeq=String((j.openParams||{}).job_seq||'');
    for(const raw of [j.url,j.openUrl]){
      if(!raw) continue;
      try{
        const u=new URL(String(raw),location.href);
        const seq=String(u.searchParams.get('job_seq')||paramSeq);
        const host=(u.hostname||'').toLowerCase();
        if(
          u.protocol==='https:' &&
          host.endsWith('.sen.go.kr') &&
          u.pathname==='/FUS/JO/JOV11.do' &&
          /^\d+$/.test(seq)
        ){
          u.searchParams.set('job_seq',seq);
          return u.href;
        }
      }catch(e){}
    }
    return '';
  };

  postingLink=function(j){
    // Any row explicitly rejected by the exact-detail audit remains non-clickable.
    if(j&&j.detailLinkVerified===false) return '';

    // Culture-arts used to be blanket-blocked even after the unified exact-detail
    // audit had verified a concrete /culture-jobs/detail.php?id=... destination.
    // Fail closed only for unverified rows; verified exact detail rows must remain
    // clickable. This keeps broken/generic destinations blocked without discarding
    // real culture-foundation postings.
    if(
      j&&j.source==='레슨인포'&&j.sourceSurface==='culture-arts'&&
      j.detailLinkVerified!==true
    ) return '';

    const direct=seoulSupportGet(j);
    if(direct) return direct;
    return basePostingLink(j);
  };
})();
