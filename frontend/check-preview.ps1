# Run with the Vite server running. Requires the installed agent-browser CLI.
param([string]$BaseUrl = 'http://localhost:4173')
$ErrorActionPreference = 'Stop'
function Browser {
    & agent-browser --session eventflow-preview-check @args
    if ($LASTEXITCODE -ne 0) { throw 'Preview check failed.' }
}
try {
    Browser --headed false open "$BaseUrl/#register"
    Browser set viewport 390 844
    Browser eval "if(document.documentElement.scrollWidth>innerWidth) throw Error('Registration overflow'); if(!document.querySelector('[name=organization]').required) throw Error('College must be required');"
    Browser click 'input[value=Other]'
    Browser eval "if(document.querySelector('[name=organization]').required) throw Error('Other organization must be optional');"
    Browser fill 'input[name=name]' 'Morgan Demo'
    Browser fill 'input[name=email]' 'morgan@example.test'
    Browser eval "for(const [name,url] of [['githubUrl','https://github.com/morgan-demo'],['linkedinUrl','https://www.linkedin.com/in/morgan-demo']]){const field=document.querySelector('[name='+name+']'); if(field.required || !field.checkValidity()) throw Error('Profile must be optional'); for(const invalid of ['not-a-url','https://example.com/morgan','javascript:alert(1)']){field.value=invalid; if(field.checkValidity()) throw Error('Invalid profile accepted')}; field.value=url; if(!field.checkValidity()) throw Error('Valid profile rejected'); if(new FormData(document.querySelector('form')).get(name)!==url) throw Error('Profile not included in registration');}"
    Browser fill 'input[name=githubUrl]' 'https://github.com/morgan-demo'
    Browser fill 'input[name=linkedinUrl]' 'https://www.linkedin.com/in/morgan-demo'
    Browser scrollintoview 'button[type=submit]'
    Browser click 'button[type=submit]'
    Browser wait '.qr-wrap img'
    Browser eval "for(const value of ['morgan@example.test','Other','Solo attendee','https://github.com/morgan-demo','https://www.linkedin.com/in/morgan-demo']){if(!document.querySelector('.pass-details').textContent.includes(value)) throw Error('Pass detail missing: '+value)} window.previewPng=''; window.exportedSvg=''; window.originalClick=HTMLAnchorElement.prototype.click; HTMLAnchorElement.prototype.click=function(){window.previewPng=this.href}; window.originalSerialize=XMLSerializer.prototype.serializeToString; XMLSerializer.prototype.serializeToString=function(node){const svg=originalSerialize.call(this,node); window.exportedSvg=svg; return svg};"
    Browser click '.pass-intro button'
    Browser wait --fn "window.previewPng.startsWith('data:image/png;base64,iVBOR')"
    Browser eval "(async()=>{HTMLAnchorElement.prototype.click=originalClick; XMLSerializer.prototype.serializeToString=originalSerialize; for(const value of ['Morgan Demo','morgan@example.test','Solo attendee','https://github.com/morgan-demo','https://www.linkedin.com/in/morgan-demo','ticket-top','ticket-stub','pass-details']){if(!exportedSvg.includes(value)) throw Error('Exported ticket detail missing: '+value)} const image=new Image(); image.src=previewPng; await image.decode(); const node=document.querySelector('.ticket-export'); if(Math.abs(image.width-node.clientWidth*3)>3 || Math.abs(image.height-node.clientHeight*3)>3) throw Error('Ticket export size mismatch'); if(document.documentElement.scrollWidth>innerWidth) throw Error('Pass overflow');})()"
    Browser open "$BaseUrl/#scanner"
    # A generated video stream tests camera controls without opening a real camera.
    Browser eval "window.testCanvas=document.createElement('canvas'); testCanvas.width=320; testCanvas.height=240; window.testStreams=[]; navigator.mediaDevices.getUserMedia=async()=>{testCanvas.getContext('2d').fillRect(0,0,320,240); const stream=testCanvas.captureStream(10); testStreams.push(stream); return stream};"
    Browser click '.camera-controls .primary'
    Browser wait --text 'Camera live'
    Browser eval "if(!document.querySelector('video').srcObject) throw Error('Camera not attached');"
    Browser click '.camera-controls .secondary'
    Browser eval "if(testStreams[0].getTracks().some(t=>t.readyState!=='ended') || document.querySelector('video').srcObject) throw Error('Stop leaked camera'); navigator.mediaDevices.getUserMedia=async()=>{throw new DOMException('Denied','NotAllowedError')};"
    Browser click '.camera-controls .primary'
    Browser wait --text 'Camera permission was denied'
    Browser eval "navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>window.resolveCamera=resolve);"
    Browser click '.camera-controls .primary'
    Browser click '.camera-controls .secondary'
    Browser eval "window.lateStream=testCanvas.captureStream(10); resolveCamera(lateStream);"
    Browser eval "if(lateStream.getTracks().some(t=>t.readyState!=='ended')) throw Error('Canceled permission request leaked camera');"
    Browser fill '.manual-form input' 'EF-0005'
    Browser click '.manual-form button'
    Browser eval "if(!document.querySelector('.result.success') || !document.querySelector('.result h3').textContent.includes('Dev Patel') || !document.querySelector('.manual-form input').disabled) throw Error('Manual success/pause failed');"
    Browser click '.scan-feedback > button'
    Browser fill '.manual-form input' 'EF-0002'
    Browser click '.manual-form button'
    Browser eval "if(!document.querySelector('.result.duplicate') || !document.querySelector('.result p').textContent.includes('10:06 AM')) throw Error('Original check-in time lost');"
    Browser click '.scan-feedback > button'
    Browser fill '.manual-form input' 'invalid'
    Browser click '.manual-form button'
    Browser eval "if(!document.querySelector('.result.invalid')) throw Error('Invalid state failed');"
    Browser open "$BaseUrl/#dashboard"
    Browser fill 'input[type=search]' 'EF-0003'
    Browser eval "if(document.querySelectorAll('tbody tr').length!==1) throw Error('Search failed');"
    Browser click 'tbody button'
    Browser eval "if(!document.querySelector('.recovery .primary').disabled) throw Error('Identity confirmation missing');"
    Browser check '.recovery input'
    Browser click '.recovery .primary'
    Browser wait '.qr-wrap img'
    Browser eval "if(!document.querySelector('.registration-id').textContent.includes('EF-0003')) throw Error('Recovery changed ID');"
    foreach ($screen in @('register','pass','scanner','dashboard')) {
        Browser open "$BaseUrl/#$screen"
        Browser eval "if(document.documentElement.scrollWidth>innerWidth) throw Error('Mobile overflow');"
    }
    Browser set media reduced-motion
    Browser eval "if(getComputedStyle(document.querySelector('main>div')).animationName!=='none') throw Error('Reduced motion ignored');"
    Write-Output 'PASS: preview form, PNG generation, camera start/stop/denial/cancellation, scanner states/pause, search, recovery, mobile width and reduced motion.'
} finally { Browser close }
