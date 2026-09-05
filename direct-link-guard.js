(()=>{
  if(typeof postingLink!=='function') return;
  const basePostingLink=postingLink;
  postingLink=function(j){
    if(j&&j.detailLinkVerified===false) return '';
    return basePostingLink(j);
  };
})();
