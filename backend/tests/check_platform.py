"""Run with Python; thread-safe fake store, no AWS writes or email sending."""
import importlib.util
import os
import sys
import threading
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parents[1]))
os.environ.update(TABLE_NAME='test', SECRET_ARN='test')
with patch('boto3.resource'), patch('boto3.client'):
    import handler as h
import platform_api as p


def fail(code='ConditionalCheckFailedException', reasons=None):
    payload = {'Error': {'Code': code}}
    if reasons: payload['CancellationReasons'] = reasons
    return ClientError(payload, 'test')


class Store:
    name = 'test'
    def __init__(self): self.items, self.lock = {}, threading.RLock()
    def get_item(self, Key, **kwargs):
        with self.lock:
            item = self.items.get((Key['pk'], Key['sk']))
            return {'Item': dict(item)} if item else {}
    def put_item(self, Item, **kwargs):
        with self.lock:
            k = Item['pk'], Item['sk']
            if kwargs.get('ConditionExpression') and k in self.items: raise fail()
            self.items[k] = dict(Item)
    def delete_item(self, Key):
        with self.lock: self.items.pop((Key['pk'], Key['sk']), None)
    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, **kwargs):
        with self.lock:
            k = Key['pk'], Key['sk']; item = self.items.get(k)
            if 'checkedInAt' in UpdateExpression:
                if not item or item.get('checkedInAt') or item.get('registrationStatus', 'approved') != 'approved': raise fail()
                item['checkedInAt'] = ExpressionAttributeValues[':now']
            elif 'mailState' in UpdateExpression:
                if not item or ':queued' in ExpressionAttributeValues and item.get('mailState') != ExpressionAttributeValues[':queued']: raise fail()
                item['mailState'] = ExpressionAttributeValues.get(':sending', ExpressionAttributeValues.get(':state'))
            elif 'attempts' in UpdateExpression:
                item = self.items.setdefault(k, dict(Key))
                if item.get('attempts', 0) >= ExpressionAttributeValues[':limit']: raise fail()
                item['attempts'] = item.get('attempts', 0) + 1
            else: raise AssertionError('Unsupported fake update')
            return {'Attributes': dict(item)}
    def query(self, KeyConditionExpression, **kwargs):
        condition = KeyConditionExpression.get_expression()
        expression = condition['values']
        if condition['operator'] == '=':
            pk, prefix = expression[1], ''
        else:
            pk = expression[0].get_expression()['values'][1]
            prefix = expression[1].get_expression()['values'][1]
        items = [dict(v) for (k, sk), v in self.items.items() if k == pk and sk.startswith(prefix)]
        return {'Items': items[:kwargs.get('Limit', len(items))]}
    def transact_write_items(self, TransactItems):
        deserialize = TypeDeserializer().deserialize
        def decode(item): return {k: deserialize(v) for k, v in item.items()}
        with self.lock:
            reasons, additions, deletions = [], [], []
            for action in TransactItems:
                spec = next(iter(action.values()))
                item = decode(spec.get('Item', spec.get('Key')))
                current = self.items.get((item['pk'], item['sk']))
                condition = spec.get('ConditionExpression')
                values = decode(spec.get('ExpressionAttributeValues', {}))
                if condition is None: valid = True
                elif condition == 'attribute_not_exists(pk)': valid = not current
                elif condition == 'attribute_exists(pk)': valid = bool(current)
                elif condition == 'registered = :registered': valid = current and current['registered'] == values[':registered']
                elif condition == 'registered = :registered AND (attribute_not_exists(#revision) OR #revision = :revision)': valid = current and current['registered'] == values[':registered'] and current.get('revision', 0) == values[':revision']
                elif condition == 'attribute_exists(pk) AND (attribute_not_exists(#revision) OR #revision = :previous) AND attribute_not_exists(checkedInAt)': valid = current and current.get('revision', 0) == values[':previous'] and not current.get('checkedInAt')
                elif condition.startswith('attribute_exists(pk) AND attribute_exists(#tickets.#ticket)'):
                    ticket = current.get('tickets', {}).get(spec['ExpressionAttributeNames']['#ticket']) if current else None
                    valid = bool(ticket)
                    if '#capacity' in condition: valid = valid and (current['capacity'] == 0 or current['registered'] < current['capacity']) and (ticket['capacity'] == 0 or ticket['registered'] < ticket['capacity'])
                    if 'registered > :zero' in condition: valid = valid and current['registered'] > 0 and ticket['registered'] > 0
                elif condition == 'attribute_exists(pk) AND (#capacity = :zero OR registered < #capacity)': valid = current and (current['capacity'] == 0 or current['registered'] < current['capacity'])
                elif condition == 'attribute_not_exists(pk) OR attribute_exists(pk)': valid = True
                elif condition == 'expiresAt > :now': valid = current and current['expiresAt'] > values[':now']
                elif condition == '(attribute_not_exists(authVersion) OR authVersion = :version) AND id = :owner': valid = current and current.get('authVersion', 0) == values[':version'] and current['id'] == values[':owner']
                elif condition == '#version = :version AND attribute_not_exists(deleting)': valid = current and current['version'] == values[':version'] and not current.get('deleting')
                elif condition == '#version = :previous': valid = current and current['version'] == values[':previous']
                elif condition == '#version = :previous AND ownerId = :owner': valid = current and current['version'] == values[':previous'] and current['ownerId'] == values[':owner']
                else:
                    valid = current and current['version'] == values[':version'] and current['opensAt'] <= values[':now'] < current['closesAt']
                reasons.append({'Code': 'None' if valid else 'ConditionalCheckFailed'})
                if 'Put' in action: additions.append(item)
                if 'Update' in action:
                    changed = copy.deepcopy(current or item)
                    if '#revision' in spec['UpdateExpression']:
                        changed['revision'] = changed.get('revision', 0) + 1
                        if ':delta' in values:
                            changed['registered'] += values[':delta']
                            changed['tickets'][spec['ExpressionAttributeNames']['#ticket']]['registered'] += values[':delta']
                    else: changed['registered'] = changed.get('registered', 0) + 1
                    additions.append(changed)
                if 'Delete' in action: deletions.append((item['pk'], item['sk']))
            if any(r['Code'] != 'None' for r in reasons): raise fail('TransactionCanceledException', reasons)
            for item in additions: self.items[item['pk'], item['sk']] = dict(item)
            for item_key in deletions: self.items.pop(item_key, None)


h.table = h.ddb = Store()
h._config = {'sessionSecret': 'local-regression-only'}
now = 1791000000
with patch('time.time', return_value=now):
    first = p.auth(h, {'name': 'Host One', 'email': 'ONE@example.test', 'password': 'fictional-password-1'}, 'ip-one', True)
    second = p.auth(h, {'name': 'Host Two', 'email': 'two@example.test', 'password': 'fictional-password-2'}, 'ip-two', True)
    owner, session_key = p.authorize(h, {'authorization': 'Bearer ' + first['token']})
    other, _ = p.authorize(h, {'authorization': 'Bearer ' + second['token']})
    draft = {'name': 'Fictional Regression Event', 'host': 'Host One', 'venue': 'Test Venue', 'description': '',
             'start': '2026-10-06T10:00:00+05:30', 'end': '2026-10-06T17:00:00+05:30',
             'registrationOpen': '2026-10-01T10:00:00+05:30', 'registrationClose': '2026-10-05T18:00:00+05:30',
             'color': '#ff5100', 'layout': 'editorial', 'mark': 'EF', 'logo': ''}
    a = p.save_event(h, draft, owner)
    b = p.save_event(h, {**draft, 'host': 'Host Two'}, other)
    event = p.get_event(h, a['id'])
    listing, _ = p.dispatch(h, 'GET', '/platform/events', {}, {}, 'public-ip')
    assert len(listing['events']) == 2 and all('owner' not in e for e in listing['events'])
    assert 'demo' not in p.validate_event(h, {**draft, 'name': 'Builders Breakout', 'demo': True})
    assert 'demo' not in p.public_event(h, {**event, 'demo': True})
    attendee = {'name': 'Fictional Attendee', 'email': ' Test@example.test ', 'type': 'Professional', 'organization': 'Test Org'}
    def attempt(_):
        try: return p.register(h, event, attendee, 'register-ip')
        except h.Problem as error:
            assert error.status == 409 and 'token' not in error.message
    with ThreadPoolExecutor(max_workers=8) as pool: results = list(pool.map(attempt, range(8)))
    assert sum(bool(r) for r in results) == 1
    created = next(r for r in results if r)
    assert created['email'] == 'test@example.test' and created['token'] != created['id']
    other_pass = p.register(h, p.get_event(h, b['id']), attendee, 'another-ip')
    assert other_pass['id'] != created['id']
    assert p.checkin(h, event, {'token': other_pass['token']}) == {'status': 'wrong_event'}
    with ThreadPoolExecutor(max_workers=8) as pool: scans = list(pool.map(lambda _: p.checkin(h, event, {'token': created['token']}), range(8)))
    assert sum(r['status'] == 'checked_in' for r in scans) == 1
    assert len({r['attendee']['checkedInAt'] for r in scans}) == 1
    assert p.checkin(h, event, {'token': created['id'].lower()})['status'] == 'already_checked_in'
    assert p.checkin(h, event, {'token': 'nonsense'}) == {'status': 'invalid_pass'}
    headers = {'authorization': 'Bearer ' + first['token']}
    other_headers = {'authorization': 'Bearer ' + second['token']}
    def rejected(call, status):
        try: call(); raise AssertionError('Forbidden request succeeded')
        except h.Problem as error: assert error.status == status
    for suffix, method, body in (('attendance', 'GET', {}), ('checkins', 'POST', {'token': created['token']}), ('pass/' + created['id'], 'POST', {'identityConfirmed': True}), ('', 'POST', draft)):
        path = '/platform/organizer/events/' + a['id'] + ('/' + suffix if suffix else '')
        rejected(lambda: p.dispatch(h, method, path, body, other_headers, 'ip'), 404)
        rejected(lambda: p.dispatch(h, method, path, body, {}, 'ip'), 401)
    recovery = '/platform/organizer/events/' + a['id'] + '/pass/' + created['id']
    rejected(lambda: p.dispatch(h, 'POST', recovery, {}, headers, 'ip'), 400)
    recovered, _ = p.dispatch(h, 'POST', recovery, {'identityConfirmed': True}, headers, 'ip')
    assert recovered['token'] == created['token'] and recovered['checkedInAt'] == scans[0]['attendee']['checkedInAt']
    counts, _ = p.dispatch(h, 'GET', '/platform/organizer/events/' + a['id'] + '/attendance', {}, headers, 'ip')
    assert counts['counts'] == {'registered': 1, 'checkedIn': 1, 'notYetArrived': 0}
    closing = int(event['closesAt'])
    assert p.state(event, closing - 1) == 'open' and p.state(event, closing) == 'closed'
    assert p.state(event, closing + 899) == 'closed' and p.state(event, closing + 900) == 'hidden'
    with patch('time.time', return_value=closing): rejected(lambda: p.register(h, event, {**attendee, 'email': 'late@example.test'}, 'late'), 409)
    # A request that read an older event cannot register after its schedule changes.
    p.save_event(h, {**draft, 'registrationClose': '2026-10-04T18:00:00+05:30'}, owner, a['id'])
    rejected(lambda: p.register(h, event, {**attendee, 'email': 'stale@example.test'}, 'stale'), 409)
    login = p.auth(h, {'email': 'one@example.test', 'password': 'fictional-password-1'}, 'login', False)
    rejected(lambda: p.auth(h, {'email': 'one@example.test', 'password': 'wrong-password'}, 'wrong', False), 401)
    h.table.items[session_key['pk'], session_key['sk']]['expiresAt'] = now
    rejected(lambda: p.authorize(h, headers), 401)
    new_headers = {'authorization': 'Bearer ' + login['token']}
    p.dispatch(h, 'POST', '/platform/logout', {}, new_headers, 'ip')
    rejected(lambda: p.authorize(h, new_headers), 401)
    for bad in ({**draft, 'color': 'orange'}, {**draft, 'start': '2026-10-06T10:00'}, {**draft, 'registrationClose': draft['registrationOpen']}):
        rejected(lambda: p.validate_event(h, bad), 400)
    deleting_event = p.get_event(h, a['id'], owner)
    for index in range(55):
        p.register(h, deleting_event, {**attendee, 'email': f'delete-{index}@example.test'}, 'delete-test-ip')
    login = p.auth(h, {'email': 'one@example.test', 'password': 'fictional-password-1'}, 'delete-login', False)
    delete_headers = {'authorization': 'Bearer ' + login['token']}
    delete_path = '/platform/organizer/events/' + a['id'] + '/delete'
    body = {'confirmation': deleting_event['name']}
    rejected(lambda: p.dispatch(h, 'POST', delete_path, body, other_headers, 'ip'), 404)
    rejected(lambda: p.dispatch(h, 'POST', delete_path, body, {}, 'ip'), 401)
    rejected(lambda: p.dispatch(h, 'POST', delete_path, {'confirmation': 'wrong event'}, delete_headers, 'ip'), 400)
    with patch('time.monotonic', side_effect=[0, 10]):
        pending, _ = p.dispatch(h, 'POST', delete_path, body, delete_headers, 'ip')
    assert pending['status'] == 'deleting'
    rejected(lambda: p.get_event(h, a['id']), 404)
    rejected(lambda: p.register(h, deleting_event, {**attendee, 'email': 'race@example.test'}, 'race'), 409)
    result, _ = p.dispatch(h, 'POST', delete_path, body, delete_headers, 'ip')
    assert result['status'] == 'deleted'
    assert not p.query(h, 'EVENT#' + a['id'])
    assert not any(k[0].startswith('EMAIL#' + a['id']) for k in h.table.items)
    assert not any(v.get('eventId') == a['id'] for v in h.table.items.values())
    assert not h.read(h.key('PUBLIC#EVENTS', a['id']))
    assert not h.read(h.key('OWNER#' + owner['ownerId'], 'EVENT#' + a['id']))
    assert p.get_event(h, b['id']) and h.read(p.attendee_key(h, b['id'], other_pass['id']))
    assert p.authorize(h, delete_headers)[0]['ownerId'] == owner['ownerId']
print('PASS: organizers isolated; concurrent registration/check-in; recovery; deadline; expiry/logout; invalid inputs. No AWS data changed.')
print('PASS: owner-only confirmed deletion, blocked concurrent registration, resumable multi-batch cleanup and preservation of other events/accounts.')
