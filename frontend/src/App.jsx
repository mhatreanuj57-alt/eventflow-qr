import { useEffect, useRef, useState } from 'react';
import QRCode from 'qrcode';
import { toPng } from 'html-to-image';
import jsQR from 'jsqr';
import { api, setSession } from './api';

const screens = [['register', '01', 'Registration'], ['pass', '02', 'QR pass'], ['scanner', '03', 'Scanner'], ['dashboard', '04', 'Dashboard'], ['login', '', 'Organizer login']];
const event = { name: 'Builders Breakout', date: '06 October 2026', time: '10 AM–3 PM IST', venue: 'Architecture Building, CSMU', timezone: 'Asia/Kolkata' };
const currentPage = () => screens.some(([id]) => id === location.hash.slice(1)) ? location.hash.slice(1) : 'register';

function EventDetails() {
  return <dl className="event-details"><div><dt>When</dt><dd>{event.date}<br />{event.time}</dd></div><div><dt>Where</dt><dd>Architecture Building<br />CSMU</dd></div></dl>;
}

function Register({ onPass }) {
  const [type, setType] = useState('Student');
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  async function submit(e) {
    e.preventDefault(); if (busy) return;
    const data = Object.fromEntries(new FormData(e.currentTarget));
    setBusy(true); setError('');
    try { onPass(await api('/registrations', { method: 'POST', body: data }), true); }
    catch (error) { setError(error.message); }
    finally { setBusy(false); }
  }
  return <div className="registration-layout">
    <section className="editorial"><p className="eyebrow"><span className="square" /> Builders Breakout / 2026</p>
      <h1 className="hero-title"><span>EVENT</span><span className="flow">FLOW<span className="hero-arrow" aria-hidden="true">↗</span></span></h1>
      <div className="hero-caption"><span className="edition">06<br /><small>OCT</small></span><div><h2>Less paperwork.<br />More building.</h2><p>Your details. One QR. Ready for check-in.</p></div></div>
      <EventDetails /><p className="demo-note"><strong>Demo registration.</strong> Separate from the official organizer registration. This pass does not grant official hackathon admission.</p>
    </section>
    <section className="registration-form"><div className="section-index"><span>01 / REGISTER</span><span aria-hidden="true">↘</span></div><h2>Make it official<span className="orange">.</span><small>Your demo pass, that is.</small></h2>
      <form onSubmit={submit}>
        <label>Full name<input name="name" required maxLength={100} pattern=".*\S.*" autoComplete="name" placeholder="Your full name" /></label>
        <label>Email<input name="email" required maxLength={254} type="email" autoComplete="email" placeholder="you@example.com" /><small>One registration per email. We will attempt to email your pass as a PNG attachment.</small></label>
        <fieldset><legend>I’m joining as</legend><div className="type-options">{['Student', 'Professional', 'Other'].map(value => <label key={value}><input type="radio" name="type" value={value} checked={type === value} onChange={() => setType(value)} /><span>{value}</span></label>)}</div></fieldset>
        <label>{type === 'Student' ? 'College' : 'Organization'} {type === 'Other' && <small>(optional)</small>}<input name="organization" required={type !== 'Other'} pattern={type !== 'Other' ? '.*\S.*' : undefined} maxLength={150} placeholder={type === 'Student' ? 'Your college name' : 'Your organization'} /></label>
        <label>Team name <small>(optional)</small><input name="team" maxLength={100} placeholder="Flying solo? Leave this blank." /></label>
        <label>GitHub profile URL <small>(optional)</small><input name="githubUrl" type="url" inputMode="url" autoCapitalize="none" autoCorrect="off" maxLength={500} pattern="https://(www[.])?github[.]com/.+" title="Enter a full GitHub profile URL starting with https://github.com/" placeholder="https://github.com/your-username" /></label>
        <label>LinkedIn profile URL <small>(optional)</small><input name="linkedinUrl" type="url" inputMode="url" autoCapitalize="none" autoCorrect="off" maxLength={500} pattern="https://(www[.])?linkedin[.]com/in/.+" title="Enter a full LinkedIn profile URL starting with https://www.linkedin.com/in/" placeholder="https://www.linkedin.com/in/your-profile" /></label>
        {error && <p role="alert">{error}</p>}<button className="primary wide" type="submit" disabled={busy}>{busy ? 'Saving registration…' : 'Get my QR pass'} <span aria-hidden="true">↗</span></button><p className="form-note">Your details are saved for demo registration and check-in. Keep your pass private.</p>
      </form><a className="text-link" href="#login">Organizer login <span aria-hidden="true">↗</span></a>
    </section>
  </div>;
}

function Pass({ person, emailNew = false }) {
  const details = [
    ['Email', person.email || 'Not provided'],
    ['Joining as', person.type],
    ['College / organization', person.organization || 'Not provided'],
    ['Team name', person.team?.trim() || 'Solo attendee'],
    ['GitHub profile', person.githubUrl || 'Not provided'],
    ['LinkedIn profile', person.linkedinUrl || 'Not provided'],
  ];
  const ticketRef = useRef(null);
  const [downloading, setDownloading] = useState(false);
  const [qr, setQr] = useState('');
  const [error, setError] = useState('');
  const [emailMessage, setEmailMessage] = useState(emailNew ? 'Preparing your email attachment…' : '');
  const emailAttempt = useRef(false);
  async function image() {
    await document.fonts.ready;
    await Promise.all(Array.from(ticketRef.current.querySelectorAll('img'), image => image.decode()));
    return toPng(ticketRef.current, { pixelRatio: 3, style: { margin: '0' } });
  }
  useEffect(() => {
    if (!qr || !emailNew || emailAttempt.current) return;
    emailAttempt.current = true;
    (async () => {
      try {
        const png = await image();
        const result = await api(`/registrations/${person.id}/email`, { method: 'POST', body: { token: person.token, png: png.split(',')[1] } });
        setEmailMessage(result.message);
      } catch { setEmailMessage('Email could not be sent. Your registration is saved—download your pass here.'); }
    })();
  }, [qr, emailNew, person.id]);
  useEffect(() => { let active = true; setQr(''); QRCode.toDataURL(person.token, { margin: 4, width: 560, color: { dark: '#000000', light: '#ffffff' } }).then(url => { if (active) setQr(url); }).catch(() => { if (active) setError('Could not render your QR. Please reload.'); }); return () => { active = false; }; }, [person.token]);
  async function download() {
    if (downloading) return;
    setDownloading(true); setError('');
    try {
      const png = await image();
      const link = document.createElement('a'); link.download = `eventflow-${person.id}.png`; link.href = png; document.body.append(link); link.click(); link.remove();
    } catch { setError('Download failed. Try again or save a screenshot of the pass.'); }
    finally { setDownloading(false); }
  }
  return <div className="pass-layout"><section className="pass-intro"><p className="eyebrow">02 / YOUR DEMO PASS</p><h1 className="display-title">YOU’RE<br /><span className="orange">ON THE</span><br />LIST<span className="orange">.</span></h1><p className="lead">Save this pass—you will need it at check-in.</p><p>Your QR contains an opaque token, without your personal details. Keep it private.</p><button className="primary" onClick={download} disabled={!qr || downloading}>{downloading ? 'Preparing download…' : 'Download QR pass'} <span aria-hidden="true">↓</span></button><p role="status">{emailMessage}</p><a href="#register" className="text-link">← Back to registration</a></section>
    <div className="ticket-export" ref={ticketRef}><article className="ticket"><div className="ticket-top"><img className="ticket-logo" src="/brand/eventflow-logo.png" alt="EventFlow QR" /><span className="tag">DEMO PASS</span></div><div className="ticket-body"><p className="eyebrow">{event.name}</p><h2>{person.name}</h2><div className="qr-wrap">{qr ? <img src={qr} alt={`QR pass for ${person.name}`} width="280" height="280" /> : <p role="status">Preparing QR…</p>}</div><p className="registration-id">{person.id}</p><p className="ticket-instruction">Show this QR at the event entrance.</p><dl className="pass-details">{details.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></div><div className="ticket-stub"><EventDetails /><p>Demo only · Separate from official registration</p><p className="micro">DEMO REGISTRATION / NOT OFFICIAL ADMISSION</p></div></article></div>{error && <p role="alert">{error}</p>}
  </div>;
}

function Login({ onLogin }) {
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault(); if (busy) return;
    const body = Object.fromEntries(new FormData(e.currentTarget)); setBusy(true); setError('');
    try { onLogin(await api('/organizer/login', { method: 'POST', body })); }
    catch (error) { setError(error.message); } finally { setBusy(false); }
  }
  return <section className="login-panel"><p className="eyebrow">ORGANIZER ACCESS</p><h1 className="display-title">AT THE <span className="orange">DOOR.</span></h1><p>Sign in to scan passes, view attendance and assist with recovery.</p><form onSubmit={submit}><label>Email<input name="email" type="email" autoComplete="username" required maxLength={254}/></label><label>Password<input name="password" type="password" autoComplete="current-password" required maxLength={256}/></label>{error && <p role="alert">{error}</p>}<button className="primary wide" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button></form></section>;
}

export function Scanner({ checkPass = value => api('/checkins', { method: 'POST', body: { token: value } }), eventName = '', backHref = '#dashboard' }) {
  const [active, setActive] = useState(false); const [starting, setStarting] = useState(false);
  const [error, setError] = useState(''); const [result, setResult] = useState(null); const [input, setInput] = useState('');
  const videoRef = useRef(null); const streamRef = useRef(null); const requestRef = useRef(0);
  const timerRef = useRef(null); const checking = useRef(false); const manualRef = useRef(null);
  function stopCamera() {
    requestRef.current++; clearTimeout(timerRef.current);
    streamRef.current?.getTracks().forEach(track => track.stop()); streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setActive(false); setStarting(false);
  }
  useEffect(() => () => { requestRef.current++; clearTimeout(timerRef.current); streamRef.current?.getTracks().forEach(track => track.stop()); }, []);
  async function check(value) {
    if (checking.current) return;
    checking.current = true; stopCamera(); setError(''); setResult({ status: 'pending' });
    try { setResult(await checkPass(value.trim())); }
    catch (error) { setResult({ status: 'error' }); setError(error.message); }
  }
  async function startCamera() {
    if (checking.current || streamRef.current) return;
    setError('');
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) { setError('Camera access requires HTTPS and a supported browser. You can enter the registration ID below.'); return; }
    const request = ++requestRef.current; setStarting(true);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
      if (request !== requestRef.current) { stream.getTracks().forEach(track => track.stop()); return; }
      streamRef.current = stream; videoRef.current.srcObject = stream; await videoRef.current.play();
      if (request !== requestRef.current) return;
      setActive(true); setStarting(false);
      const canvas = document.createElement('canvas'); const ctx = canvas.getContext('2d', { willReadFrequently: true });
      function decode() {
        if (request !== requestRef.current || checking.current) return;
        const video = videoRef.current;
        if (video?.readyState >= 2 && video.videoWidth) {
          canvas.width = Math.min(640, video.videoWidth); canvas.height = Math.round(canvas.width * video.videoHeight / video.videoWidth);
          ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
          const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
          const decoded = jsQR(image.data, image.width, image.height, { inversionAttempts: 'dontInvert' });
          if (decoded?.data) { check(decoded.data); return; }
        }
        timerRef.current = setTimeout(decode, 200);
      }
      decode();
    } catch (error) {
      if (request !== requestRef.current) return;
      stopCamera(); setError(error.name === 'NotAllowedError' ? 'Camera permission denied. Allow camera access in browser settings or enter the registration ID.' : 'Could not open the camera. Close other camera apps or enter the registration ID.');
    }
  }
  const states = { checked_in: ['success', '✓', 'Checked in'], already_checked_in: ['duplicate', '↻', 'Already checked in'], invalid_pass: ['invalid', '×', 'Invalid pass'], wrong_event: ['invalid', '×', 'Pass for another event'], pending: ['waiting', '…', 'Checking pass…'], error: ['invalid', '!', 'Could not check pass'] };
  const state = result && states[result.status];
  return <div className="scanner-page"><div className="page-heading"><div><p className="eyebrow">03 / ORGANIZER{eventName && ` · ${eventName}`}</p><h1 className="display-title">SCAN. <span className="orange">WELCOME.</span></h1></div><a href={backHref} className="text-link">← Dashboard</a></div><div className="scanner-layout"><section><div className={`camera-stage ${active ? 'camera-live' : ''}`}><video ref={videoRef} muted playsInline autoPlay aria-label="Live camera preview"/><div className="camera-meta"><span className="status-dot"/>{result ? 'Paused after scan' : starting ? 'Waiting for permission…' : active ? 'Camera live' : 'Camera stopped'}<span>ENTRANCE / 01</span></div><div className="scan-target" aria-hidden="true"><span>{active ? '' : 'QR'}</span></div><p>{active ? 'Hold the QR inside the frame' : 'Ready when you are.'}</p><small>One pass. One check-in.</small></div><div className="camera-controls"><button className="primary" disabled={!!result || active || starting} onClick={startCamera}>{starting ? 'Starting camera…' : 'Start camera'}</button><button className="secondary" disabled={!active && !starting} onClick={stopCamera}>Stop</button></div>{error && <p className="camera-error" role="alert">{error}</p>}<form className="manual-form" onSubmit={e => { e.preventDefault(); check(input); }}><label>Camera unavailable? Enter a token or registration ID<input ref={manualRef} value={input} onChange={e => setInput(e.target.value)} required maxLength={100} placeholder="EF-…" disabled={!!result}/></label><button className="secondary" disabled={!!result}>Check pass ↗</button></form></section><section className="scan-feedback"><p className="eyebrow">CHECK-IN RESULT</p>{result ? <div className={`result ${state[0]}`} role="status"><span className="result-icon" aria-hidden="true">{state[1]}</span><h2>{state[2]}</h2><h3>{result.attendee?.name || (result.status === 'invalid_pass' ? 'No matching registration' : '')}</h3>{result.attendee?.checkedInAt && <p>{result.status === 'already_checked_in' ? 'Original check-in' : 'Checked in'} · {formatTime(result.attendee.checkedInAt)} IST</p>}</div> : <div className="result waiting"><span className="result-icon" aria-hidden="true">↙</span><h2>One pass.<br/>One welcome.</h2><p>Start the camera or enter a registration ID.</p></div>}<button className="primary wide" disabled={!result || result.status === 'pending'} onClick={() => { checking.current = false; setResult(null); setInput(''); setError(''); manualRef.current?.focus(); }}>Scan next →</button><p className="form-note">Scanning pauses after every result.</p></section></div></div>;
}

const formatTime = value => new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', hour: 'numeric', minute: '2-digit', second: '2-digit' }).format(new Date(value));

function Dashboard({ onPass }) {
  const [data, setData] = useState(null); const [search, setSearch] = useState(''); const [filter, setFilter] = useState('all');
  const [recovery, setRecovery] = useState(null); const [confirmed, setConfirmed] = useState(false);
  const [message, setMessage] = useState('Loading attendance…'); const [error, setError] = useState(''); const [recovering, setRecovering] = useState(false);
  const refreshBusy = useRef(false); const mounted = useRef(true);
  async function refresh() {
    if (refreshBusy.current) return; refreshBusy.current = true;
    try { const result = await api('/attendance'); if (mounted.current) { setData(result); setError(''); setMessage(`Updated ${formatTime(new Date())} IST`); } }
    catch (error) { if (mounted.current) setError(error.message); } finally { refreshBusy.current = false; }
  }
  useEffect(() => { mounted.current = true; refresh(); const timer = setInterval(refresh, 5000); return () => { mounted.current = false; clearInterval(timer); }; }, []);
  async function recover() {
    if (!confirmed || recovering) return; setRecovering(true); setError('');
    try { onPass(await api(`/registrations/${recovery.id}/pass`)); } catch (error) { setError(error.message); } finally { setRecovering(false); }
  }
  const people = data?.attendees || [];
  const rows = people.filter(p => `${p.name} ${p.id}`.toLowerCase().includes(search.toLowerCase()) && (filter === 'all' || (filter === 'in' ? !!p.checkedInAt : !p.checkedInAt)));
  const recent = people.filter(p => p.checkedInAt).sort((a, b) => b.checkedInAt.localeCompare(a.checkedInAt)).slice(0, 5);
  return <div className="dashboard-page"><div className="page-heading"><div><p className="eyebrow">04 / ORGANIZER · {event.name}</p><h1 className="display-title">THE <span className="orange">TURNOUT.</span></h1></div><a className="primary" href="#scanner">Open scanner ↗</a></div><div className="stats">{[['registered', 'Registered', 'Total demo passes'], ['checkedIn', 'Checked in', 'Through the door'], ['notYetArrived', 'Not yet arrived', 'Still on the way']].map(([key, label, note]) => <div className={key === 'checkedIn' ? 'checked-stat' : ''} key={key}><p>{label}</p><strong>{data ? String(data.counts[key]).padStart(2, '0') : '—'}</strong><span>{note}</span></div>)}</div><div className="attendance-layout"><section className="attendee-section"><div className="list-heading"><h2>Attendee roll</h2><button className="text-link" onClick={refresh}>Refresh ↻</button></div><p className="refresh-note" role="status">{message} · Refreshes every 5 seconds</p>{error && <p role="alert">{error}</p>}<div className="table-tools"><label>Search attendees<input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Name or registration ID"/></label><label>Attendance<select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All attendees</option><option value="in">Checked in</option><option value="waiting">Not yet arrived</option></select></label></div><table><caption className="sr-only">Demo event attendees</caption><thead><tr><th scope="col">Attendee</th><th scope="col">Type</th><th scope="col">Attendance</th><th scope="col">Pass</th></tr></thead><tbody>{rows.map(p => <tr key={p.id}><td><strong>{p.name}</strong><small>{p.organization || 'Independent'} · {p.id}</small></td><td data-label="Type">{p.type}</td><td data-label="Attendance"><span className={`attendance-status ${p.checkedInAt ? 'arrived' : ''}`}>{p.checkedInAt ? '✓ Checked in' : '○ Not yet arrived'}</span><small>{p.checkedInAt ? `${formatTime(p.checkedInAt)} IST` : '—'}</small></td><td><button className="text-link" aria-label={`Recover pass for ${p.name}`} onClick={() => { setRecovery(p); setConfirmed(false); }}>Recover ↗</button></td></tr>)}</tbody></table>{data && !rows.length && <p className="empty">No matching attendees.</p>}{recovery && <section className="recovery" aria-label="Pass recovery"><h3>Recover {recovery.name}’s pass</h3><p>Confirm identity in person before retrieving the existing pass.</p><label className="checkbox"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)}/> I have confirmed their identity in person</label><div className="button-row"><button className="primary" disabled={!confirmed || recovering} onClick={recover}>{recovering ? 'Retrieving pass…' : 'Open existing pass ↗'}</button><button className="text-link" onClick={() => setRecovery(null)}>Cancel</button></div></section>}</section><aside className="recent"><p className="eyebrow">AT THE DOOR</p><h2>Recent<br/>check-ins<span className="orange">.</span></h2>{recent.map(p => <div className="recent-person" key={p.id}><span className="recent-mark" aria-hidden="true">↗</span><div><strong>{p.name}</strong><small>{p.id} / {formatTime(p.checkedInAt)} IST</small></div></div>)}{data && !recent.length && <p>No check-ins yet.</p>}<p className="micro">06 OCT 2026 / ASIA/KOLKATA</p></aside></div></div>;
}

export default function App() {
  const [page, setPage] = useState(currentPage); const [person, setPerson] = useState(null); const [emailNew, setEmailNew] = useState(false);
  const [session, updateSession] = useState(null); const [notice, setNotice] = useState(''); const main = useRef(null);
  useEffect(() => { function changed() { setPage(currentPage()); window.scrollTo(0, 0); main.current?.focus(); } window.addEventListener('hashchange', changed); return () => window.removeEventListener('hashchange', changed); }, []);
  useEffect(() => { function expired() { setSession(null); updateSession(null); setNotice('Your session has expired. Please sign in again.'); location.hash = 'login'; } window.addEventListener('session-expired', expired); const timer = session && setTimeout(expired, Math.max(0, session.expiresAt * 1000 - Date.now())); return () => { window.removeEventListener('session-expired', expired); clearTimeout(timer); }; }, [session]);
  function onPass(value, sendEmail = false) { setPerson(value); setEmailNew(sendEmail); location.hash = 'pass'; }
  function onLogin(value) { setSession(value); updateSession(value); setNotice(''); location.hash = 'dashboard'; }
  async function logout() { try { await api('/organizer/logout', { method: 'POST' }); } catch { setNotice('Signed out on this device. The server session expires within one hour.'); } finally { setSession(null); updateSession(null); location.hash = 'login'; } }
  const protectedPage = page === 'scanner' || page === 'dashboard';
  const links = [['register', 'Registration'], ...(person ? [['pass', 'QR pass']] : []), ...(session ? [['scanner', 'Scanner'], ['dashboard', 'Dashboard']] : [['login', 'Organizer login']])];
  return <><a className="skip-link" href="#main" onClick={e => { e.preventDefault(); main.current?.focus(); }}>Skip to content</a><aside className="preview-bar" aria-label="Demo notice"><strong>DEMO REGISTRATION</strong><span>Builders Breakout · Separate from official organizer registration</span></aside><header className="site-header"><a href="#register" aria-label="EventFlow QR registration"><img className="brand-logo" src="/brand/eventflow-logo.png" alt="EventFlow QR"/></a><nav aria-label="Main navigation">{links.map(([id, label]) => <a key={id} href={`#${id}`} aria-current={page === id ? 'page' : undefined}>{label}</a>)}{session && <button className="text-link" onClick={logout}>Log out</button>}</nav></header><main id="main" ref={main} tabIndex="-1">{notice && <p role="status">{notice}</p>}{page === 'register' && <Register onPass={onPass}/>}{page === 'pass' && (person ? <Pass key={person.id} person={person} emailNew={emailNew}/> : <section><h1>Your pass</h1><p>Use your downloaded pass. If it is lost, ask an organizer to confirm your identity and recover it.</p><a href="#register">Registration</a></section>)}{(page === 'login' || (protectedPage && !session)) && <Login onLogin={onLogin}/>} {session && page === 'scanner' && <Scanner/>}{session && page === 'dashboard' && <Dashboard onPass={onPass}/>}</main><footer><img className="team-signature" src="/brand/team-signature.png" alt="Built by Error 403: Forbidden SSO"/><div className="footer-team"><span>Four people. One flow.</span></div></footer></>;
}
