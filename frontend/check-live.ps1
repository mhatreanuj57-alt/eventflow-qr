# Requires agent-browser. Supply organizer password through EF_QA_PASSWORD (never committed).
param([string]$BaseUrl = 'https://eventflow-qr-preview.vercel.app', [string]$OrganizerEmail = 'anumhatre1803@gmail.com')
$ErrorActionPreference = 'Stop'
if (!$env:EF_QA_PASSWORD) { throw 'Set EF_QA_PASSWORD for this check.' }
function Browser {
    & agent-browser --session eventflow-live-check @args
    if ($LASTEXITCODE -ne 0) { throw 'Live browser check failed.' }
}
try {
    Browser --headed false open "$BaseUrl/#scanner"
    Browser wait 'input[name=password]'
    Browser eval "if(document.querySelector('video')) throw Error('Scanner exposed before login'); location.hash='register'"
    Browser set viewport 390 844
    Browser wait 'input[name=name]'
    Browser fill 'input[name=name]' 'Mobile Browser Demo (fictional)'
    Browser fill 'input[name=email]' "browser-$([Guid]::NewGuid().ToString('N').Substring(0,10))@example.test"
    Browser click 'input[value=Professional]'
    Browser fill 'input[name=organization]' 'Fictional Demo Studio'
    Browser fill 'input[name=githubUrl]' 'https://github.com/demo'
    Browser fill 'input[name=linkedinUrl]' 'https://www.linkedin.com/in/demo'
    Browser scrollintoview 'button[type=submit]'
    # Submit through the native form API: the CLI's mobile click coordinates can miss after scrolling.
    Browser eval "document.querySelector('form').requestSubmit()"
    Browser wait '.qr-wrap img'
    Browser eval "window.passId=document.querySelector('.registration-id').textContent; window.passQr=document.querySelector('.qr-wrap img').src; if(passId==='EF-PREVIEW') throw Error('Fake pass'); for(const text of ['Professional','Solo attendee','https://github.com/demo','https://www.linkedin.com/in/demo']) if(!document.querySelector('.pass-details').textContent.includes(text)) throw Error('Detail missing'); if(document.documentElement.scrollWidth>innerWidth) throw Error('Pass overflow'); window.exportPng=''; window.oldClick=HTMLAnchorElement.prototype.click; HTMLAnchorElement.prototype.click=function(){window.exportPng=this.href};"
    Browser click '.pass-intro button'
    Browser wait --fn "window.exportPng.startsWith('data:image/png;base64,iVBOR')"
    Browser eval "HTMLAnchorElement.prototype.click=oldClick; location.hash='login'"
    Browser wait 'input[name=password]'
    Browser fill 'input[name=email]' $OrganizerEmail
    Browser fill 'input[name=password]' $env:EF_QA_PASSWORD
    Browser click '.login-panel button'
    Browser wait '.stats'
    Browser wait 'tbody tr'
    Browser eval "if(document.documentElement.scrollWidth>innerWidth) throw Error('Dashboard overflow'); location.hash='scanner'"
    Browser wait '.camera-controls'
    Browser eval "(async()=>{window.testCanvas=document.createElement('canvas'); testCanvas.width=640; testCanvas.height=640; const image=new Image(); image.src=passQr; await image.decode(); const ctx=testCanvas.getContext('2d'); ctx.fillStyle='#fff'; ctx.fillRect(0,0,640,640); navigator.mediaDevices.getUserMedia=async()=>{window.testStream=testCanvas.captureStream(10); window.cameraInterval=setInterval(()=>ctx.drawImage(image,40,40,560,560),100); testStream.getTracks()[0].addEventListener('ended',()=>clearInterval(cameraInterval)); return testStream};})()"
    Browser eval "document.querySelector('.camera-controls .primary').click()"
    Browser wait '.result.success'
    Browser eval "if(!document.querySelector('.result h3').textContent.includes('Mobile Browser Demo')) throw Error('Camera decoded wrong attendee'); if(testStream.getTracks().some(t=>t.readyState!=='ended')) throw Error('Camera failed to pause'); if(!document.querySelector('.manual-form input').disabled) throw Error('Scan not paused'); window.firstTime=document.querySelector('.result p').textContent;"
    Browser eval "document.querySelector('.scan-feedback > button').click()"
    Browser eval "document.querySelector('.manual-form input').focus()"
    $passId = (& agent-browser --session eventflow-live-check eval 'window.passId') -replace '"',''
    if ($LASTEXITCODE -ne 0) { throw 'Could not read registration ID.' }
    Browser fill '.manual-form input' $passId.Trim()
    Browser eval "document.querySelector('.manual-form button').click()"
    Browser wait '.result.duplicate'
    Browser eval "if(!document.querySelector('.result p').textContent.includes(firstTime.replace(/^[^0-9]+/,''))) throw Error('Original timestamp changed')"
    Browser eval "document.querySelector('.scan-feedback > button').click()"
    Browser fill '.manual-form input' 'invalid-pass'
    Browser eval "document.querySelector('.manual-form button').click()"
    Browser wait '.result.invalid'
    Browser eval "location.hash='dashboard'"
    Browser wait 'tbody tr'
    Browser fill 'input[type=search]' $passId.Trim()
    Browser eval "if(document.querySelectorAll('tbody tr').length!==1) throw Error('Search failed')"
    Browser eval "document.querySelector('tbody button').click()"
    Browser eval "if(!document.querySelector('.recovery .primary').disabled) throw Error('Identity confirmation missing')"
    Browser check '.recovery input'
    Browser eval "document.querySelector('.recovery .primary').click()"
    Browser wait '.qr-wrap img'
    Browser eval "if(document.querySelector('.registration-id').textContent!==passId || document.querySelector('.qr-wrap img').src!==passQr) throw Error('Recovery changed pass')"
    Browser set media reduced-motion
    Browser eval "if(!matchMedia('(prefers-reduced-motion:reduce)').matches) throw Error('Reduced motion not enabled'); location.hash='dashboard'"
    Browser wait '.stats'
    Browser click 'nav button'
    Browser wait 'input[name=password]'
    Write-Output 'PASS: public registration, full ticket download, organizer login/logout, real QR decoder with generated camera stream, pause, manual duplicate/invalid, mobile dashboard, search and unchanged recovery. Physical phone camera still needs a two-device check.'
} finally { Browser close }
