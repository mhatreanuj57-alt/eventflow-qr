$ErrorActionPreference = 'Stop'
& agent-browser --session eventflow-final-pass open https://eventflow-qr.vercel.app
$script = @'
(async()=>{
  const now=Date.now(),iso=offset=>new Date(now+offset).toISOString();
  const event={id:'e'.repeat(32),name:'Builders Breakout',host:'Fictional QA host',venue:'QA venue',description:'',start:iso(86400000),end:iso(100800000),registrationOpen:iso(-3600000),registrationClose:iso(3600000),color:'#ff5100',layout:'editorial',mark:'EF',logo:'',demo:true};
  const original=window.fetch;let attachment=false;
  const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
  window.fetch=async(url,options={})=>{
    const path=String(url);
    if(!path.startsWith('https://7ctg987wi2.execute-api.ap-southeast-2.amazonaws.com/platform/'))return original(url,options);
    if(path.endsWith('/events'))return response({events:[event],serverTime:new Date().toISOString()});
    if(path.endsWith('/events/'+event.id))return response(event);
    if(path.endsWith('/registrations'))return response({...JSON.parse(options.body),id:'EF-AAAAAAAAAA',token:'opaque-fictional-qa-token-for-visual-check'},201);
    if(path.endsWith('/email')){attachment=JSON.parse(options.body).png.startsWith('iVBOR');return response({message:'QA attachment captured; no email sent.'});}
    throw Error('Unexpected API call blocked; no AWS data changed');
  };
  const wait=async fn=>{for(let n=0;n<200;n++){if(fn())return;await new Promise(r=>setTimeout(r,100));}throw Error('Timed out waiting for pass');};
  location.hash='event/'+event.id;
  await wait(()=>document.querySelector('[name="organization"]'));
  if(/demo/i.test(document.body.innerText))throw Error('Demo registration wording remains');
  for(const [name,value] of [['name','Fictional QA Attendee'],['email','qa-only@example.test'],['organization','QA Studio']])document.querySelector('[name="'+name+'"]').value=value;
  document.querySelector('form').requestSubmit();
  await wait(()=>attachment&&document.querySelector('.ticket-export .qr-wrap img'));
  const ticket=document.querySelector('.ticket-export').innerText;
  if(/demo|preview/i.test(ticket)||!ticket.includes('EVENT PASS'))throw Error('Production pass label is incorrect');
  if(!ticket.includes('Fictional QA Attendee')||!ticket.includes('EF-AAAAAAAAAA'))throw Error('Pass details missing');
  return 'PASS: deployed registration and issued/downloadable ticket contain no demo or preview labels, including legacy demo=true fixture. Full PNG generated. No AWS writes or emails.';
})()
'@
$encoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script))
& agent-browser --session eventflow-final-pass eval --base64 $encoded
if ($LASTEXITCODE -ne 0) { throw 'Production pass check failed' }
