const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs'); const assert=require('node:assert/strict'); fs.mkdirSync('dev/evidence',{recursive:true});
(async()=>{
const b=await chromium.launch({...(process.env.CHROMIUM?{executablePath:process.env.CHROMIUM}:{}),headless:true,args:['--no-sandbox','--enable-unsafe-swiftshader']});
const p=await b.newPage({viewport:{width:1672,height:941}});const errors=[];p.on('pageerror',e=>errors.push(e.message));p.on('dialog',d=>d.accept());
async function shot(n){await p.waitForTimeout(500);await p.screenshot({path:'dev/evidence/'+n+'.png'});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,n+' overflows');console.log('PASS',n);}
await p.goto('http://127.0.0.1:4173/autopilot/');await p.locator('#homeIn').waitFor();await p.waitForTimeout(500);
for(const theme of ['dark','light']) {await p.evaluate(t=>{document.cookie=`vellur_theme=${t}; path=/autopilot`;document.documentElement.dataset.theme=t},theme);await shot('workspace-'+theme);}
await p.locator('#tabs [data-view="chat"]').click();await p.locator('#convSel').evaluate(e=>{e.value='1';e.dispatchEvent(new Event('change'))});await p.locator('#conversationWork').waitFor();await p.locator('#msgs').evaluate(e=>e.scrollTop=e.scrollHeight);await shot('conversation-completed');
await p.locator('#convNew').click();await p.locator('#chatMode').selectOption('work');await p.locator('#chatIn').fill('Demo task: improve an empty workspace');await p.locator('#btnSend').click();
await p.locator('#conversationWork').waitFor({timeout:10000});await shot('conversation-running');
console.log('work',await p.locator('#conversationWork').innerText());await p.waitForTimeout(10000);await shot('conversation-finished');console.log('finished',await p.locator('#conversationWork').innerText());
for(const m of ['build','review','ship']){await p.locator('#stModes [data-mode="'+m+'"]').click();await p.waitForTimeout(1500);await shot('stage-'+m);}
assert.match(await p.locator('#shBody').innerText(),/Changes saved[\s\S]*Nothing is on GitHub yet|nothing to release/);
await p.locator('#stModes [data-mode="discover"]').click();
await p.reload();await p.locator('#conversationWork').waitFor();console.log('restored',await p.locator('#chatTitle').innerText());
await p.locator('#chatMode').selectOption('chat');await p.locator('#chatIn').fill('Explain the result simply.');await p.locator('#btnSend').click();await p.getByText('This is the staging preview answer.',{exact:false}).waitFor();await shot('conversation-discuss');
for(const v of ['models','costs','tools','agent']){await p.locator('#tabs [data-view="'+v+'"]').click();await p.waitForTimeout(500);await shot(v);}
await p.setViewportSize({width:390,height:844});await p.evaluate(()=>document.querySelector('#tabs [data-view="chat"]').click());await p.locator('#chatIn').waitFor();await shot('conversation-mobile');
await p.locator('#convSel').evaluate(e=>{e.value='1';e.dispatchEvent(new Event('change'))});await p.locator('#modeBar').waitFor();
for(const m of ['review','ship','discover']){await p.locator('#stModes [data-mode="'+m+'"]').click();await p.waitForTimeout(900);await shot('mobile-'+m);}
await p.setViewportSize({width:820,height:1180});await shot('conversation-tablet');
await p.setViewportSize({width:1672,height:941});
await p.locator('#convNew').click();await p.locator('#chatMode').selectOption('work');await p.locator('#workRepo').fill('demo/unavailable');await p.locator('#chatIn').fill('Demo failure: repository unavailable');await p.locator('#btnSend').click();await p.locator('#conversationWork').waitFor();await p.waitForTimeout(4000);await shot('conversation-failed');assert.match(await p.locator('#conversationWork').innerText(),/Failed/);
await p.locator('[data-open-work]').click();await p.locator('#pvToggle').click();await p.waitForTimeout(700);await shot('preview-unavailable');
await p.goto('http://127.0.0.1:4173/autopilot/3d/');await p.waitForTimeout(1200);await shot('studio3d-light');
assert.deepEqual(errors,[]);await b.close();})().catch(e=>{console.error(e);process.exit(1)});
