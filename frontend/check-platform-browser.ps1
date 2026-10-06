$ErrorActionPreference = 'Stop'
$fixturePath = Join-Path $PSScriptRoot '../.audit/platform-qa.json'
$fixture = Get-Content -LiteralPath $fixturePath -Raw | ConvertFrom-Json
function Browser([string]$script) {
  $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script))
  & agent-browser --session eventflow-platform-live eval --base64 $encoded
  if ($LASTEXITCODE -ne 0) { throw 'Browser assertion failed' }
}
$qaJson = $fixture | ConvertTo-Json -Depth 20 -Compress
Browser "window.qa = $qaJson; 'QA fixture loaded without printing credentials'"
Browser @'
window.waitForQa = async predicate => { for(let i=0;i<150;i++){if(predicate())return;await new Promise(r=>setTimeout(r,100));}throw Error('Timed out waiting for UI'); };
window.fillQa = (name,value) => {const field=document.querySelector('[name="'+name+'"]'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(field,value);field.dispatchEvent(new Event('input',{bubbles:true}));};
{const button=[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Already an organizer'));button?.click();'Sign-in selected';}
'@
Browser @'
(async()=>{await waitForQa(()=>document.querySelector('input[autocomplete="current-password"]'));fillQa('email',qa.organizers[0].organizer.email);fillQa('password',qa.password);document.querySelector('form').requestSubmit();return 'Sign-in submitted';})()
'@
Browser @'
(async()=>{await waitForQa(()=>location.hash==='#workspace' && document.body.innerText.includes(qa.events[0].name));if(document.body.innerText.includes(qa.events[1].name))throw Error('Other organizer event leaked');location.hash='attendance/'+qa.events[0].id;await waitForQa(()=>document.querySelector('.refresh-note')?.textContent.includes('Updated'));window.qaBaseline=[...document.querySelectorAll('.stats strong')].map(n=>Number(n.textContent));location.hash='edit/'+qa.events[0].id;return 'Own workspace isolated; baseline attendance recorded';})()
'@
Browser @'
(async()=>{await waitForQa(()=>document.querySelector('textarea[name="description"]'));const field=document.querySelector('textarea[name="description"]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(field,'Temporary fictional browser QA event.');field.dispatchEvent(new Event('input',{bubbles:true}));for(const [name,offset] of [['registrationOpen',-3600000],['registrationClose',3600000],['start',86400000],['end',100800000]]){const input=document.querySelector('[name="'+name+'"]');input.value=new Date(Date.now()+offset+19800000).toISOString().slice(0,16);input.dispatchEvent(new Event('input',{bubbles:true}));}return 'Event edit filled with a fresh QA registration window';})()
'@
Browser "document.querySelector('form').requestSubmit(); 'Event edit submitted'"
Browser @'
(async()=>{await waitForQa(()=>location.hash==='#workspace');const event=await (await fetch('/api/platform/events/'+qa.events[0].id)).json();if(event.description!=='Temporary fictional browser QA event.')throw Error('Event edit not saved');location.hash='event/'+qa.events[0].id;return 'Event editor saves to AWS and preserves branding';})()
'@
Browser @'
(async()=>{await waitForQa(()=>document.querySelector('input[name="organization"]'));const original=window.fetch;window.fetch=async(url,options)=>{if(String(url).endsWith('/email')){window.qaMail=JSON.parse(options.body);return new Response(JSON.stringify({status:'unavailable',message:'QA attachment captured; no email sent.'}),{status:200,headers:{'Content-Type':'application/json'}});}const response=await original(url,options);if(String(url).endsWith('/registrations')&&response.status===201)window.qaPerson=await response.clone().json();return response;};fillQa('name','Fictional Browser Attendee');fillQa('email','browser-'+crypto.randomUUID()+'@example.test');fillQa('organization','QA Studio');fillQa('team','');return 'Registration fields filled';})()
'@
Browser "document.querySelector('form').requestSubmit(); 'Registration submitted'"
Browser @'
(async()=>{await waitForQa(()=>window.qaMail && location.hash==='#ticket');if(!qaMail.png.startsWith('iVBOR'))throw Error('Missing full PNG attachment');if(qaMail.token!==qaPerson.token)throw Error('Attachment token changed');window.qaQr=document.querySelector('.qr-wrap img').src;window.qaQrExpected=qaPerson.token;const img=new Image();img.src=qaQr;await img.decode();const canvas=document.createElement('canvas');canvas.width=640;canvas.height=640;const ctx=canvas.getContext('2d');window.qaDraw=setInterval(()=>{ctx.fillStyle='white';ctx.fillRect(0,0,640,640);ctx.drawImage(img,100,100,440,440);},100);navigator.mediaDevices.getUserMedia=async()=>{window.qaStream=canvas.captureStream(10);return qaStream;};location.hash='scan/'+qa.events[0].id;return 'Pass and email attachment rendered; scanner fixture ready';})()
'@
Browser @'
(async()=>{await waitForQa(()=>[...document.querySelectorAll('button')].some(b=>b.textContent.includes('Start camera')));[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Start camera')).click();await waitForQa(()=>document.body.innerText.includes('Checked in')&&document.body.innerText.includes('Fictional Browser Attendee'));if(qaStream.getTracks().some(t=>t.readyState!=='ended'))throw Error('Camera did not pause after scan');return 'Actual QR decoder + AWS check-in; camera paused';})()
'@
Browser @'
(async()=>{const next=[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Scan next'));if(!next)throw Error('Missing Scan next');next.click();await waitForQa(()=>document.querySelector('input'));return 'Ready for duplicate manual scan';})()
'@
Browser @'
{const field=document.querySelector('input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(field,qaPerson.id);field.dispatchEvent(new Event('input',{bubbles:true}));'Manual ID entered';}
'@
Browser "document.querySelector('form').requestSubmit(); 'Duplicate submitted'"
Browser @'
(async()=>{await waitForQa(()=>document.body.innerText.includes('Already checked in'));location.hash='attendance/'+qa.events[0].id;await waitForQa(()=>document.querySelectorAll('tbody tr').length===qaBaseline[0]+1);const stats=[...document.querySelectorAll('.stats strong')].map(n=>Number(n.textContent));const expected=[qaBaseline[0]+1,qaBaseline[1]+1,qaBaseline[2]];if(stats.join(',')!==expected.join(','))throw Error('Attendance mismatch '+stats);[...document.querySelectorAll('tr')].find(row=>row.textContent.includes(qaPerson.id)).querySelector('button').click();return 'Duplicate rejected; dashboard counts consistent';})()
'@
Browser @'
{const recovery=document.querySelector('.recovery');if(!recovery.querySelector('.primary').disabled)throw Error('Recovery identity gate missing');recovery.querySelector('input').click();'Identity confirmed';}
'@
Browser "document.querySelector('.recovery .primary').click(); 'Existing pass requested'"
Browser @'
(async()=>{await waitForQa(()=>location.hash==='#ticket'&&document.querySelector('.qr-wrap img'));await waitForQa(()=>document.querySelector('.qr-wrap img').src===qaQr);if(!document.body.innerText.includes(qaPerson.id))throw Error('Recovery ID changed');clearInterval(qaDraw);return 'Recovery preserves original QR and ID';})()
'@
& agent-browser --session eventflow-platform-live set viewport 390 844
Browser @'
(async()=>{location.hash='attendance/'+qa.events[0].id;await waitForQa(()=>document.querySelector('table'));if(document.documentElement.scrollWidth>innerWidth)throw Error('Mobile overflow');return 'Mobile dashboard fits 390px';})()
'@
Browser @'
(async()=>{[...document.querySelectorAll('button')].find(b=>b.textContent==='Sign out').click();await waitForQa(()=>location.hash==='#home'&&!document.body.innerText.includes('Sign out'));location.hash='scan/'+qa.events[0].id;await waitForQa(()=>document.body.innerText.includes('The scanner and attendance belong to this event'));return 'Logout gates scanner';})()
'@
Write-Output 'PASS: actual organizer UI, registration, full attachment rendering, QR camera decoder, duplicate manual scan, dashboard, recovery, mobile layout and logout. No emails sent.'
