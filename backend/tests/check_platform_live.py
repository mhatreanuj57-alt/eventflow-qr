"""Isolated HTTPS platform checks. Leaves only named QA fixtures for browser checks/cleanup."""
import base64
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

API = 'https://7ctg987wi2.execute-api.ap-southeast-2.amazonaws.com'
root = Path(__file__).resolve().parents[2]
manifest = root / '.audit/platform-qa.json'
assert not manifest.exists(), 'Clean previous platform QA fixtures before a new test.'
fixture = {'organizers': [], 'events': [], 'attendees': [], 'password': secrets.token_urlsafe(24)}
def save(): manifest.write_text(json.dumps(fixture), encoding='utf-8')
def request(path, body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    req = Request(API + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urlopen(req, timeout=30) as response: return response.status, json.load(response)
    except HTTPError as error: return error.code, json.load(error)
def iso(value): return value.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(timespec='seconds')

save()
for index in range(2):
    email = 'eventflow-qa-' + secrets.token_hex(8) + '@example.test'
    status, account = request('/platform/signup', {'name': 'Fictional QA Host ' + str(index + 1), 'email': email, 'password': fixture['password']})
    assert status == 201, (status, account)
    fixture['organizers'].append(account); save()
    now = datetime.now(timezone.utc)
    draft = {'name': 'Fictional platform QA ' + str(index + 1), 'host': 'QA only', 'description': 'Temporary automated regression fixture; removed after checks.',
        'venue': 'Fictional QA venue', 'start': iso(now + timedelta(days=1)), 'end': iso(now + timedelta(days=1, hours=4)),
        'registrationOpen': iso(now - timedelta(days=1)), 'registrationClose': iso(now + timedelta(hours=2)),
        'color': '#ff5100', 'mark': 'QA', 'layout': 'editorial', 'logo': ''}
    if index == 0:
        draft['logo'] = 'data:image/png;base64,' + base64.b64encode((root / 'frontend/public/brand/eventflow-logo.png').read_bytes()).decode()
    status, event = request('/platform/organizer/events', draft, account['token'])
    assert status == 201, (status, event)
    fixture['events'].append(event); save()
    if index == 0:
        assert event['logo'].startswith('https://')
        with urlopen(Request(event['logo'], headers={'Origin': 'http://localhost:4174'}), timeout=20) as image:
            assert image.read().startswith(b'\x89PNG\r\n\x1a\n')
            assert image.headers.get('Access-Control-Allow-Origin') == 'http://localhost:4174'

first, second = fixture['organizers']
a, b = fixture['events']
person = {'name': 'Fictional Platform Attendee', 'email': ' Fictional-' + secrets.token_hex(8) + '@example.test ', 'type': 'Professional', 'organization': 'QA Studio', 'team': '', 'githubUrl': 'https://github.com/octocat', 'linkedinUrl': ''}
with ThreadPoolExecutor(max_workers=8) as pool:
    results = list(pool.map(lambda _: request('/platform/events/' + a['id'] + '/registrations', person), range(8)))
for status, attendee in results:
    if status == 201: fixture['attendees'].append({**attendee, 'eventId': a['id']}); save()
assert sum(status == 201 for status, _ in results) == 1, [status for status, _ in results]
assert all(status in (201, 409) for status, _ in results), [status for status, _ in results]
assert all('token' not in result for status, result in results if status == 409)
created = fixture['attendees'][0]
time.sleep(1)
status, another = request('/platform/events/' + b['id'] + '/registrations', person)
assert status == 201, (status, another)
fixture['attendees'].append({**another, 'eventId': b['id']}); save()
path = '/platform/organizer/events/' + a['id']
with ThreadPoolExecutor(max_workers=8) as pool:
    scans = list(pool.map(lambda _: request(path + '/checkins', {'token': created['token']}, first['token']), range(8)))
assert all(status == 200 for status, _ in scans), [status for status, _ in scans]
assert sum(result['status'] == 'checked_in' for _, result in scans) == 1
assert len({result['attendee']['checkedInAt'] for _, result in scans}) == 1
for suffix, body in (('/attendance', None), ('/checkins', {'token': created['token']}), ('/pass/' + created['id'], {'identityConfirmed': True}), ('', draft)):
    assert request(path + suffix, body, second['token'])[0] == 404
    assert request(path + suffix, body)[0] == 401
assert request(path + '/checkins', {'token': another['token']}, first['token'])[1] == {'status': 'wrong_event'}
assert request(path + '/checkins', {'token': 'invalid-token'}, first['token'])[1] == {'status': 'invalid_pass'}
assert request(path + '/pass/' + created['id'], {}, first['token'])[0] == 400
status, recovery = request(path + '/pass/' + created['id'], {'identityConfirmed': True}, first['token'])
assert status == 200 and recovery['token'] == created['token']
assert recovery['checkedInAt'] == scans[0][1]['attendee']['checkedInAt']
assert request(path + '/attendance', token=first['token'])[1]['counts'] == {'registered': 1, 'checkedIn': 1, 'notYetArrived': 0}
assert request('/platform/events/' + a['id'] + '/registrations', {**person, 'email': 'bad', 'organization': ''})[0] == 400
assert request('/platform/events/' + a['id'] + '/registrations/' + created['id'] + '/email', {'token': 'invalid', 'png': ''})[0] == 404
status, login = request('/platform/login', {'email': first['organizer']['email'], 'password': fixture['password']})
assert status == 200
assert request('/platform/logout', {}, login['token'])[0] == 200
assert request('/platform/organizer/events', token=login['token'])[0] == 401
# The second event crosses a real server deadline, then is moved past the public grace period.
now = datetime.now(timezone.utc)
closing = {**b, 'registrationClose': iso(now + timedelta(seconds=5))}
assert request('/platform/organizer/events/' + b['id'], closing, second['token'])[0] == 200
time.sleep(6)
closed = request('/platform/events/' + b['id'])[1]
assert closed['status'] == 'closed'
assert request('/platform/events/' + b['id'] + '/registrations', {**person, 'email': 'late@example.test'})[0] == 409
assert b['id'] in [e['id'] for e in request('/platform/events')[1]['events']]
closing['registrationClose'] = iso(datetime.now(timezone.utc) - timedelta(seconds=901))
assert request('/platform/organizer/events/' + b['id'], closing, second['token'])[0] == 200
assert b['id'] not in [e['id'] for e in request('/platform/events')[1]['events']]
assert b['id'] in [e['id'] for e in request('/platform/organizer/events', token=second['token'])[1]['events']]
fixture['attendees'][0]['checkedInAt'] = recovery['checkedInAt']; save()
print('PASS: actual HTTPS concurrency, tenant isolation, recovery, invalid inputs, logout and real deadline/public retention. No emails sent. QA fixtures ready for browser checks.')
