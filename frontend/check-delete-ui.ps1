$ErrorActionPreference = 'Stop'
& agent-browser --session eventflow-delete-ui open http://localhost:4174/#host
if ($LASTEXITCODE -ne 0) { throw 'Could not open local UI' }
$script = @'
(async()=>{
  const event={id:'f'.repeat(32),name:'Fictional Delete UI',host:'QA only',venue:'QA venue',description:'',start:'2026-10-06T10:00:00+05:30',end:'2026-10-06T17:00:00+05:30',registrationOpen:'2026-10-01T10:00:00+05:30',registrationClose:'2026-10-05T18:00:00+05:30',color:'#ff5100',layout:'editorial',mark:'QA',logo:'',owner:'ui-only@example.test'};
  const original=window.fetch;let deleted=false,calls=0;
  const response=value=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
  window.fetch=async(url,options={})=>{
    const path=String(url);
    if(!path.startsWith('/api/platform/'))return original(url,options);
    if(path==='/api/platform/login')return response({token:'fictional-browser-only-token',expiresAt:Date.now()/1000+3600,organizer:{email:event.owner}});
    if(path==='/api/platform/events')return response({events:deleted?[]:[event],serverTime:new Date().toISOString()});
    if(path==='/api/platform/organizer/events')return response({events:deleted?[]:[event]});
    if(path.endsWith('/delete')){const body=JSON.parse(options.body);if(body.confirmation!==event.name)throw Error('Confirmation payload mismatch');calls++;deleted=calls===2;return response({status:deleted?'deleted':'deleting',id:event.id});}
    throw Error('Unexpected API request intercepted; no AWS mutation made');
  };
  const wait=async fn=>{for(let n=0;n<200;n++){if(fn())return;await new Promise(r=>setTimeout(r,100));}throw Error('Timed out waiting for UI');};
  [...document.querySelectorAll('button')].find(b=>b.textContent.includes('Already an organizer')).click();
  await wait(()=>document.querySelector('input[autocomplete="current-password"]'));
  document.querySelector('[name="email"]').value=event.owner;
  document.querySelector('[name="password"]').value='fictional-ui-password';
  document.querySelector('form').requestSubmit();
  await wait(()=>location.hash==='#workspace'&&document.body.innerText.includes(event.name));
  const open=()=>[...document.querySelectorAll('button')].find(b=>b.textContent==='Delete event').click();
  open();await wait(()=>document.querySelector('dialog').open);
  const dialog=document.querySelector('dialog');
  if(!dialog.querySelector('.primary').disabled)throw Error('Unconfirmed delete enabled');
  [...dialog.querySelectorAll('button')].find(b=>b.textContent==='Cancel').click();
  if(dialog.open||calls)throw Error('Cancel mutated event');
  open();
  const input=dialog.querySelector('input');
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'wrong name');input.dispatchEvent(new Event('input',{bubbles:true}));
  await new Promise(r=>setTimeout(r,100));
  if(!dialog.querySelector('.primary').disabled)throw Error('Wrong name enabled deletion');
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,event.name);input.dispatchEvent(new Event('input',{bubbles:true}));
  await wait(()=>!dialog.querySelector('.primary').disabled);
  dialog.querySelector('form').requestSubmit();
  await wait(()=>document.querySelector('[role="alert"]')?.textContent.includes('Deleted Fictional Delete UI'));
  if(calls!==2||document.querySelector('dialog')||[...document.querySelectorAll('h3')].some(n=>n.textContent===event.name))throw Error('Pending cleanup did not finish');
  return 'PASS: modal confirmation, cancel, wrong-name guard, resumed deletion and removed event card. All API writes mocked; no AWS data changed.';
})()
'@
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script))
& agent-browser --session eventflow-delete-ui eval --base64 $encoded
if ($LASTEXITCODE -ne 0) { throw 'Deletion UI verification failed' }
