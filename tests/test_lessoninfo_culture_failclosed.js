const fs = require('fs');
const vm = require('vm');
const guard = fs.readFileSync('direct-link-guard.js', 'utf8');
const sandbox = {location:{href:'https://puzzlex5.github.io/gg-edujob/'},URL,postingLink:j=>j.url||'fallback'};
vm.createContext(sandbox); vm.runInContext(guard,sandbox);

const verifiedCulture={source:'레슨인포',sourceSurface:'culture-arts',sourceIdentity:'culture:id:94673',detailLinkVerified:true,url:'https://www.lessoninfo.co.kr/culture-jobs/detail.php?id=94673'};
if(sandbox.postingLink(verifiedCulture)!==verifiedCulture.url) throw new Error('verified Lessoninfo culture exact detail must stay clickable');

const unverifiedCulture={source:'레슨인포',sourceSurface:'culture-arts',sourceIdentity:'culture:id:94674',url:'https://www.lessoninfo.co.kr/culture-jobs/detail.php?id=94674'};
if(sandbox.postingLink(unverifiedCulture)!=='') throw new Error('unverified Lessoninfo culture detail must fail closed');

const rejectedCulture={source:'레슨인포',sourceSurface:'culture-arts',sourceIdentity:'culture:id:94675',detailLinkVerified:false,url:'https://www.lessoninfo.co.kr/culture-jobs/detail.php?id=94675'};
if(sandbox.postingLink(rejectedCulture)!=='') throw new Error('rejected Lessoninfo culture detail must fail closed');

const afterschool={source:'레슨인포',sourceSurface:'afterschool-nulbom',url:'https://www.lessoninfo.co.kr/board/board.php?bo_table=20170316171033_6494&wr_no=12345'};
if(sandbox.postingLink(afterschool)!==afterschool.url) throw new Error('Lessoninfo afterschool control route changed');
if(sandbox.postingLink({detailLinkVerified:false,url:'https://example.com/wrong'})!=='') throw new Error('existing fail-close contract changed');
console.log('Lessoninfo culture verified-link regression passed');
