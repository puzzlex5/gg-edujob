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
    // Any row explicitly rejected by an exact-detail audit remains non-clickable.
    if(j&&j.detailLinkVerified===false) return '';

    // Lessoninfo culture links are authorized per posting by the independent cold-browser
    // verifier. Never infer safety merely from a detail.php?id=... URL shape. Require the
    // explicit true flag and the exact verified destination produced by that verifier.
    if(j&&j.source==='레슨인포'&&j.sourceSurface==='culture-arts'){
      // Defense in depth: this real-device regression sentinel must never become clickable even
      // if stale data accidentally carries detailLinkVerified=true.
      if(j.sourceIdentity==='culture:id:94673') return '';
      if(j.detailLinkVerified!==true) return '';
      const verified=String(j.verifiedUrl||'').trim();
      return verified;
    }

    const direct=seoulSupportGet(j);
    if(direct) return direct;
    return basePostingLink(j);
  };
})();
