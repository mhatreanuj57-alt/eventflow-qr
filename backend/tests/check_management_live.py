"""Actual AWS lifecycle checks; isolated records and reserved addresses, no email."""
import hashlib
import json
import secrets
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from boto3.dynamodb.conditions import Key

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'infra'))
from aws_session import eventflow_session
session = eventflow_session()
assert session.region_name == 'ap-southeast-2'
assert session.client('sts').get_caller_identity()['Account'] == '122458452281'
table = session.resource('dynamodb').Table('eventflow-qr')
manifest = root / '.audit/management-qa-scope.json'
assert not manifest.exists(), 'Review previous QA cleanup first'
scope = {'accounts': [], 'events': []}
accounts = []
digest = lambda value: hashlib.sha256(value.encode()).hexdigest()


def save(): manifest.write_text(json.dumps(scope, indent=2), encoding='utf-8')
def request(path, body=None, token=None):
    headers = {'content-type': 'application/json'}
    if token: headers['authorization'] = 'Bearer ' + token
    req = Request('https://7ctg987wi2.execute-api.ap-southeast-2.amazonaws.com' + path,
                  data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urlopen(req, timeout=30) as result: return result.status, json.load(result)
    except HTTPError as issue: return issue.code, json.load(issue)
def scan():
    items, params = [], {'ConsistentRead': True}
    while True:
        result = table.scan(**params); items.extend(result['Items'])
        if not result.get('LastEvaluatedKey'): return items
        params['ExclusiveStartKey'] = result['LastEvaluatedKey']
def snapshot(items):
    return {(p['pk'], p['sk']): digest(json.dumps(p, sort_keys=True, default=str)) for p in items if not p['pk'].startswith('RATE#')}
def iso(value): return value.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(timespec='seconds')
def checked(result, expected=200):
    assert result[0] == expected, (result[0], result[1].get('message', 'Unexpected response'))
    return result[1]


before = snapshot(scan())
save()
try:
    for label in ('owner', 'stranger'):
        email = 'eventflow-management-' + label + '-' + secrets.token_hex(8) + '@example.test'
        scope['accounts'].append({'email': email, 'id': None}); save()
        result = checked(request('/platform/signup', {'name': 'Fictional management QA', 'email': email, 'password': secrets.token_urlsafe(24)}), 201)
        scope['accounts'][-1]['id'] = result['organizer']['id']; save(); accounts.append(result)
    owner, stranger = accounts
    now = datetime.now(timezone.utc)
    draft = {'name': 'Fictional management QA ' + secrets.token_hex(4), 'host': 'Temporary QA only', 'venue': 'Fictional venue', 'description': '',
             'start': iso(now + timedelta(days=1)), 'end': iso(now + timedelta(days=1, hours=4)), 'registrationOpen': iso(now - timedelta(hours=1)),
             'registrationClose': iso(now + timedelta(hours=1)), 'color': '#ff5100', 'layout': 'editorial', 'mark': 'QA', 'logo': '', 'capacity': '1',
             'cover': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=',
             'waitlist': True, 'visibility': 'unlisted', 'tickets': [{'id': 'review', 'name': 'Review ticket', 'capacity': '1', 'requireApproval': True}],
             'questions': [{'id': 'interest', 'label': 'What will you learn?', 'type': 'choice', 'required': True, 'choices': ['Python', 'React']}]}
    event = checked(request('/platform/organizer/events', draft, owner['token']), 201)
    scope['events'].append({'id': event['id'], 'name': event['name']}); save()
    path = '/platform/organizer/events/' + event['id']
    public = '/platform/events/' + event['id']
    assert event['cover'].startswith('https://') and not event['logo']
    event = checked(request(path, {**draft, 'cover': event['cover'], 'logo': draft['cover']}, owner['token']))
    assert event['cover'].startswith('https://') and event['logo'].startswith('https://')
    assert any(e['id'] == event['id'] for e in checked(request('/platform/organizer/events', token=owner['token']))['events'])
    person = {'name': 'Fictional guest', 'type': 'Other', 'ticketId': 'review', 'answers': {'interest': 'Python'}}
    def register(label, **extra): return request(public + '/registrations', {**person, 'email': label + '-' + event['id'] + '@example.test', **extra})
    checked(register('invalid', answers={}), 400)
    a = checked(register('first'), 201); b = checked(register('second'), 201)
    assert a['registrationStatus'] == b['registrationStatus'] == 'pending' and not a['token']
    assert checked(request(path + '/checkins', {'token': a['id']}, owner['token']))['status'] == 'invalid_pass'
    checked(request(path + '/pass/' + a['id'], {'identityConfirmed': True}, owner['token']), 404)
    checked(request(path + '/attendance'), 401)
    checked(request(path + '/communications', token=stranger['token']), 404)
    checked(request(path + '/guests/' + a['id'], {'action': 'approve'}, stranger['token']), 404)
    assert all(e['id'] != event['id'] for e in checked(request('/platform/events'))['events'])
    def approve(guest): return request(path + '/guests/' + guest['id'], {'action': 'approve'}, owner['token'])
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(approve, (a, b)))
    assert sorted(code for code, _ in results) == [200, 409]
    issued = next(value for code, value in results if code == 200)
    assert issued['token'] and checked(request(public))['registered'] == 1
    c = checked(register('waitlist'), 201)
    assert c['registrationStatus'] == 'waitlisted' and not c['token']
    checked(register('again', email=a['email'].upper()), 409)
    checked(request('/platform/registration/open', {'token': 'invalid-capability'}), 404)
    checked(request(path + '/communications', {'action': 'invite', 'emails': ['invite-' + event['id'] + '@example.test']}, owner['token']))
    jobs = table.query(KeyConditionExpression=Key('pk').eq('EVENT#' + event['id']) & Key('sk').begins_with('MAIL#'), ConsistentRead=True)['Items']
    invite = next(j for j in jobs if j['kind'] == 'invitation')
    invite_token = invite['body'].split('/#guest-invite/')[1].split('\n')[0]
    checked(request('/platform/guest-invites/open', {'token': invite_token}))
    checked(register('wrong', invitationToken=invite_token), 400)
    cancelled = checked(request('/platform/registration/cancel', {'token': issued['accessToken']}))
    assert cancelled['attendee']['registrationStatus'] == 'cancelled' and not cancelled['attendee']['token']
    assert checked(request(path + '/checkins', {'token': issued['token']}, owner['token']))['status'] == 'invalid_pass'
    assert checked(request(public))['registered'] == 0
    invited = checked(register('invite', invitationToken=invite_token), 201)
    assert invited['registrationStatus'] == 'approved' and invited['token']
    checked(request('/platform/guest-invites/open', {'token': invite_token}), 404)
    checked(request(path + '/communications', {'subject': 'QA update', 'message': 'Fictional message only.', 'recipients': [a['id'], b['id']]}, owner['token']))
    checked(request(path + '/communications', {'subject': 'Bad\nheader', 'message': 'x', 'recipients': [a['id']]}, owner['token']), 400)
    checked(request(path + '/communications', {'subject': 'x', 'message': 'x', 'recipients': ['EF-FFFFFFFFFF']}, owner['token']), 400)
    for _ in range(40):
        history = checked(request(path + '/communications', token=owner['token']))['messages']
        if history and all(j['mailState'] == 'skipped_test_recipient' for j in history): break
        time.sleep(1)
    else: raise AssertionError('Reserved-address mail jobs did not finish safely')
    assert all('body' not in j for j in history)
    opened = checked(request('/platform/registration/open', {'token': invited['accessToken']}))
    assert opened['attendee']['token'] == invited['token']
    with ThreadPoolExecutor(max_workers=2) as pool:
        scans = list(pool.map(lambda _: request(path + '/checkins', {'token': invited['token']}, owner['token']), range(2)))
    assert sorted(value['status'] for code, value in scans if code == 200) == ['already_checked_in', 'checked_in']
    checked(request('/platform/registration/cancel', {'token': invited['accessToken']}), 409)
    print('PASS: actual DynamoDB ticket/approval/waitlist transactions, private status/cancellation, invitation identity, owner boundaries, atomic scans and safe mail queue. No email sent.', flush=True)
finally:
    for event in scope['events']:
        result = checked(request('/platform/organizer/events/' + event['id'] + '/delete', {'confirmation': event['name']}, accounts[0]['token']))
        assert result['status'] == 'deleted', 'QA event cleanup requires review'
    ids = {a['id'] for a in scope['accounts'] if a['id']}
    keys = [{'pk': item['pk'], 'sk': item['sk']} for item in scan() if item['pk'].startswith('PLATFORM_SESSION#') and item.get('ownerId') in ids]
    keys.extend({'pk': 'ORGANIZER_EMAIL#' + digest(a['email']), 'sk': 'VALUE'} for a in scope['accounts'])
    with table.batch_writer() as batch:
        for item_key in keys: batch.delete_item(Key=item_key)
    assert snapshot(scan()) == before, 'Other application data changed or QA records remain'
    manifest.unlink()
    print('PASS: isolated QA records removed; existing application data preserved.', flush=True)
