const utc = value => new Date(value).toISOString().replaceAll(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
const escape = value => String(value || '').replaceAll('\\', '\\\\').replaceAll(/\r\n|\r|\n/g, '\\n').replaceAll(';', '\\;').replaceAll(',', '\\,');
export function eventCalendar(event, origin = location.origin, now = Date.now()) {
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//EventFlow QR//Events//EN', 'CALSCALE:GREGORIAN', 'BEGIN:VEVENT',
    `UID:${escape(event.id)}@eventflow-qr`, `DTSTAMP:${utc(now)}`, `DTSTART:${utc(event.start)}`, `DTEND:${utc(event.end)}`,
    `SUMMARY:${escape(event.name)}`, `LOCATION:${escape(event.venue)}`, `DESCRIPTION:${escape(event.description)}`, `URL:${origin}/#event/${event.id}`, 'END:VEVENT', 'END:VCALENDAR'];
  // RFC 5545 folding counts UTF-8 bytes, rather than cutting Unicode characters.
  const encoder = new TextEncoder();
  return lines.map(line => { let result = '', width = 0; for (const char of line) { const size = encoder.encode(char).length; if (width + size > 75) { result += '\r\n '; width = 1; } result += char; width += size; } return result; }).join('\r\n') + '\r\n';
}
export function googleCalendar(event, origin = location.origin) {
  return 'https://calendar.google.com/calendar/render?' + new URLSearchParams({ action: 'TEMPLATE', text: event.name, dates: `${utc(event.start)}/${utc(event.end)}`, details: `${event.description || ''}\n${origin}/#event/${event.id}`, location: event.venue, ctz: 'Asia/Kolkata' });
}
