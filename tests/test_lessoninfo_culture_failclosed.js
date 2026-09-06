const fs = require('fs');
const vm = require('vm');

const guard = fs.readFileSync('direct-link-guard.js', 'utf8');
const sandbox = {
  location: { href: 'https://puzzlex5.github.io/gg-edujob/' },
  URL,
  postingLink: j => j.url || 'fallback',
};
vm.createContext(sandbox);
vm.runInContext(guard, sandbox);

const culture = {
  source: '레슨인포',
  sourceSurface: 'culture-arts',
  sourceIdentity: 'culture:id:94673',
  url: 'https://www.lessoninfo.co.kr/culture-jobs/detail.php?id=94673',
};
if (sandbox.postingLink(culture) !== '') {
  throw new Error('Lessoninfo culture cold-link must fail closed');
}

const afterschool = {
  source: '레슨인포',
  sourceSurface: 'afterschool-nulbom',
  url: 'https://www.lessoninfo.co.kr/board/board.php?bo_table=20170316171033_6494&wr_no=12345',
};
if (sandbox.postingLink(afterschool) !== afterschool.url) {
  throw new Error('Lessoninfo afterschool control route must remain unchanged');
}

const explicitUnverified = { detailLinkVerified: false, url: 'https://example.com/wrong' };
if (sandbox.postingLink(explicitUnverified) !== '') {
  throw new Error('existing fail-close contract must remain intact');
}

console.log('Lessoninfo culture fail-close regression passed');
