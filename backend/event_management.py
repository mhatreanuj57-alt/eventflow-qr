"""Free-ticket lifecycle and organizer communication, using the existing table/mail sender."""
import json
import os
import re
import secrets
import time
from datetime import datetime
from decimal import Decimal
from botocore.exceptions import ClientError
import platform_api as p


def number(h, value):
    if value in ('', None) or isinstance(value, (int, Decimal)) and not isinstance(value, bool) and value == 0:
        return 0
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)) or not re.fullmatch(r'[1-9][0-9]{0,5}', str(value)):
        raise h.Problem(400, 'Use a whole-number capacity from 1 to 999999, or blank for unlimited')
    return int(value)


def ticket_types(event):
    return event.get('tickets') or [{'id': 'standard', 'name': 'Standard', 'description': '', 'capacity': 0, 'requireApproval': False, 'hidden': False, 'opensAt': int(event['opensAt']), 'closesAt': int(event['closesAt'])}]


def full(h, event, seats=None):
    seats = seats if seats is not None else h.read(h.key('EVENT#' + event['id'], 'SEATS')) or {}
    if seats.get('capacity') and seats.get('registered', 0) >= seats['capacity']:
        return True
    available = [t for t in ticket_types(event) if not t['hidden'] and int(t['opensAt']) <= time.time() < int(t['closesAt'])]
    return bool(available) and all(t['capacity'] and seats.get('tickets', {}).get(t['id'], {}).get('registered', 0) >= t['capacity'] for t in available)


def validate(h, data, event):
    options = {'waitlist': False, 'visibility': 'public', 'category': '', 'city': '', 'questions': []}
    for field in ('waitlist',):
        value = data.get(field, options[field])
        if type(value) is not bool:
            raise h.Problem(400, 'Check registration settings')
        options[field] = value
    options['visibility'] = data.get('visibility', 'public')
    if options['visibility'] not in ('public', 'unlisted'):
        raise h.Problem(400, 'Choose public or unlisted visibility')
    for field in ('category', 'city'):
        options[field] = h.text_field(data, field, 80)
    raw = data.get('tickets') or ticket_types(event)
    if not isinstance(raw, list) or not 1 <= len(raw) <= 8:
        raise h.Problem(400, 'Add between one and eight free ticket types')
    tickets, ids = [], set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise h.Problem(400, 'Invalid ticket type')
        ticket = {field: h.text_field(entry, field, maximum, True) for field, maximum in (('id', 40), ('name', 80))}
        if not re.fullmatch(r'[a-z0-9-]{1,40}', ticket['id']) or ticket['id'] in ids:
            raise h.Problem(400, 'Ticket identifiers must be unique')
        ids.add(ticket['id'])
        ticket.update(description=h.text_field(entry, 'description', 300), capacity=number(h, entry.get('capacity')))
        for field in ('requireApproval', 'hidden'):
            if type(entry.get(field, False)) is not bool:
                raise h.Problem(400, 'Invalid ticket setting')
            ticket[field] = entry.get(field, False)
        for field, bound in (('registrationOpen', 'opensAt'), ('registrationClose', 'closesAt')):
            value = entry.get(field)
            if value:
                try:
                    stamp = datetime.fromisoformat(h.text_field(entry, field, 40, True))
                    if not stamp.utcoffset() or stamp.utcoffset().total_seconds() != 19800:
                        raise ValueError()
                    ticket[field] = stamp.isoformat()
                    ticket[bound] = int(stamp.timestamp())
                except ValueError:
                    raise h.Problem(400, 'Use valid India-time ticket dates') from None
            else:
                ticket[bound] = int(event[bound])
        if ticket['opensAt'] < int(event['opensAt']) or ticket['closesAt'] > int(event['closesAt']) or ticket['closesAt'] <= ticket['opensAt']:
            raise h.Problem(400, 'Ticket availability must fit inside the event registration window')
        tickets.append(ticket)
    if all(t['hidden'] for t in tickets):
        raise h.Problem(400, 'Keep at least one ticket type visible')
    options['tickets'] = tickets
    questions = data.get('questions', [])
    if not isinstance(questions, list) or len(questions) > 8:
        raise h.Problem(400, 'Use up to eight custom questions')
    ids = set()
    for entry in questions:
        if not isinstance(entry, dict):
            raise h.Problem(400, 'Invalid registration question')
        question = {field: h.text_field(entry, field, maximum, True) for field, maximum in (('id', 40), ('label', 150))}
        if not re.fullmatch(r'[a-z0-9-]{1,40}', question['id']) or question['id'] in ids or type(entry.get('required', False)) is not bool:
            raise h.Problem(400, 'Invalid registration question')
        ids.add(question['id'])
        question.update(required=entry.get('required', False), type=entry.get('type', 'text'))
        if question['type'] not in ('text', 'choice'):
            raise h.Problem(400, 'Question type must be text or choice')
        choices = entry.get('choices', [])
        if question['type'] == 'choice':
            if not isinstance(choices, list) or not 2 <= len(choices) <= 12:
                raise h.Problem(400, 'A choice question needs 2–12 options')
            choices = [h.text_field({'choice': c}, 'choice', 100, True) for c in choices]
            if len(set(choices)) != len(choices):
                raise h.Problem(400, 'Choice options must be unique')
            question['choices'] = choices
        options['questions'].append(question)
    return options


def seats_for(event, previous, people):
    registered = int(previous.get('registered', sum(person.get('registrationStatus', 'approved') == 'approved' for person in people)))
    old = previous.get('tickets') or {'standard': {'registered': registered}}
    tickets = {ticket['id']: {'registered': int(old.get(ticket['id'], {}).get('registered', 0)), 'capacity': int(ticket['capacity'])} for ticket in ticket_types(event)}
    return {'registered': registered, 'capacity': int(event.get('capacity', previous.get('capacity', 0))), 'revision': int(previous.get('revision', 0)), 'tickets': tickets}


def counter(h, event, ticket_id, delta, available=False):
    names = {'#tickets': 'tickets', '#ticket': ticket_id, '#capacity': 'capacity', '#revision': 'revision'}
    values = {':zero': 0, ':one': 1}
    condition = 'attribute_exists(pk) AND attribute_exists(#tickets.#ticket)'
    update = 'SET #revision = if_not_exists(#revision, :zero) + :one'
    if delta:
        values[':delta'] = delta
        update += ', registered = registered + :delta, #tickets.#ticket.registered = #tickets.#ticket.registered + :delta'
        if delta < 0:
            condition += ' AND registered > :zero AND #tickets.#ticket.registered > :zero'
    if delta > 0 or available:
        condition += ' AND (#capacity = :zero OR registered < #capacity) AND (#tickets.#ticket.#capacity = :zero OR #tickets.#ticket.registered < #tickets.#ticket.#capacity)'
    else:
        names.pop('#capacity')
    return {'Update': {'TableName': h.table.name, 'Key': p.encoded(h.key('EVENT#' + event['id'], 'SEATS')), 'UpdateExpression': update,
                       'ConditionExpression': condition, 'ExpressionAttributeNames': names, 'ExpressionAttributeValues': p.encoded(values)}}


def ensure_seats(h, event):
    item_key = h.key('EVENT#' + event['id'], 'SEATS')
    previous = h.read(item_key) or {}
    if previous.get('tickets'):
        return
    people = p.query(h, 'EVENT#' + event['id'], 'ATTENDEE#')
    seats = seats_for(event, previous, people)
    seats.update(item_key)
    action = p.put(h, seats)
    if previous:
        action['Put'].update(ConditionExpression='registered = :registered', ExpressionAttributeValues=p.encoded({':registered': int(previous['registered'])}))
    try:
        p.transaction(h, [p.event_gate(h, event), action])
    except ClientError as error:
        if error.response['Error']['Code'] == 'TransactionCanceledException':
            if (h.read(item_key) or {}).get('tickets'):
                return
            raise h.Problem(409, 'This event changed. Refresh and try again.') from None
        raise


def mail_item(h, event, email, subject, body, kind='status'):
    job_id = secrets.token_hex(16)
    return {**h.key('EVENT#' + event['id'], 'MAIL#' + job_id), 'id': job_id, 'eventId': event['id'], 'email': email,
            'subject': subject, 'body': body, 'kind': kind, 'mailState': 'queued', 'createdAt': h.stamp()}


def kick_mail(h, event_id):
    try:
        h.boto3.client('lambda', region_name=h.REGION).invoke(FunctionName=os.environ['AWS_LAMBDA_FUNCTION_NAME'], InvocationType='Event', Payload=json.dumps({'emailJob': 'event-mail', 'eventId': event_id}).encode())
    except Exception:
        print('event_mail_queue_pending')  # Persisted jobs can be resumed from Communications.


def mail_worker(h, event_id):
    event = h.read(p.event_key(h, event_id))
    if not event or event.get('deleting'):
        return
    # ponytail: query this event's mail history; add a queued-mail index when volume grows.
    jobs = [job for job in p.query(h, 'EVENT#' + event_id, 'MAIL#') if job['mailState'] == 'queued']
    if not jobs:
        return
    job = sorted(jobs, key=lambda j: (j['createdAt'], j['id']))[0]
    job_key = h.key(job['pk'], job['sk'])
    try:
        h.table.update_item(Key=job_key, UpdateExpression='SET mailState = :sending', ConditionExpression='attribute_exists(pk) AND mailState = :queued', ExpressionAttributeValues={':sending': 'sending', ':queued': 'queued'})
    except ClientError as error:
        if error.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return
        raise
    try:
        if test_recipient(job['email']):
            result = 'skipped_test_recipient'
        else:
            p.send_link(h, job['email'], job['subject'], job['body'])
            result = 'accepted'
    except Exception:
        result = 'failed'
        print('event_mail_send_failed')
    try:
        h.table.update_item(Key=job_key, UpdateExpression='SET mailState = :state', ConditionExpression='attribute_exists(pk)', ExpressionAttributeValues={':state': result})
    except ClientError as error:
        if error.response['Error']['Code'] != 'ConditionalCheckFailedException':
            raise
    if len(jobs) > 1:
        kick_mail(h, event_id)


def test_recipient(email):
    domain = email.rsplit('@', 1)[-1].lower()
    return domain.endswith(('.test', '.invalid')) or domain in ('example.com', 'example.net', 'example.org')


def status_mail(h, event, person):
    status = person['registrationStatus']
    url = os.environ.get('FRONTEND_ORIGIN', 'https://eventflow-qr.vercel.app') + '/#registration/' + person['accessToken']
    return mail_item(h, event, person['email'], 'Your EventFlow QR registration — ' + event['name'],
                     f'{person["name"]}, your registration for {event["name"]} is {status}.\nRegistration ID: {person["id"]}\n\nCheck your status and, once approved, save your QR pass:\n{url}\n\nPending/waitlisted registrations do not grant entry.')


def register(h, event, data, ip):
    if p.state(event) != 'open':
        raise h.Problem(409, 'Registration is not open for this event')
    person = h.validate_registration(data)
    h.throttle('platform-registration', ip, 100, 3600)
    ensure_seats(h, event)
    types = ticket_types(event)
    ticket_id = data.get('ticketId', types[0]['id'] if len(types) == 1 else '')
    ticket = next((t for t in types if t['id'] == ticket_id), None)
    now = int(time.time())
    if not ticket or ticket.get('hidden') or not int(ticket['opensAt']) <= now < int(ticket['closesAt']):
        raise h.Problem(409, 'Choose an available ticket type')
    answers = data.get('answers', {})
    if not isinstance(answers, dict) or len(answers) > 8:
        raise h.Problem(400, 'Check your registration answers')
    person['answers'] = {}
    for question in event.get('questions', []):
        answer = h.text_field(answers, question['id'], 500, question['required'])
        if answer and question['type'] == 'choice' and answer not in question['choices']:
            raise h.Problem(400, 'Choose a valid answer for ' + question['label'])
        person['answers'][question['id']] = answer
    invited, invitation_keys = False, []
    if data.get('invitationToken'):
        token = h.text_field(data, 'invitationToken', 100, True)
        invitation_key = h.key('GUEST_INVITE#' + h.digest(token))
        invitation = h.read(invitation_key)
        if not invitation or invitation['eventId'] != event['id'] or invitation['email'] != person['email'] or int(invitation['expiresAt']) <= now:
            raise h.Problem(400, 'This guest invitation is invalid, expired, or belongs to another email')
        invited = True
        invitation_keys = [invitation_key, h.key('EVENT#' + event['id'], 'GUEST_INVITE#' + h.digest(token))]
    person.update(id='EF-' + secrets.token_hex(5).upper(), token=secrets.token_urlsafe(32), accessToken=secrets.token_urlsafe(32), createdAt=h.stamp(), eventId=event['id'],
                  ticketId=ticket_id, ticketName=ticket['name'], registrationStatus='pending' if ticket['requireApproval'] and not invited else 'approved', revision=0)
    person.update(p.attendee_key(h, event['id'], person['id']))
    person['issuedAt'] = person['createdAt'] if person['registrationStatus'] == 'approved' else ''
    access_key = h.key('REGISTRATION_ACCESS#' + h.digest(person['accessToken']))
    access = {**access_key, 'eventId': event['id'], 'id': person['id'], 'expiresAt': int(datetime.fromisoformat(event['end']).timestamp()) + 2592000}
    gate = {'ConditionCheck': {'TableName': h.table.name, 'Key': p.encoded(p.event_key(h, event['id'])),
            'ConditionExpression': '#version = :version AND opensAt <= :now AND closesAt > :now', 'ExpressionAttributeNames': {'#version': 'version'}, 'ExpressionAttributeValues': p.encoded({':version': int(event['version']), ':now': now})}}
    def actions():
        items = [person, {**h.key('EMAIL#' + event['id'] + '#' + h.digest(person['email'])), 'id': person['id']}, access]
        if person['registrationStatus'] == 'approved':
            items.append({**h.key('TOKEN#' + h.digest(person['token'])), 'id': person['id'], 'eventId': event['id']})
        else:
            items.append(status_mail(h, event, person))
        return [gate, counter(h, event, ticket_id, 1 if person['registrationStatus'] == 'approved' else 0, person['registrationStatus'] != 'waitlisted')] + [p.put(h, item) for item in items] + [
            {'Delete': {'TableName': h.table.name, 'Key': p.encoded(k), 'ConditionExpression': 'expiresAt > :now', 'ExpressionAttributeValues': p.encoded({':now': now})}} for k in invitation_keys]
    for attempt in range(2):
        try:
            p.transaction(h, actions())
            if person['registrationStatus'] != 'approved':
                kick_mail(h, event['id'])
            return h.public_person(person, True)
        except ClientError as error:
            if error.response['Error']['Code'] != 'TransactionCanceledException':
                raise
            reasons = error.response.get('CancellationReasons', [])
            # Check uniqueness first even if a full-capacity condition failed too.
            if h.read(h.key('EMAIL#' + event['id'] + '#' + h.digest(person['email']))):
                raise h.Problem(409, 'Already registered—use your saved pass or status link. Ask an organizer for help if it is lost.') from None
            if reasons and reasons[0].get('Code') == 'ConditionalCheckFailed':
                raise h.Problem(409, 'The event or registration window changed. Refresh and try again.') from None
            if len(reasons) > 1 and reasons[1].get('Code') == 'ConditionalCheckFailed':
                if attempt == 0 and event.get('waitlist') and full(h, event):
                    person.update(registrationStatus='waitlisted', issuedAt='')
                    continue
                raise h.Problem(409, 'This ticket or event is full. No places remain.') from None
            raise h.Problem(409, 'The registration changed. Refresh and try again.') from None


def change_guest(h, event, person, action):
    previous = person.get('registrationStatus', 'approved')
    if action not in ('approve', 'decline', 'cancel') or person.get('checkedInAt'):
        raise h.Problem(409, 'This registration cannot be changed after check-in')
    if action == 'approve' and previous not in ('pending', 'waitlisted'):
        raise h.Problem(409, 'Only pending or waitlisted guests can be approved')
    if action != 'approve' and previous not in ('approved', 'pending', 'waitlisted'):
        raise h.Problem(409, 'This registration has already been closed')
    if action == 'approve' and time.time() >= datetime.fromisoformat(event['end']).timestamp():
        raise h.Problem(409, 'This event has ended')
    ensure_seats(h, event)
    status = {'approve': 'approved', 'decline': 'declined', 'cancel': 'cancelled'}[action]
    changed = {**person, 'registrationStatus': status, 'revision': int(person.get('revision', 0)) + 1}
    if status == 'approved':
        changed['issuedAt'] = h.stamp()
    # Legacy immediate registrations have no public management capability until requested by an owner.
    access = []
    if not changed.get('accessToken'):
        changed['accessToken'] = secrets.token_urlsafe(32)
        access.append(p.put(h, {**h.key('REGISTRATION_ACCESS#' + h.digest(changed['accessToken'])), 'id': person['id'], 'eventId': event['id'], 'expiresAt': int(datetime.fromisoformat(event['end']).timestamp()) + 2592000}))
    update = p.put(h, changed, 'attribute_exists(pk) AND (attribute_not_exists(#revision) OR #revision = :previous) AND attribute_not_exists(checkedInAt)')
    update['Put'].update(ExpressionAttributeNames={'#revision': 'revision'}, ExpressionAttributeValues=p.encoded({':previous': int(person.get('revision', 0))}))
    delta = (1 if status == 'approved' else 0) - (1 if previous == 'approved' else 0)
    token_key = h.key('TOKEN#' + h.digest(person['token']))
    token_action = p.put(h, {**token_key, 'id': person['id'], 'eventId': event['id']}) if status == 'approved' else {'Delete': {'TableName': h.table.name, 'Key': p.encoded(token_key)}}
    try:
        p.transaction(h, [p.event_gate(h, event), counter(h, event, person.get('ticketId', 'standard'), delta), update, token_action, p.put(h, status_mail(h, event, changed))] + access)
    except ClientError as error:
        if error.response['Error']['Code'] == 'TransactionCanceledException':
            raise h.Problem(409, 'Capacity or registration changed. Refresh and try again.') from None
        raise
    kick_mail(h, event['id'])
    return h.public_person(changed, True)


def open_registration(h, data, ip, cancel=False):
    h.throttle('registration-access', ip, 60, 300)
    token = h.text_field(data, 'token', 100, True)
    lookup = h.read(h.key('REGISTRATION_ACCESS#' + h.digest(token)))
    if not lookup or int(lookup['expiresAt']) <= time.time():
        raise h.Problem(404, 'This registration link is invalid or expired')
    event = p.get_event(h, lookup['eventId'])
    person = h.read(p.attendee_key(h, event['id'], lookup['id']))
    if not person:
        raise h.Problem(404, 'Registration not found')
    if cancel:
        person = change_guest(h, event, person, 'cancel')
    else:
        person = h.public_person(person, True)
    return {'event': p.public_event(h, event), 'attendee': person}


def open_invitation(h, data, ip):
    h.throttle('guest-invite-access', ip, 60, 300)
    token = h.text_field(data, 'token', 100, True)
    invitation = h.read(h.key('GUEST_INVITE#' + h.digest(token)))
    if not invitation or int(invitation['expiresAt']) <= time.time():
        raise h.Problem(404, 'Invitation not found or expired')
    return {'email': invitation['email'], 'event': p.public_event(h, p.get_event(h, invitation['eventId']))}


def communications(h, event, owner, data, method):
    if method == 'GET':
        jobs = p.query(h, 'EVENT#' + event['id'], 'MAIL#')
        return {'messages': [{field: job.get(field, '') for field in ('id', 'email', 'subject', 'kind', 'mailState', 'createdAt')} for job in sorted(jobs, key=lambda j: j['createdAt'], reverse=True)[:100]]}
    h.throttle('event-communications', owner['ownerId'], 20, 3600)
    if data.get('action') == 'resume':
        kick_mail(h, event['id'])
        return {'message': 'Queued messages resumed. Check delivery status below.'}
    if data.get('action') == 'invite':
        emails = data.get('emails')
        if not isinstance(emails, list) or not 1 <= len(emails) <= 25:
            raise h.Problem(400, 'Invite 1–25 email addresses at a time')
        normalized = []
        for email in emails:
            email = h.text_field({'email': email}, 'email', 254, True).lower()
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
                raise h.Problem(400, 'Check invitation email addresses')
            if email not in normalized:
                normalized.append(email)
        if time.time() >= int(event['closesAt']):
            raise h.Problem(409, 'Registration has closed')
        queued = 0
        for email in normalized:
            if h.read(h.key('EMAIL#' + event['id'] + '#' + h.digest(email))):
                continue
            token = secrets.token_urlsafe(32)
            invitation = {'email': email, 'eventId': event['id'], 'expiresAt': int(event['closesAt'])}
            job = mail_item(h, event, email, 'You are invited — ' + event['name'], f'You are invited to {event["name"]}.\n{event["venue"]}\n{event["start"]} (India time)\n\nRegister with this email using your private invitation:\n{os.environ["FRONTEND_ORIGIN"]}/#guest-invite/{token}\n\nAn invitation does not reserve a place. Capacity and ticket availability still apply.', 'invitation')
            p.transaction(h, [p.event_gate(h, event), p.put(h, {**invitation, **h.key('GUEST_INVITE#' + h.digest(token))}), p.put(h, {**invitation, **h.key('EVENT#' + event['id'], 'GUEST_INVITE#' + h.digest(token))}), p.put(h, job)])
            queued += 1
        kick_mail(h, event['id'])
        return {'message': f'{queued} guest invitations queued. Already registered addresses were skipped.'}
    subject = h.text_field(data, 'subject', 120, True)
    body = data.get('message')
    if not isinstance(body, str) or not body.strip() or len(body) > 3000 or any(ord(c) < 32 and c not in '\n\r\t' for c in body):
        raise h.Problem(400, 'Write a message of 1–3000 characters')
    body = body.strip()
    recipients = data.get('recipients')
    if not isinstance(recipients, list) or not 1 <= len(recipients) <= 50 or any(not isinstance(i, str) or not re.fullmatch(r'EF-[A-F0-9]{10}', i) for i in recipients):
        raise h.Problem(400, 'Select 1–50 attendees for this announcement')
    people = []
    for registration_id in set(recipients):
        person = h.read(p.attendee_key(h, event['id'], registration_id))
        if not person:
            raise h.Problem(400, 'One of the selected attendees no longer exists')
        people.append(person)
    jobs = [mail_item(h, event, person['email'], subject, body + '\n\nFrom the organizer of ' + event['name'], 'announcement') for person in people]
    p.transaction(h, [p.event_gate(h, event)] + [p.put(h, job) for job in jobs])
    kick_mail(h, event['id'])
    return {'message': f'{len(jobs)} announcements queued. Check accepted/failed status below; inbox delivery is not guaranteed.'}
