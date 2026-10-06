import React, { useEffect, useState } from 'react';
import { api } from './api';

export function StatusCard({ event, person }) {
  const state = person.registrationStatus || 'approved';
  const descriptions = { approved: 'Your place is confirmed. Save your QR pass below.', pending: 'Your request is awaiting organizer approval. This is not an entry pass.', waitlisted: 'You are on the waitlist. Your place is not confirmed until the organizer approves it.', declined: 'The organizer declined this registration. This registration does not grant entry.', cancelled: 'This registration has been cancelled. Its QR pass cannot be used.' };
  return <section className="registration-status-card"><p className="eyebrow">{event.name} / {person.ticketName || 'Standard'}</p><h2>{state === 'pending' ? 'Awaiting approval.' : state === 'waitlisted' ? 'On the waitlist.' : state === 'approved' ? 'You’re on the list.' : 'Registration closed.'}</h2><p>{descriptions[state]}</p><p><strong>{person.name}</strong> · {person.id}</p>{person.accessToken && <a className="text-link" href={`#registration/${person.accessToken}`}>Open your private registration status</a>}</section>;
}

export default function RegistrationStatus({ token, renderPass }) {
  const [data, setData] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false), [confirmed, setConfirmed] = useState(false);
  useEffect(() => { let active = true, reading = false; const refresh = async () => { if (reading) return; reading = true; try { const result = await api('/platform/registration/open', { method: 'POST', body: { token } }); if (active) { setData(result); setError(''); } } catch (issue) { if (active) setError(issue.message); } finally { reading = false; } }; refresh(); const timer = setInterval(refresh, 15000); return () => { active = false; clearInterval(timer); }; }, [token]);
  async function cancel() { if (busy || !confirmed) return; setBusy(true); setError(''); try { setData(await api('/platform/registration/cancel', { method: 'POST', body: { token } })); setConfirmed(false); } catch (issue) { setError(issue.message); } finally { setBusy(false); } }
  const status = data?.attendee.registrationStatus || 'approved';
  return <div className="registration-status-page"><p className="eyebrow">YOUR PRIVATE REGISTRATION LINK</p><h1 className="display-title">YOUR <span className="orange">PLACE.</span></h1><p>Keep this link private. Anyone with it can view or cancel this registration before check-in.</p>{error && <p role="alert">{error}</p>}{!data && !error && <p role="status">Loading registration…</p>}{data && <><StatusCard event={data.event} person={data.attendee}/>{status === 'approved' && renderPass(data.event, data.attendee)}{['approved', 'pending', 'waitlisted'].includes(status) && !data.attendee.checkedInAt && <section className="recovery"><h2>Can’t make it?</h2><label className="checkbox"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)}/> I want to cancel this registration. This cannot be undone.</label><button className="secondary" disabled={!confirmed || busy} onClick={cancel}>{busy ? 'Cancelling…' : 'Cancel my registration'}</button></section>}</>}<a href="#events" className="text-link">Explore events</a></div>;
}

export function GuestInvitation({ token, renderEvent }) {
  const [data, setData] = useState(null), [error, setError] = useState('');
  useEffect(() => { let active = true; api('/platform/guest-invites/open', { method: 'POST', body: { token } }).then(result => { if (active) setData(result); }).catch(issue => { if (active) setError(issue.message); }); return () => { active = false; }; }, [token]);
  return <>{error && <p role="alert">{error}</p>}{!data && !error && <p role="status">Opening your invitation…</p>}{data && <><p className="guest-invitation-note">You’re invited as {data.email}. Register below; an invitation does not reserve a place.</p>{renderEvent(data.event, data.email)}</>}</>;
}
