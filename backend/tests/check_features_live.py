"""Isolated actual HTTPS/DynamoDB checks; never send email or alter user records.

Invitations and reset links are seeded directly for fictional reserved-domain
accounts. SMTP delivery is a separate check. Secrets/tokens/passwords stay in memory.
"""
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
manifest = root / '.audit/feature-qa-scope.json'
assert not manifest.exists(), 'Previous QA cleanup needs review before running again'
scope = {'accounts': [], 'events': [], 'keys': []}
accounts = []
digest = lambda value: hashlib.sha256(value.encode()).hexdigest()

def save(): manifest.write_text(json.dumps(scope, indent=2), encoding='utf-8')
def request(path, body=None, token=None):
    headers = {'content-type': 'application/json'}
    if token: headers['authorization'] = 'Bearer ' + token
    req = Request('https://7ctg987wi2.execute-api.ap-southeast-2.amazonaws.com' + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
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
def seed(item):
    scope['keys'].append({'pk': item['pk'], 'sk': item['sk']}); save()
    table.put_item(Item=item, ConditionExpression='attribute_not_exists(pk)')
def iso(value): return value.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(timespec='seconds')

before = snapshot(scan())
save()
try:
    password = secrets.token_urlsafe(24)
    for label in ('owner', 'volunteer'):
        email = 'eventflow-features-' + label + '-' + secrets.token_hex(8) + '@example.test'
        scope['accounts'].append({'email': email, 'id': None}); save()
        status, result = request('/platform/signup', {'name': 'Fictional feature QA ' + label, 'email': email, 'password': password})
        assert status == 201, ('signup', status)
        scope['accounts'][-1]['id'] = result['organizer']['id']; save(); accounts.append(result)
    owner, staff = accounts
    now = datetime.now(timezone.utc)
    draft = {'name': 'Fictional feature QA ' + secrets.token_hex(4), 'host': 'Temporary QA only', 'venue': 'Fictional venue', 'description': '',
             'start': iso(now + timedelta(days=1)), 'end': iso(now + timedelta(days=1, hours=4)), 'registrationOpen': iso(now - timedelta(hours=1)),
             'registrationClose': iso(now + timedelta(hours=1)), 'color': '#ff5100', 'layout': 'editorial', 'mark': 'QA', 'logo': '', 'capacity': '2'}
    status, event = request('/platform/organizer/events', draft, owner['token'])
    assert status == 201, ('event', status)
    scope['events'].append({'id': event['id'], 'name': event['name']}); save()
    path = '/platform/organizer/events/' + event['id']
    def register(index): return request('/platform/events/' + event['id'] + '/registrations', {'name': 'Fictional feature attendee', 'email': f'qa-{index}-{event["id"]}@example.test', 'type': 'Other'})
    with ThreadPoolExecutor(max_workers=6) as pool: results = list(pool.map(register, range(6)))
    assert [status for status, _ in results].count(201) == 2 and [status for status, _ in results].count(409) == 4
    person = next(value for status, value in results if status == 201)
    status, full = request('/platform/events/' + event['id'])
    assert status == 200 and full['registered'] == 2 and full['status'] == 'full'
    assert request(path, {**draft, 'capacity': '1'}, owner['token'])[0] == 409
    assert request(path, {**draft, 'capacity': '3'}, owner['token'])[0] == 200
    assert request('/platform/events/' + event['id'] + '/registrations', {'name': 'Duplicate', 'email': person['email'].upper(), 'type': 'Other'})[0] == 409
    assert request('/platform/events/' + event['id'])[1]['registered'] == 2
    assert request(path, {**draft, 'capacity': '2'}, owner['token'])[0] == 200
    assert request(path + '/attendance')[0] == 401
    assert request(path + '/attendance', token=staff['token'])[0] == 404
    invite_token = secrets.token_urlsafe(32); invite_id = digest(invite_token)
    invite = {'inviteId': invite_id, 'eventId': event['id'], 'email': staff['organizer']['email'], 'expiresAt': int(time.time()) + 900}
    seed({**invite, 'pk': 'EVENT#' + event['id'], 'sk': 'INVITE#' + invite_id})
    seed({**invite, 'pk': 'STAFF_INVITE#' + invite_id, 'sk': 'VALUE'})
    assert request('/platform/invitations/accept', {'token': invite_token}, owner['token'])[0] == 400
    assert request('/platform/invitations/accept', {'token': invite_token}, staff['token'])[0] == 200
    assert request('/platform/invitations/accept', {'token': invite_token}, staff['token'])[0] == 400
    status, workspace = request('/platform/organizer/events', token=staff['token'])
    assert status == 200 and workspace['events'][0]['role'] == 'scanner'
    assert request(path + '/attendance', token=staff['token'])[0] == 404
    assert request(path + '/staff', token=staff['token'])[0] == 404
    assert request(path + '/pass/' + person['id'], {'identityConfirmed': True}, staff['token'])[0] == 404
    assert request(path, draft, staff['token'])[0] == 404
    status, scan_result = request(path + '/checkins', {'token': person['token']}, staff['token'])
    assert status == 200 and scan_result['status'] == 'checked_in'
    again = request(path + '/checkins', {'token': person['id']}, staff['token'])[1]
    assert again['status'] == 'already_checked_in' and again['attendee']['checkedInAt'] == scan_result['attendee']['checkedInAt']
    assert request(path + '/staff', {'action': 'revoke', 'id': staff['organizer']['id']}, owner['token'])[0] == 200
    assert request(path + '/checkins', {'token': person['token']}, staff['token'])[0] == 404
    reset_token = secrets.token_urlsafe(32)
    reset_key = {'pk': 'PASSWORD_RESET#' + digest(reset_token), 'sk': 'VALUE'}
    seed({**reset_key, 'ownerId': owner['organizer']['id'], 'email': owner['organizer']['email'], 'authVersion': 0, 'expiresAt': int(time.time()) + 900})
    replacement = secrets.token_urlsafe(24)
    status, _ = request('/platform/password-reset/complete', {'token': reset_token, 'password': replacement})
    assert status == 200, ('reset', status)
    assert request('/platform/password-reset/complete', {'token': reset_token, 'password': replacement})[0] == 400
    assert request('/platform/organizer/events', token=owner['token'])[0] == 401
    assert request('/platform/organizer/events', token=staff['token'])[0] == 200
    status, new_login = request('/platform/login', {'email': owner['organizer']['email'], 'password': replacement})
    assert status == 200
    owner['token'] = new_login['token']
    # Unknown reserved-domain email exercises async Lambda permission without sending mail.
    unknown = 'eventflow-features-unknown-' + secrets.token_hex(8) + '@example.test'
    assert request('/platform/password-reset', {'email': unknown})[0] == 200
    print('PASS: live atomic capacity, duplicate rollback, scanner-only access and revocation, reset one-use token and session revocation; async reset accepted. No emails sent.', flush=True)
finally:
    for event in scope['events']:
        token = accounts[0]['token'] if accounts else None
        status, result = request('/platform/organizer/events/' + event['id'] + '/delete', {'confirmation': event['name']}, token)
        assert status == 200 and result['status'] == 'deleted', 'QA event cleanup needs review'
    ids = {a['id'] for a in scope['accounts'] if a['id']}
    keys = list(scope['keys'])
    keys.extend({'pk': item['pk'], 'sk': item['sk']} for item in scan() if item['pk'].startswith(('PLATFORM_SESSION#', 'STAFF#')) and (item.get('ownerId') in ids or item.get('id') in ids))
    keys.extend({'pk': 'ORGANIZER_EMAIL#' + digest(a['email']), 'sk': 'VALUE'} for a in scope['accounts'])
    with table.batch_writer() as batch:
        for item_key in {tuple(sorted(k.items())) for k in keys}: batch.delete_item(Key=dict(item_key))
    assert snapshot(scan()) == before, 'Other application records changed or QA records remain'
    manifest.unlink()
    print('PASS: QA records removed; all pre-existing application records unchanged. Rate controls preserved.', flush=True)
