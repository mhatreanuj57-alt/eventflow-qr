"""Feature security/concurrency checks; mocked AWS and mail, no external writes."""
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock
import check_platform as base

h, p = base.h, base.p
h.table = h.ddb = base.Store()
h._config = {'sessionSecret': 'local-test-only'}
os.environ.update(FRONTEND_ORIGIN='https://eventflow-qr.vercel.app', AWS_LAMBDA_FUNCTION_NAME='test')

with patch('time.time', return_value=base.now):
    first = p.auth(h, {'name': 'Owner', 'email': 'owner@example.test', 'password': 'fictional-password'}, 'owner', True)
    second = p.auth(h, {'name': 'Volunteer', 'email': 'volunteer@example.test', 'password': 'fictional-password'}, 'staff', True)
    owner, _ = p.authorize(h, {'authorization': 'Bearer ' + first['token']})
    volunteer, _ = p.authorize(h, {'authorization': 'Bearer ' + second['token']})
    event = p.get_event(h, p.save_event(h, {**base.draft, 'capacity': '2'}, owner)['id'])
    def register(index):
        try: return p.register(h, event, {**base.attendee, 'email': f'capacity-{index}@example.test'}, 'capacity')
        except h.Problem as issue: assert issue.status == 409
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(register, range(8)))
    assert len([r for r in results if r]) == 2
    assert p.public_event(h, event)['registered'] == 2 and p.public_event(h, event)['status'] == 'full'
    assert len(p.query(h, 'EVENT#' + event['id'], 'ATTENDEE#')) == 2
    base.rejected(lambda: p.save_event(h, {**base.draft, 'capacity': '1'}, owner, event['id']), 409)
    event = p.get_event(h, p.save_event(h, {**base.draft, 'capacity': '3'}, owner, event['id'])['id'])
    person = next(r for r in results if r)
    base.rejected(lambda: p.register(h, event, {**base.attendee, 'email': person['email']}, 'duplicate'), 409)
    assert p.public_event(h, event)['registered'] == 2  # Duplicate transaction does not consume a place.
    for capacity in (-1, True, False, 1.5, '0', '1000000', 'x'):
        base.rejected(lambda: p.validate_event(h, {**base.draft, 'capacity': capacity}), 400)
    # Events created before the feature retain existing registrations on first use.
    event = p.get_event(h, p.save_event(h, {**base.draft, 'capacity': ''}, owner, event['id'])['id'])
    h.table.delete_item(Key=h.key('EVENT#' + event['id'], 'SEATS'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(pool.map(register, range(20, 22)))
    assert p.public_event(h, event)['registered'] == 4
    owner_headers = {'authorization': 'Bearer ' + first['token']}
    staff_headers = {'authorization': 'Bearer ' + second['token']}
    path = '/platform/organizer/events/' + event['id']
    with patch.object(p, 'send_link') as mail:
        p.staff(h, event, owner, {'email': 'volunteer@example.test'}, 'POST')
        body = mail.call_args.args[3]
        token = body.split('/#invite/')[1].split('\n')[0]
    base.rejected(lambda: p.accept_invite(h, owner, {'token': token}), 400)
    p.accept_invite(h, volunteer, {'token': token})
    base.rejected(lambda: p.accept_invite(h, volunteer, {'token': token}), 400)
    listed, _ = p.dispatch(h, 'GET', '/platform/organizer/events', {}, staff_headers, 'ip')
    assert len(listed['events']) == 1 and listed['events'][0]['role'] == 'scanner' and 'owner' not in listed['events'][0]
    assert p.dispatch(h, 'POST', path + '/checkins', {'token': person['token']}, staff_headers, 'ip')[0]['status'] == 'checked_in'
    for suffix, method, data in [('attendance', 'GET', {}), ('pass/' + person['id'], 'POST', {'identityConfirmed': True}), ('staff', 'GET', {}), ('', 'POST', base.draft), ('delete', 'POST', {'confirmation': event['name']})]:
        base.rejected(lambda: p.dispatch(h, method, path + ('/' + suffix if suffix else ''), data, staff_headers, 'ip'), 404)
    p.staff(h, event, owner, {'action': 'revoke', 'id': volunteer['ownerId']}, 'POST')
    base.rejected(lambda: p.dispatch(h, 'POST', path + '/checkins', {'token': person['token']}, staff_headers, 'ip'), 404)
    with patch.object(p, 'send_link', side_effect=OSError('mock failure')):
        base.rejected(lambda: p.staff(h, event, owner, {'email': 'failed@example.test'}, 'POST'), 503)
    assert not p.query(h, 'EVENT#' + event['id'], 'INVITE#')
    with patch.object(p, 'send_link') as mail:
        p.staff(h, event, owner, {'email': volunteer['email']}, 'POST')
        token = mail.call_args.args[3].split('/#invite/')[1].split('\n')[0]
    with patch('time.time', return_value=base.now + 86401):
        base.rejected(lambda: p.accept_invite(h, volunteer, {'token': token}), 400)
    p.accept_invite(h, volunteer, {'token': token})
    with patch.object(p, 'send_link'):
        p.staff(h, event, owner, {'email': 'pending@example.test'}, 'POST')
    assert p.delete_event(h, event, owner, {'confirmation': event['name']})['status'] == 'deleted'
    assert not p.query(h, 'EVENT#' + event['id']) and not p.query(h, 'STAFF#' + volunteer['ownerId'])
    assert not any(k[0].startswith('STAFF_INVITE#') for k in h.table.items)

    with patch.object(h.boto3, 'client', return_value=MagicMock()) as client:
        known = p.request_reset(h, {'email': owner['email']}, 'reset-known')
        unknown = p.request_reset(h, {'email': 'unknown@example.test'}, 'reset-unknown')
        assert known == unknown and client.return_value.invoke.call_count == 2
        assert client.return_value.invoke.call_args.kwargs['InvocationType'] == 'Event'
    with patch.object(p, 'send_link') as mail:
        p.reset_mail_job(h, owner['email'])
        reset_token = mail.call_args.args[3].split('/#reset/')[1].split('\n')[0]
        p.reset_mail_job(h, 'unknown@example.test')
        assert mail.call_count == 1
    assert not any(reset_token in str(value) for value in h.table.items.values())
    with patch('time.time', return_value=base.now + 901):
        base.rejected(lambda: p.reset_password(h, {'token': reset_token, 'password': 'replacement-password'}, 'expired'), 400)
    def reset(index):
        try: return p.reset_password(h, {'token': reset_token, 'password': 'replacement-password'}, f'reset-{index}')
        except h.Problem as issue: assert issue.status == 400
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(bool(r) for r in pool.map(reset, range(2))) == 1
    base.rejected(lambda: p.authorize(h, owner_headers), 401)
    p.authorize(h, staff_headers)  # Other organizer sessions survive.
    base.rejected(lambda: p.auth(h, {'email': owner['email'], 'password': 'fictional-password'}, 'old-password', False), 401)
    login = p.auth(h, {'email': owner['email'], 'password': 'replacement-password'}, 'new-password', False)
    p.authorize(h, {'authorization': 'Bearer ' + login['token']})
    base.rejected(lambda: p.reset_password(h, {'token': reset_token, 'password': 'replacement-password'}, 'reuse'), 400)
print('PASS: atomic capacity and duplicate rollback; scanner-only invitations, expiry, revocation and deletion; non-enumerating asynchronous reset, one-use token, expiry and session revocation. No AWS or email writes.')
