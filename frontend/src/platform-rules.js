export const HIDE_DELAY = 15 * 60 * 1000;
export function clearLocalTestData(storage) {
  const marker = 'eventflow-local-cleanup-2026-10-03';
  if (storage.getItem(marker) === 'done') return false;
  storage.removeItem('eventflow-platform-design-v1');
  storage.setItem(marker, 'done');
  return true;
}
export function addPreviewAttendee(people, event, person, now = Date.now()) {
  if (eventState(event, now) !== 'open') throw Error('Registration is not open for this event.');
  const email = person.email.trim().toLowerCase();
  if (people.some(p => p.eventId === event.id && p.email === email)) throw Error('Already registered for this event. Use your saved preview pass.');
  const attendee = { ...person, email, eventId: event.id };
  people.push(attendee);
  return attendee;
}
export function checkPreviewPass(people, eventId, value, timestamp = new Date().toISOString()) {
  const person = people.find(p => p.token === value.trim() || p.id === value.trim().toUpperCase());
  if (!person) return { status: 'invalid_pass' };
  if (person.eventId !== eventId) return { status: 'wrong_event' };
  const status = person.checkedInAt ? 'already_checked_in' : 'checked_in';
  person.checkedInAt ||= timestamp;
  return { status, attendee: { ...person } };
}
export function eventState(event, now = Date.now()) {
  if (now >= Date.parse(event.registrationClose) + HIDE_DELAY) return 'hidden';
  if (now >= Date.parse(event.registrationClose)) return 'closed';
  if (now < Date.parse(event.registrationOpen)) return 'scheduled';
  if (event.capacity && event.registered >= event.capacity) return 'full';
  const tickets = event.tickets?.filter(ticket => !ticket.hidden && now >= Date.parse(ticket.registrationOpen || event.registrationOpen) && now < Date.parse(ticket.registrationClose || event.registrationClose)) || [];
  if (tickets.length && tickets.every(ticket => ticket.capacity && ticket.registered >= ticket.capacity)) return 'full';
  return 'open';
}
export function indiaTime(value) { return `${value}:00+05:30`; }
export function brandInk(hex) {
  const rgb = hex.match(/[a-f\d]{2}/gi)?.map(value => parseInt(value, 16) / 255);
  if (rgb?.length !== 3) return '#191916';
  const light = rgb.map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  const luminance = light[0] * 0.2126 + light[1] * 0.7152 + light[2] * 0.0722;
  return luminance > 0.179 ? '#000000' : '#ffffff';
}
export function validateEvent(event) {
  const times = ['start', 'end', 'registrationOpen', 'registrationClose'].map(key => Date.parse(event[key]));
  if (times.some(value => !Number.isFinite(value))) return 'Please choose all event and registration dates.';
  if (times[1] <= times[0]) return 'The event must end after it starts.';
  if (times[3] <= times[2]) return 'Registration must close after it opens.';
  if (times[3] > times[1]) return 'Registration must close by the end of the event.';
  return '';
}
