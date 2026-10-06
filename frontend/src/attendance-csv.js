export function attendanceCsv(people) {
  const cell = value => {
    let text = String(value ?? '');
    if (/^[\s\u0000-\u001f]*[=+\-@]/.test(text)) text = `'${text}`;
    return `"${text.replaceAll('"', '""')}"`;
  };
  const fields = ['id', 'name', 'email', 'type', 'organization', 'team', 'githubUrl', 'linkedinUrl', 'createdAt', 'checkedInAt', 'ticketName', 'registrationStatus'];
  return '\ufeff' + [fields, ...people.map(person => fields.map(field => person[field]))].map(row => row.map(cell).join(',')).join('\r\n');
}
