"""Free lifecycle checks; no AWS writes, Lambda calls or outgoing mail."""
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import check_platform as base
import event_management as m
h, p = base.h, base.p
h.table = h.ddb = base.Store()
h._config = {'sessionSecret': 'fictional-test-only'}
os.environ['FRONTEND_ORIGIN'] = 'https://eventflow-qr.vercel.app'
with patch('time.time', return_value=base.now), patch.object(m, 'kick_mail'):
    auth = p.auth(h, {'name': 'Owner', 'email': 'manager@example.test', 'password': 'fictional-password'}, 'manager', True)
    owner, _ = p.authorize(h, {'authorization': 'Bearer ' + auth['token']})
    draft = {**base.draft, 'capacity': 1, 'waitlist': True,
             'tickets': [{'id': 'standard', 'name': 'General', 'capacity': 1}, {'id': 'curated', 'name': 'Curated', 'capacity': 1, 'requireApproval': True}],
             'questions': [{'id': 'interest', 'label': 'What interests you?', 'type': 'choice', 'required': True, 'choices': ['Code', 'Design']}]}
    event = p.get_event(h, p.save_event(h, draft, owner)['id'])
    os.environ['BRAND_BUCKET'] = 'fictional-brand-bucket'
    with patch.object(h.boto3, 'client') as client:
        client.return_value.generate_presigned_url.return_value = 'https://example.test/private-brand'
        for branding in ({'coverKey': 'cover.png'}, {'coverKey': 'cover.png', 'logoKey': 'logo.png'}):
            result = p.public_event(h, {**event, **branding}, True)
            assert result['cover'] == 'https://example.test/private-brand'
            assert result['logo'] == ('https://example.test/private-brand' if branding.get('logoKey') else '')
    person = {**base.attendee, 'ticketId': 'standard', 'answers': {'interest': 'Code'}}
    base.rejected(lambda: m.register(h, event, {**person, 'answers': {}}, 'missing'), 400)
    base.rejected(lambda: m.register(h, event, {**person, 'answers': {'interest': 'Other'}}, 'choice'), 400)
    a = m.register(h, event, person, 'a')
    assert a['registrationStatus'] == 'approved' and a['token'] and a['accessToken']
    b = m.register(h, event, {**person, 'ticketId': 'curated', 'email': 'b@example.test'}, 'b')
    c = m.register(h, event, {**person, 'email': 'c@example.test'}, 'c')
    assert b['registrationStatus'] == c['registrationStatus'] == 'waitlisted' and not b['token'] and not c['token']
    assert p.public_event(h, event)['registered'] == 1
    assert p.checkin(h, event, {'token': b['id']}) == {'status': 'invalid_pass'}
    base.rejected(lambda: p.dispatch(h, 'POST', '/platform/organizer/events/' + event['id'] + '/pass/' + b['id'], {'identityConfirmed': True}, {'authorization': 'Bearer ' + auth['token']}, 'ip'), 404)
    pending = m.open_registration(h, {'token': b['accessToken']}, 'open')
    assert pending['attendee']['registrationStatus'] == 'waitlisted' and not pending['attendee']['token']
    base.rejected(lambda: m.open_registration(h, {'token': b['email']}, 'email-only'), 404)
    base.rejected(lambda: m.change_guest(h, event, h.read(p.attendee_key(h, event['id'], b['id'])), 'approve'), 409)
    m.change_guest(h, event, h.read(p.attendee_key(h, event['id'], a['id'])), 'cancel')
    assert p.public_event(h, event)['registered'] == 0
    assert p.checkin(h, event, {'token': a['token']}) == {'status': 'invalid_pass'}
    def approve(person):
        try: return m.change_guest(h, event, h.read(p.attendee_key(h, event['id'], person['id'])), 'approve')
        except h.Problem as issue: assert issue.status == 409
    with ThreadPoolExecutor(max_workers=2) as pool: approved = list(pool.map(approve, [b, c]))
    assert sum(bool(result) for result in approved) == 1 and p.public_event(h, event)['registered'] == 1
    issued = next(result for result in approved if result)
    assert issued['token']
    assert p.checkin(h, event, {'token': issued['token']})['status'] == 'checked_in'
    base.rejected(lambda: m.change_guest(h, event, h.read(p.attendee_key(h, event['id'], issued['id'])), 'cancel'), 409)
    # Capacity edits/ticket removal preserve pending and historical registrations.
    base.rejected(lambda: p.save_event(h, {**draft, 'tickets': [draft['tickets'][0]]}, owner, event['id']), 409)
    m.communications(h, event, owner, {'action': 'invite', 'emails': ['invite@example.test']}, 'POST')
    jobs = p.query(h, 'EVENT#' + event['id'], 'MAIL#')
    invitation = next(j for j in jobs if j['kind'] == 'invitation')
    token = invitation['body'].split('/#guest-invite/')[1].split('\n')[0]
    assert m.open_invitation(h, {'token': token}, 'invite-open')['email'] == 'invite@example.test'
    base.rejected(lambda: m.register(h, event, {**person, 'email': 'wrong@example.test', 'invitationToken': token}, 'wrong-invite'), 400)
    m.communications(h, event, owner, {'subject': 'Important update', 'message': 'A multiline\nannouncement', 'recipients': [b['id'], c['id']]}, 'POST')
    before = len(p.query(h, 'EVENT#' + event['id'], 'MAIL#'))
    base.rejected(lambda: m.communications(h, event, owner, {'subject': 'Bad\nHeader', 'message': 'x', 'recipients': [b['id']]}, 'POST'), 400)
    base.rejected(lambda: m.communications(h, event, owner, {'subject': 'x', 'message': 'x', 'recipients': ['EF-FFFFFFFFFF']}, 'POST'), 400)
    assert len(p.query(h, 'EVENT#' + event['id'], 'MAIL#')) == before
    history = m.communications(h, event, owner, {}, 'GET')
    assert all('body' not in item for item in history['messages'])
    # A worker claims each recipient once, records acceptance/failure and never revives deleted records.
    with patch.object(p, 'send_link') as send, patch.object(m, 'test_recipient', return_value=False):
        while any(j['mailState'] == 'queued' for j in p.query(h, 'EVENT#' + event['id'], 'MAIL#')):
            m.mail_worker(h, event['id'])
        assert send.call_count == before
        m.mail_worker(h, event['id']); assert send.call_count == before
    reserved = m.mail_item(h, event, 'safe@example.test', 'Reserved address', 'No outbound message')
    h.table.put_item(Item=reserved)
    with patch.object(p, 'send_link') as send:
        m.mail_worker(h, event['id'])
        assert not send.called and h.read(h.key(reserved['pk'], reserved['sk']))['mailState'] == 'skipped_test_recipient'
    assert p.delete_event(h, event, owner, {'confirmation': event['name']})['status'] == 'deleted'
    assert not any(k[0].startswith(('REGISTRATION_ACCESS#', 'GUEST_INVITE#')) for k in h.table.items)
    assert not p.query(h, 'EVENT#' + event['id'])
    private = p.get_event(h, p.save_event(h, {**base.draft, 'visibility': 'unlisted'}, owner)['id'])
    assert not p.dispatch(h, 'GET', '/platform/events', {}, {}, 'ip')[0]['events']
    assert p.public_event(h, private)['visibility'] == 'unlisted'
print('PASS: free ticket validation, answers, waitlist/no premature QR, capacity-safe concurrent approvals, cancellation/check-in race protection, invitations, announcements, worker deduplication, unlisted discovery and complete deletion. No email or AWS writes.')
