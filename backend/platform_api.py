"""Event-scoped routes; reuse the existing validation, SMTP and throttling helpers."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

serializer = TypeSerializer()
EVENT_FIELDS = ('id', 'name', 'host', 'venue', 'description', 'start', 'end',
                'registrationOpen', 'registrationClose', 'timezone', 'color', 'layout', 'mark', 'logoKey', 'visibility', 'category', 'city', 'waitlist', 'questions')


def encoded(item):
    return {k: serializer.serialize(v) for k, v in item.items()}


def put(h, item, condition='attribute_not_exists(pk)'):
    return {'Put': {'TableName': h.table.name, 'Item': encoded(item), 'ConditionExpression': condition}}


def transaction(h, actions):
    # DynamoDB can reject overlapping transactions; retry conflicts, never uniqueness failures.
    for attempt in range(4):
        try:
            return h.ddb.transact_write_items(TransactItems=actions)
        except ClientError as error:
            reasons = error.response.get('CancellationReasons', [])
            conflict = error.response['Error']['Code'] == 'TransactionConflictException' or any(r.get('Code') == 'TransactionConflict' for r in reasons)
            if not conflict or attempt == 3:
                raise
            time.sleep(.04 * (attempt + 1))


def query(h, pk, prefix=''):
    condition = Key('pk').eq(pk)
    if prefix:
        condition = condition & Key('sk').begins_with(prefix)
    params = {'KeyConditionExpression': condition, 'ConsistentRead': True}
    result = []
    while True:
        page = h.table.query(**params)
        result.extend(page['Items'])
        if not page.get('LastEvaluatedKey'):
            return result
        params['ExclusiveStartKey'] = page['LastEvaluatedKey']


def session_key(h, token):
    return h.key('PLATFORM_SESSION#' + hmac.new(h.config()['sessionSecret'].encode(), token.encode(), hashlib.sha256).hexdigest())


def credentials(h, data):
    email = h.text_field(data, 'email', 254, True).lower()
    password = data.get('password', '')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or not isinstance(password, str) or not 8 <= len(password) <= 256:
        raise h.Problem(400, 'Enter a valid email and a password of 8–256 characters')
    return email, password


def auth(h, data, ip, signup):
    h.throttle('platform-login-ip', ip, 10, 300)
    h.throttle('platform-login-global', 'platform', 100, 300)
    email, password = credentials(h, data)
    item_key = h.key('ORGANIZER_EMAIL#' + h.digest(email))
    if signup:
        h.throttle('platform-signup', ip, 10, 3600)
        name = h.text_field(data, 'name', 100, True)
        salt = secrets.token_hex(32)
        organizer = {**item_key, 'id': secrets.token_hex(16), 'email': email, 'name': name,
                     'passwordSalt': salt, 'passwordHash': hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000).hex()}
        try:
            h.table.put_item(Item=organizer, ConditionExpression='attribute_not_exists(pk)')
        except ClientError as error:
            if error.response['Error']['Code'] == 'ConditionalCheckFailedException':
                raise h.Problem(409, 'Could not create this account. If you already have an account, sign in.') from None
            raise
    else:
        organizer = h.read(item_key)
        # Same expensive hash work for an unknown email; no credential enumeration.
        dummy = {'passwordSalt': '00' * 32, 'passwordHash': '00' * 32}
        matched = h.password_matches(password, organizer or dummy)
        if not organizer or not matched:
            raise h.Problem(401, 'Email or password is incorrect')
    token, expires = secrets.token_urlsafe(32), int(time.time()) + 3600
    h.table.put_item(Item={**session_key(h, token), 'expiresAt': expires, 'ownerId': organizer['id'], 'email': email, 'authVersion': int(organizer.get('authVersion', 0))})
    return {'token': token, 'expiresAt': expires, 'organizer': {'id': organizer['id'], 'email': email, 'name': organizer['name']}}


def authorize(h, headers):
    value = headers.get('authorization', '')
    if not value.startswith('Bearer ') or not 20 < len(value) < 150:
        raise h.Problem(401, 'Please sign in to continue')
    item_key = session_key(h, value[7:])
    session = h.read(item_key)
    if not session or int(session['expiresAt']) <= time.time():
        raise h.Problem(401, 'Your session has expired. Please sign in again.')
    organizer = h.read(h.key('ORGANIZER_EMAIL#' + h.digest(session['email'])))
    if not organizer or organizer['id'] != session['ownerId'] or int(organizer.get('authVersion', 0)) != int(session.get('authVersion', 0)):
        raise h.Problem(401, 'Please sign in again')
    return session, item_key


def send_link(h, email, subject, body):
    """Reuse the protected Gmail sender; never return provider errors or log addresses."""
    settings = h.config()
    message = h.EmailMessage()
    message['From'], message['To'], message['Subject'] = settings['senderEmail'], email, subject
    message.set_content(body)
    h.throttle('email-daily', 'shared-gmail-sender', 100, 86400)
    with h.smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=5, context=h.ssl.create_default_context()) as smtp:
        smtp.login(settings['senderEmail'], settings['smtpAppPassword'])
        if smtp.send_message(message, from_addr=settings['senderEmail'], to_addrs=[email]):
            raise h.Problem(503, 'Email could not be sent. Please try again later.')


def request_reset(h, data, ip):
    email = h.text_field(data, 'email', 254, True).lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise h.Problem(400, 'Enter a valid email')
    h.throttle('password-reset-ip', ip, 5, 900)
    h.throttle('password-reset-email', email, 3, 900)
    h.throttle('password-reset-global', 'platform', 50, 3600)
    # Async mail keeps mailbox existence and SMTP latency out of the public response.
    h.boto3.client('lambda', region_name=h.REGION).invoke(FunctionName=os.environ['AWS_LAMBDA_FUNCTION_NAME'], InvocationType='Event', Payload=json.dumps({'emailJob': 'password-reset', 'email': email}).encode())
    return {'message': 'If an organizer account uses that email, a reset link will arrive. Check inbox and spam. The link expires in 15 minutes.'}


def reset_mail_job(h, email):
    organizer = h.read(h.key('ORGANIZER_EMAIL#' + h.digest(email)))
    if not organizer:
        return
    token = secrets.token_urlsafe(32)
    h.table.put_item(Item={**h.key('PASSWORD_RESET#' + h.digest(token)), 'email': email, 'ownerId': organizer['id'],
                          'authVersion': int(organizer.get('authVersion', 0)), 'expiresAt': int(time.time()) + 900})
    url = os.environ['FRONTEND_ORIGIN'] + '/#reset/' + token
    send_link(h, email, 'Reset your EventFlow QR organizer password', f'Use this single-use link within 15 minutes to choose a new password:\n{url}\n\nIf you did not request this, ignore this email. Your password has not changed.')


def reset_password(h, data, ip):
    h.throttle('reset-complete', ip, 10, 900)
    token = h.text_field(data, 'token', 100, True)
    reset_key = h.key('PASSWORD_RESET#' + h.digest(token))
    reset = h.read(reset_key)
    if not reset or int(reset['expiresAt']) <= time.time():
        raise h.Problem(400, 'This reset link is invalid or expired. Request a new one.')
    _, password = credentials(h, {'email': reset['email'], 'password': data.get('password')})
    organizer_key = h.key('ORGANIZER_EMAIL#' + h.digest(reset['email']))
    organizer = h.read(organizer_key)
    version = int(reset['authVersion'])
    if not organizer or organizer['id'] != reset['ownerId'] or int(organizer.get('authVersion', 0)) != version:
        raise h.Problem(400, 'This reset link is invalid or expired. Request a new one.')
    salt = secrets.token_hex(32)
    changed = {**organizer, 'passwordSalt': salt, 'passwordHash': hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000).hex(), 'authVersion': version + 1}
    action = put(h, changed, '(attribute_not_exists(authVersion) OR authVersion = :version) AND id = :owner')
    action['Put']['ExpressionAttributeValues'] = encoded({':version': version, ':owner': reset['ownerId']})
    try:
        transaction(h, [action, {'Delete': {'TableName': h.table.name, 'Key': encoded(reset_key), 'ConditionExpression': 'expiresAt > :now', 'ExpressionAttributeValues': encoded({':now': int(time.time())})}}])
    except ClientError as error:
        if error.response['Error']['Code'] == 'TransactionCanceledException':
            raise h.Problem(400, 'This reset link is invalid or expired. Request a new one.') from None
        raise
    return {'message': 'Password changed. Sign in with your new password. Previous sessions have been signed out.'}


def state(event, now=None):
    if event.get('deleting'):
        return 'deleting'
    now = time.time() if now is None else now
    if now < int(event['opensAt']):
        return 'scheduled'
    if now < int(event['closesAt']):
        return 'open'
    return 'closed' if now < int(event['closesAt']) + 900 else 'hidden'


def event_key(h, event_id):
    return h.key('EVENT#' + event_id, 'META')


def get_event(h, event_id, owner=None, include_deleting=False):
    if not re.fullmatch(r'[a-f0-9]{32}', event_id):
        raise h.Problem(404, 'Event not found')
    event = h.read(event_key(h, event_id))
    if not event or (event.get('deleting') and not include_deleting) or (owner and event['ownerId'] != owner['ownerId']):
        raise h.Problem(404, 'Event not found')
    return event


def public_event(h, event, owner=False):
    result = {k: event.get(k, '') for k in EVENT_FIELDS if k != 'logoKey'}
    seats = h.read(h.key('EVENT#' + event['id'], 'SEATS'))
    if not seats:
        seats = {'registered': sum(person.get('registrationStatus', 'approved') == 'approved' for person in query(h, 'EVENT#' + event['id'], 'ATTENDEE#'))}
    result['capacity'] = int(seats.get('capacity', 0))
    result['registered'] = int(seats.get('registered', 0))
    result['status'] = state(event)
    if result['status'] == 'open' and result['capacity'] and result['registered'] >= result['capacity']:
        result['status'] = 'full'
    result['logo'] = ''
    if event.get('coverKey'):
        result['cover'] = h.boto3.client('s3', region_name=h.REGION).generate_presigned_url('get_object', Params={'Bucket': os.environ['BRAND_BUCKET'], 'Key': event['coverKey']}, ExpiresIn=3600)
    import event_management as m
    result['tickets'] = []
    for ticket in m.ticket_types(event):
        if ticket['hidden'] and not owner:
            continue
        sold = int(seats.get('tickets', {}).get(ticket['id'], {}).get('registered', result['registered'] if ticket['id'] == 'standard' else 0))
        result['tickets'].append({**{k: ticket.get(k, '') for k in ('id', 'name', 'description', 'requireApproval', 'hidden')}, 'capacity': int(ticket['capacity']), 'registered': sold, 'registrationOpen': ticket.get('registrationOpen') or event['registrationOpen'], 'registrationClose': ticket.get('registrationClose') or event['registrationClose'], 'inheritOpen': not bool(ticket.get('registrationOpen')), 'inheritClose': not bool(ticket.get('registrationClose'))})
    if result['status'] == 'open' and m.full(h, event, seats):
        result['status'] = 'full'
    if event.get('logoKey'):
        import os
        result['logo'] = h.boto3.client('s3', region_name=h.REGION).generate_presigned_url('get_object', Params={'Bucket': os.environ['BRAND_BUCKET'], 'Key': event['logoKey']}, ExpiresIn=3600)
    if owner:
        result['owner'] = event['ownerEmail']
    return result


def validate_event(h, data):
    event = {k: h.text_field(data, k, maximum, required) for k, maximum, required in (
        ('name', 120, True), ('host', 120, True), ('venue', 250, True), ('description', 1500, False),
        ('color', 7, True), ('layout', 20, True), ('mark', 3, False))}
    if not re.fullmatch(r'#[a-fA-F0-9]{6}', event['color']) or event['layout'] not in ('editorial', 'compact'):
        raise h.Problem(400, 'Please check the brand color and pass layout')
    capacity = data.get('capacity', '')
    if isinstance(capacity, bool):
        raise h.Problem(400, 'Capacity must be a whole number, or blank for unlimited')
    if capacity in ('', None, 0):
        event['capacity'] = 0
    elif isinstance(capacity, (str, int)) and not isinstance(capacity, bool) and re.fullmatch(r'[1-9][0-9]{0,5}', str(capacity)):
        event['capacity'] = int(capacity)
    else:
        raise h.Problem(400, 'Capacity must be a whole number from 1 to 999999, or blank for unlimited')
    times = {}
    for name in ('start', 'end', 'registrationOpen', 'registrationClose'):
        value = h.text_field(data, name, 40, True)
        try:
            date = datetime.fromisoformat(value)
            if date.utcoffset() is None or date.utcoffset().total_seconds() != 19800:
                raise ValueError()
            times[name] = int(date.timestamp())
            event[name] = date.isoformat()
        except ValueError:
            raise h.Problem(400, 'Use valid Asia/Kolkata dates and times') from None
    if times['end'] <= times['start'] or times['registrationClose'] <= times['registrationOpen'] or times['registrationClose'] > times['end']:
        raise h.Problem(400, 'Please check the event and registration window dates')
    event.update(timezone='Asia/Kolkata', opensAt=times['registrationOpen'], closesAt=times['registrationClose'])
    return event


def logo(h, value, owner, previous):
    if value == previous.get('logoKey') or value is None:
        return previous.get('logoKey', '')
    # The browser sends the existing presigned URL when the logo is unchanged.
    if isinstance(value, str) and value.startswith('https://') and previous.get('logoKey'):
        return previous['logoKey']
    if not value:
        return ''
    match = re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)', value) if isinstance(value, str) else None
    try:
        image = base64.b64decode(match[2], validate=True) if match else b''
        kind = match[1] if match else ''
        valid = (kind == 'png' and image.startswith(b'\x89PNG\r\n\x1a\n')) or (kind == 'jpeg' and image.startswith(b'\xff\xd8\xff')) or (kind == 'webp' and image[:4] == b'RIFF' and image[8:12] == b'WEBP')
        if not valid or not 10 <= len(image) <= 500_000:
            raise ValueError()
    except (ValueError, TypeError):
        raise h.Problem(400, 'Upload a PNG, JPG or WebP logo up to 500 KB') from None
    import os
    object_key = 'brands/' + owner['ownerId'] + '/' + secrets.token_hex(16) + '.' + kind
    h.boto3.client('s3', region_name=h.REGION).put_object(Bucket=os.environ['BRAND_BUCKET'], Key=object_key, Body=image, ContentType='image/' + kind, ServerSideEncryption='AES256')
    return object_key


def save_event(h, data, owner, event_id=None):
    previous = get_event(h, event_id, owner) if event_id else {}
    event = validate_event(h, data)
    import event_management as m
    event.update(m.validate(h, {**previous, **data}, event))
    event.update(id=event_id or secrets.token_hex(16), ownerId=owner['ownerId'], ownerEmail=owner['email'],
                 version=int(previous.get('version', 0)) + 1, logoKey=logo(h, data.get('logo'), owner, previous))
    event['coverKey'] = logo(h, data.get('cover'), owner, {'logoKey': previous.get('coverKey', '')})
    meta = {**event, **event_key(h, event['id'])}
    seats_key = h.key('EVENT#' + event['id'], 'SEATS')
    seats = h.read(seats_key)
    people = query(h, 'EVENT#' + event['id'], 'ATTENDEE#') if previous else []
    counters = m.seats_for(event, seats or {}, people)
    registered = counters['registered']
    retained = {t['id'] for t in event['tickets']}
    if any(person.get('ticketId', 'standard') not in retained for person in people):
        raise h.Problem(409, 'A ticket type with registrations cannot be deleted. Hide it instead.')
    if any(t['capacity'] and counters['tickets'][t['id']]['registered'] > t['capacity'] for t in event['tickets']):
        raise h.Problem(409, 'Ticket capacity cannot be lower than approved registrations')
    if event['capacity'] and event['capacity'] < registered:
        raise h.Problem(409, 'Capacity cannot be lower than the number already registered')
    seat_action = put(h, {**seats_key, **counters})
    if seats:
        seat_action['Put'].update(ConditionExpression='registered = :registered AND (attribute_not_exists(#revision) OR #revision = :revision)', ExpressionAttributeNames={'#revision': 'revision'}, ExpressionAttributeValues=encoded({':registered': registered, ':revision': int(seats.get('revision', 0))}))
    action = put(h, meta)
    if previous:
        action['Put']['ConditionExpression'] = '#version = :previous'
        action['Put']['ExpressionAttributeNames'] = {'#version': 'version'}
        action['Put']['ExpressionAttributeValues'] = encoded({':previous': int(previous['version'])})
    try:
        transaction(h, [action, seat_action, put(h, {**event, **h.key('PUBLIC#EVENTS', event['id'])}, 'attribute_exists(pk)' if previous else 'attribute_not_exists(pk)'),
                        put(h, {**event, **h.key('OWNER#' + owner['ownerId'], 'EVENT#' + event['id'])}, 'attribute_exists(pk)' if previous else 'attribute_not_exists(pk)')])
    except ClientError as error:
        if error.response['Error']['Code'] == 'TransactionCanceledException':
            raise h.Problem(409, 'This event changed. Refresh your workspace and try again.') from None
        raise
    return public_event(h, event, True)


def attendee_key(h, event_id, registration_id):
    return h.key('EVENT#' + event_id, 'ATTENDEE#' + registration_id)


def ensure_seats(h, event):
    import event_management
    return event_management.ensure_seats(h, event)


def register(h, event, data, ip):
    import event_management
    return event_management.register(h, event, data, ip)


def checkin(h, event, data):
    value = h.text_field(data, 'token', 100, True)
    if re.fullmatch(r'EF-[A-F0-9]{10}', value.upper()):
        registration_id = value.upper()
    else:
        lookup = h.read(h.key('TOKEN#' + h.digest(value)))
        if not lookup:
            return {'status': 'invalid_pass'}
        if lookup.get('eventId') != event['id']:
            return {'status': 'wrong_event'}
        registration_id = lookup['id']
    item_key = attendee_key(h, event['id'], registration_id)
    try:
        changed = h.table.update_item(Key=item_key, UpdateExpression='SET checkedInAt = :now',
            ConditionExpression='attribute_exists(pk) AND attribute_not_exists(checkedInAt) AND (attribute_not_exists(registrationStatus) OR registrationStatus = :approved)', ExpressionAttributeValues={':now': h.stamp(), ':approved': 'approved'}, ReturnValues='ALL_NEW')['Attributes']
        return {'status': 'checked_in', 'attendee': h.public_person(changed)}
    except ClientError as error:
        if error.response['Error']['Code'] != 'ConditionalCheckFailedException':
            raise
        person = h.read(item_key)
        return {'status': 'already_checked_in', 'attendee': h.public_person(person)} if person and person.get('checkedInAt') and person.get('registrationStatus', 'approved') == 'approved' else {'status': 'invalid_pass'}


def staff(h, event, owner, data, method):
    if method == 'GET':
        members = query(h, 'EVENT#' + event['id'], 'STAFF#')
        invites = query(h, 'EVENT#' + event['id'], 'INVITE#')
        return {'members': [{'id': m['id'], 'email': m['email'], 'name': m['name']} for m in members],
                'invites': [{'id': m['inviteId'], 'email': m['email'], 'expiresAt': int(m['expiresAt'])} for m in invites if int(m['expiresAt']) > time.time()]}
    if data.get('action') == 'revoke':
        member_id = h.text_field(data, 'id', 64, True)
        if not re.fullmatch(r'[a-f0-9]{32}|[a-f0-9]{64}', member_id):
            raise h.Problem(400, 'Invalid volunteer')
        if len(member_id) == 64:
            keys = (h.key('EVENT#' + event['id'], 'INVITE#' + member_id), h.key('STAFF_INVITE#' + member_id))
        else:
            keys = (h.key('EVENT#' + event['id'], 'STAFF#' + member_id), h.key('STAFF#' + member_id, 'EVENT#' + event['id']))
        transaction(h, [event_gate(h, event)] + [{'Delete': {'TableName': h.table.name, 'Key': encoded(k)}} for k in keys])
        return {'message': 'Scanner access revoked.'}
    email = h.text_field(data, 'email', 254, True).lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise h.Problem(400, 'Enter a valid email')
    if email == owner['email']:
        raise h.Problem(400, 'You already own this event')
    h.throttle('staff-invites', owner['ownerId'], 10, 3600)
    token = secrets.token_urlsafe(32)
    invite_id = h.digest(token)
    invite = {'inviteId': invite_id, 'email': email, 'eventId': event['id'], 'expiresAt': int(time.time()) + 86400}
    transaction(h, [event_gate(h, event), put(h, {**invite, **h.key('EVENT#' + event['id'], 'INVITE#' + invite_id)}),
                    put(h, {**invite, **h.key('STAFF_INVITE#' + invite_id)})])
    try:
        send_link(h, email, 'Your EventFlow QR scanner invitation', f'You are invited to scan passes for {event["name"]}.\nSign in or create an account with this email, then accept the invitation:\n{os.environ["FRONTEND_ORIGIN"]}/#invite/{token}\n\nExpires in 24 hours. Scanner access does not include attendee exports, pass recovery or event management.')
    except Exception:
        transaction(h, [{'Delete': {'TableName': h.table.name, 'Key': encoded(k)}} for k in (h.key('EVENT#' + event['id'], 'INVITE#' + invite_id), h.key('STAFF_INVITE#' + invite_id))])
        raise h.Problem(503, 'Invitation email could not be sent. Please try again later.') from None
    return {'message': 'Invitation sent. Ask the volunteer to check inbox and spam.'}


def event_gate(h, event):
    return {'ConditionCheck': {'TableName': h.table.name, 'Key': encoded(event_key(h, event['id'])),
            'ConditionExpression': '#version = :version AND attribute_not_exists(deleting)',
            'ExpressionAttributeNames': {'#version': 'version'}, 'ExpressionAttributeValues': encoded({':version': int(event['version'])})}}


def accept_invite(h, owner, data):
    token = h.text_field(data, 'token', 100, True)
    invite_id = h.digest(token)
    lookup_key = h.key('STAFF_INVITE#' + invite_id)
    invite = h.read(lookup_key)
    if not invite or invite['email'] != owner['email'] or int(invite['expiresAt']) <= time.time():
        raise h.Problem(400, 'This invitation is invalid, expired, or belongs to a different email')
    event = get_event(h, invite['eventId'])
    organizer = h.read(h.key('ORGANIZER_EMAIL#' + h.digest(owner['email'])))
    member = {'id': owner['ownerId'], 'email': owner['email'], 'name': organizer['name'], 'eventId': event['id']}
    try:
        transaction(h, [event_gate(h, event),
            {'Delete': {'TableName': h.table.name, 'Key': encoded(lookup_key), 'ConditionExpression': 'expiresAt > :now', 'ExpressionAttributeValues': encoded({':now': int(time.time())})}},
            {'Delete': {'TableName': h.table.name, 'Key': encoded(h.key('EVENT#' + event['id'], 'INVITE#' + invite_id)), 'ConditionExpression': 'expiresAt > :now', 'ExpressionAttributeValues': encoded({':now': int(time.time())})}},
            put(h, {**member, **h.key('EVENT#' + event['id'], 'STAFF#' + owner['ownerId'])}, 'attribute_not_exists(pk) OR attribute_exists(pk)'),
            put(h, {**member, **h.key('STAFF#' + owner['ownerId'], 'EVENT#' + event['id'])}, 'attribute_not_exists(pk) OR attribute_exists(pk)')])
    except ClientError as error:
        if error.response['Error']['Code'] == 'TransactionCanceledException':
            raise h.Problem(400, 'This invitation changed or has already been used') from None
        raise
    return {'message': 'Scanner access added. Open the scanner from your workspace.'}


def delete_event(h, event, owner, data):
    """Block registration first; bounded, resumable cleanup for any attendee count."""
    if data.get('confirmation') != event['name']:
        raise h.Problem(400, 'Type the event name to confirm permanent deletion')
    if not event.get('deleting'):
        changed = {**event, 'deleting': True, 'version': int(event['version']) + 1}
        meta = put(h, changed, '#version = :previous AND ownerId = :owner')
        meta['Put']['ExpressionAttributeNames'] = {'#version': 'version'}
        meta['Put']['ExpressionAttributeValues'] = encoded({':previous': int(event['version']), ':owner': owner['ownerId']})
        try:
            transaction(h, [meta,
                {'Delete': {'TableName': h.table.name, 'Key': encoded(h.key('PUBLIC#EVENTS', event['id']))}},
                put(h, {**changed, **h.key('OWNER#' + owner['ownerId'], 'EVENT#' + event['id'])}, 'attribute_exists(pk)')])
        except ClientError as error:
            if error.response['Error']['Code'] == 'TransactionCanceledException':
                raise h.Problem(409, 'This event changed. Refresh and try again.') from None
            raise
        event = changed
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        people = h.table.query(KeyConditionExpression=Key('pk').eq('EVENT#' + event['id']) & Key('sk').begins_with('ATTENDEE#'), ConsistentRead=True, Limit=25)['Items']
        if not people:
            access = query(h, 'EVENT#' + event['id'], 'STAFF#')[:25] or query(h, 'EVENT#' + event['id'], 'INVITE#')[:25] or query(h, 'EVENT#' + event['id'], 'GUEST_INVITE#')[:25] or query(h, 'EVENT#' + event['id'], 'MAIL#')[:25]
            if access:
                actions = []
                for row in access:
                    keys = [h.key(row['pk'], row['sk'])]
                    if row['sk'].startswith('STAFF#'):
                        keys.append(h.key('STAFF#' + row['id'], 'EVENT#' + event['id']))
                    elif row['sk'].startswith('INVITE#'):
                        keys.append(h.key('STAFF_INVITE#' + row['inviteId']))
                    elif row['sk'].startswith('GUEST_INVITE#'):
                        keys.append(h.key(row['sk']))
                    actions.extend({'Delete': {'TableName': h.table.name, 'Key': encoded(k)}} for k in keys)
                transaction(h, actions)
                continue
            for asset in ('logoKey', 'coverKey'):
                if event.get(asset):
                    h.boto3.client('s3', region_name=h.REGION).delete_object(Bucket=os.environ['BRAND_BUCKET'], Key=event[asset])
            transaction(h, [{'Delete': {'TableName': h.table.name, 'Key': encoded(item_key)}} for item_key in (
                event_key(h, event['id']), h.key('EVENT#' + event['id'], 'SEATS'), h.key('OWNER#' + owner['ownerId'], 'EVENT#' + event['id']))])
            return {'status': 'deleted', 'id': event['id']}
        actions = []
        for person in people:
            if person.get('accessToken'):
                actions.append({'Delete': {'TableName': h.table.name, 'Key': encoded(h.key('REGISTRATION_ACCESS#' + h.digest(person['accessToken'])))}})
            for item_key in (attendee_key(h, event['id'], person['id']), h.key('EMAIL#' + event['id'] + '#' + h.digest(person['email'])), h.key('TOKEN#' + h.digest(person['token']))):
                actions.append({'Delete': {'TableName': h.table.name, 'Key': encoded(item_key)}})
        transaction(h, actions)
    return {'status': 'deleting', 'id': event['id']}


def dispatch(h, method, path, data, headers, ip):
    import event_management as m
    if method == 'POST' and path in ('/platform/registration/open', '/platform/registration/cancel'):
        return m.open_registration(h, data, ip, path.endswith('cancel')), 200
    if method == 'POST' and path == '/platform/guest-invites/open':
        return m.open_invitation(h, data, ip), 200
    if method == 'POST' and path == '/platform/password-reset':
        return request_reset(h, data, ip), 200
    if method == 'POST' and path == '/platform/password-reset/complete':
        return reset_password(h, data, ip), 200
    if method == 'POST' and path in ('/platform/signup', '/platform/login'):
        return auth(h, data, ip, path.endswith('signup')), 201 if path.endswith('signup') else 200
    if method == 'GET' and path == '/platform/events':
        events = [public_event(h, e) for e in query(h, 'PUBLIC#EVENTS') if state(e) not in ('hidden', 'deleting') and e.get('visibility', 'public') == 'public']
        return {'events': events, 'serverTime': h.stamp()}, 200
    public = re.fullmatch(r'/platform/events/([a-f0-9]{32})(?:/(registrations)(?:/(EF-[A-F0-9]{10})/(email))?)?', path)
    if public and ((method == 'GET' and not public[2]) or (method == 'POST' and public[2])):
        event = get_event(h, public[1])
        if public[4]:
            return h.email_pass(public[3], data, event['id'], event), 200
        if public[2]:
            return register(h, event, data, ip), 201
        return public_event(h, event), 200
    owner, item_key = authorize(h, headers)
    if method == 'POST' and path == '/platform/invitations/accept':
        return accept_invite(h, owner, data), 200
    if method == 'POST' and path == '/platform/logout':
        h.table.delete_item(Key=item_key)
        return {'ok': True}, 200
    if path == '/platform/organizer/events':
        if method == 'GET':
            events = [public_event(h, e, True) for e in query(h, 'OWNER#' + owner['ownerId'], 'EVENT#')]
            for membership in query(h, 'STAFF#' + owner['ownerId'], 'EVENT#'):
                event = h.read(event_key(h, membership['eventId']))
                if event and not event.get('deleting') and event['ownerId'] != owner['ownerId']:
                    events.append({**public_event(h, event), 'role': 'scanner'})
            return {'events': events}, 200
        if method == 'POST':
            h.throttle('create-event', owner['ownerId'], 20, 3600)
            return save_event(h, data, owner), 201
    route = re.fullmatch(r'/platform/organizer/events/([a-f0-9]{32})(?:/(attendance|checkins|pass|delete|staff|guests|communications)(?:/(EF-[A-F0-9]{10}))?)?', path)
    if route:
        event = get_event(h, route[1], include_deleting=route[2] == 'delete')
        if event['ownerId'] != owner['ownerId'] and not (method == 'POST' and route[2] == 'checkins' and h.read(h.key('EVENT#' + event['id'], 'STAFF#' + owner['ownerId']))):
            raise h.Problem(404, 'Event not found')
        if route[2] == 'communications' and method in ('GET', 'POST'):
            return m.communications(h, event, owner, data, method), 200
        if route[2] == 'guests' and route[3] and method == 'POST':
            person = h.read(attendee_key(h, event['id'], route[3]))
            if not person:
                raise h.Problem(404, 'Guest not found')
            return m.change_guest(h, event, person, data.get('action')), 200
        if route[2] == 'staff' and method in ('GET', 'POST'):
            return staff(h, event, owner, data, method), 200
        if method == 'POST' and route[2] == 'delete':
            return delete_event(h, event, owner, data), 200
        if method == 'POST' and not route[2]:
            return save_event(h, data, owner, event['id']), 200
        if method == 'POST' and route[2] == 'checkins':
            return checkin(h, event, data), 200
        if method == 'GET' and route[2] == 'attendance':
            people = [h.public_person(p) for p in query(h, 'EVENT#' + event['id'], 'ATTENDEE#')]
            arrived = sum(bool(p['checkedInAt']) for p in people)
            confirmed = sum(p['registrationStatus'] == 'approved' for p in people)
            return {'attendees': people, 'counts': {'registered': confirmed, 'checkedIn': arrived, 'notYetArrived': confirmed - arrived}}, 200
        if method == 'POST' and route[2] == 'pass' and route[3]:
            if data.get('identityConfirmed') is not True:
                raise h.Problem(400, 'Confirm the attendee’s identity in person first')
            person = h.read(attendee_key(h, event['id'], route[3]))
            if not person or person.get('registrationStatus', 'approved') != 'approved':
                raise h.Problem(404, 'Approved pass not found')
            return h.public_person(person, True), 200
    raise h.Problem(404, 'Not found')
