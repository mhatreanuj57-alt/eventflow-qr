"""Exercise deletion on an isolated QA event; never touch the user's event."""
import base64
import hashlib
import json
import secrets
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'infra'))
from aws_session import eventflow_session
session = eventflow_session()
assert session.client('sts').get_caller_identity()['Account'] == '122458452281'
table = session.resource('dynamodb').Table('eventflow-qr')
preserved_key = {'pk': 'EVENT#0e2754890d4738903b009e56a7190562', 'sk': 'ATTENDEE#EF-99CC6DF31A'}
before = table.get_item(Key=preserved_key, ConsistentRead=True).get('Item')
assert before, 'Expected user attendee missing; stop before QA writes'
manifest = root / '.audit/platform-qa.json'
assert not manifest.exists(), 'Clean prior QA fixtures first'
fixture = {'organizers': [], 'events': [], 'attendees': [], 'password': secrets.token_urlsafe(24)}
def save(): manifest.write_text(json.dumps(fixture), encoding='utf-8')
def request(path, body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    req = Request('https://7ctg987wi2.execute-api.ap-southeast-2.amazonaws.com' + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urlopen(req, timeout=30) as response: return response.status, json.load(response)
    except HTTPError as error: return error.code, json.load(error)
def iso(value): return value.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(timespec='seconds')
save()
status, organizer = request('/platform/signup', {'name': 'Fictional Deletion QA Host', 'email': 'eventflow-qa-' + secrets.token_hex(8) + '@example.test', 'password': fixture['password']})
assert status == 201, status
fixture['organizers'].append(organizer); save()
now = datetime.now(timezone.utc)
draft = {'name': 'Fictional platform QA deletion', 'host': 'QA only', 'venue': 'Fictional QA venue', 'description': 'Temporary deletion check',
    'start': iso(now + timedelta(days=1)), 'end': iso(now + timedelta(days=1, hours=4)), 'registrationOpen': iso(now - timedelta(hours=1)),
    'registrationClose': iso(now + timedelta(hours=1)), 'color': '#ff5100', 'mark': 'QA', 'layout': 'editorial',
    'logo': 'data:image/png;base64,' + base64.b64encode((root / 'frontend/public/brand/eventflow-logo.png').read_bytes()).decode()}
status, event = request('/platform/organizer/events', draft, organizer['token'])
assert status == 201, status
fixture['events'].append(event); save()
status, person = request('/platform/events/' + event['id'] + '/registrations', {'name': 'Fictional Delete Attendee', 'email': 'delete-' + secrets.token_hex(8) + '@example.test', 'type': 'Other'})
assert status == 201, status
fixture['attendees'].append({**person, 'eventId': event['id']}); save()
path = '/platform/organizer/events/' + event['id']
meta_key = {'pk': 'EVENT#' + event['id'], 'sk': 'META'}
meta = table.get_item(Key=meta_key, ConsistentRead=True)['Item']
assert request(path + '/delete', {'confirmation': event['name']})[0] == 401
assert request(path + '/delete', {'confirmation': 'wrong name'}, organizer['token'])[0] == 400
assert request('/platform/organizer/events/0e2754890d4738903b009e56a7190562/delete', {'confirmation': 'Builder CUP'}, organizer['token'])[0] == 404
for _ in range(10):
    status, deleted = request(path + '/delete', {'confirmation': event['name']}, organizer['token'])
    assert status == 200, (status, deleted)
    if deleted['status'] == 'deleted': break
assert deleted['status'] == 'deleted'
keys = [meta_key, {'pk': 'EVENT#' + event['id'], 'sk': 'ATTENDEE#' + person['id']}, {'pk': 'PUBLIC#EVENTS', 'sk': event['id']},
    {'pk': 'OWNER#' + organizer['organizer']['id'], 'sk': 'EVENT#' + event['id']},
    {'pk': 'EMAIL#' + event['id'] + '#' + hashlib.sha256(person['email'].encode()).hexdigest(), 'sk': 'VALUE'},
    {'pk': 'TOKEN#' + hashlib.sha256(person['token'].encode()).hexdigest(), 'sk': 'VALUE'}]
assert all(not table.get_item(Key=key, ConsistentRead=True).get('Item') for key in keys)
assert request('/platform/events/' + event['id'])[0] == 404
assert request(path + '/attendance', token=organizer['token'])[0] == 404
assert request('/platform/organizer/events', token=organizer['token'])[0] == 200
s3 = session.client('s3')
assert not s3.list_objects_v2(Bucket='eventflow-artifacts-122458452281-ap-southeast-2', Prefix=meta['logoKey']).get('Contents')
assert table.get_item(Key=preserved_key, ConsistentRead=True)['Item'] == before
print('PASS: actual confirmed deletion removes QA event/attendee/lookups/logo; missing confirmation, unauthenticated and wrong-owner requests rejected. Builder CUP attendee unchanged. No emails sent.')
