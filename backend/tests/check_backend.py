"""Run: python backend/tests/check_backend.py. No AWS calls or secrets needed."""
import hashlib
import base64
import struct
import zlib
import importlib.util
import json
import os
import sys
import threading
import ssl
import smtplib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch, MagicMock
from email import policy
from email.parser import BytesParser
from botocore.exceptions import ClientError

os.environ['TABLE_NAME'] = 'test'
os.environ['SECRET_ARN'] = 'test'
with patch('boto3.resource'), patch('boto3.client'):
    spec = importlib.util.spec_from_file_location('handler', Path(__file__).parents[1] / 'handler.py')
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)


def conditional():
    return ClientError({'Error': {'Code': 'ConditionalCheckFailedException'}}, 'UpdateItem')


class Database:
    name = 'test'
    def __init__(self):
        self.items, self.lock = {}, threading.Lock()
    def get_item(self, Key, **kwargs):
        with self.lock:
            item = self.items.get(tuple(Key.values()))
            return {'Item': dict(item)} if item else {}
    def put_item(self, Item, **kwargs):
        with self.lock:
            self.items[(Item['pk'], Item['sk'])] = dict(Item)
    def delete_item(self, Key):
        with self.lock:
            self.items.pop(tuple(Key.values()), None)
    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, **kwargs):
        with self.lock:
            k = tuple(Key.values()); item = self.items.get(k)
            if 'checkedInAt' in UpdateExpression:
                assert kwargs['ConditionExpression'] == 'attribute_exists(pk) AND attribute_not_exists(checkedInAt)'
                if not item or item.get('checkedInAt'): raise conditional()
                item['checkedInAt'] = ExpressionAttributeValues[':now']
            elif 'attempts' in UpdateExpression:
                item = self.items.setdefault(k, dict(Key))
                if item.get('attempts', 0) >= ExpressionAttributeValues[':limit']: raise conditional()
                item['attempts'] = item.get('attempts', 0) + 1
            return {'Attributes': dict(item)}
    def transact_write_items(self, TransactItems):
        from boto3.dynamodb.types import TypeDeserializer
        deserialize = TypeDeserializer().deserialize
        items = [{k: deserialize(v) for k, v in entry['Put']['Item'].items()} for entry in TransactItems]
        assert all(entry['Put']['ConditionExpression'] == 'attribute_not_exists(pk)' for entry in TransactItems)
        with self.lock:
            if any((item['pk'], item['sk']) in self.items for item in items):
                raise ClientError({'Error': {'Code': 'TransactionCanceledException'}, 'CancellationReasons': [{'Code': 'ConditionalCheckFailed'}]}, 'TransactWriteItems')
            self.items.update({(item['pk'], item['sk']): item for item in items})


app.table = app.ddb = Database()
salt = bytes.fromhex('01' * 32)
app._config = {'organizerEmail': 'organizer@example.test', 'passwordSalt': salt.hex(),
               'passwordHash': hashlib.pbkdf2_hmac('sha256', b'correct-long-test-password', salt, 600_000).hex(), 'sessionSecret': 'test-only-pepper'}
person = {'name': 'Morgan Demo', 'email': ' MORGAN@example.test ', 'type': 'Professional', 'organization': 'Demo Studio', 'team': ''}


def attempt_register(i):
    try: return app.register(person, str(i))
    except app.Problem as error:
        assert error.status == 409 and 'token' not in error.message
        return None


with ThreadPoolExecutor(max_workers=8) as pool:
    registrations = list(pool.map(attempt_register, range(8)))
assert sum(bool(p) for p in registrations) == 1
created = next(p for p in registrations if p)
assert created['email'] == 'morgan@example.test' and created['token'] != created['id']
with ThreadPoolExecutor(max_workers=8) as pool:
    scans = list(pool.map(lambda _: app.checkin({'token': created['token']}), range(8)))
assert sum(s['status'] == 'checked_in' for s in scans) == 1
assert len({s['attendee']['checkedInAt'] for s in scans}) == 1
assert app.checkin({'token': created['id']})['status'] == 'already_checked_in'
assert app.checkin({'token': 'invalid-token'}) == {'status': 'invalid_pass'}
assert app.checkin({'token': 'EF-0000000000'}) == {'status': 'invalid_pass'}
assert app.handler({'requestContext': {'http': {'method': 'OPTIONS'}}, 'rawPath': '/registrations'}, None)['statusCode'] == 204
assert app.read(app.attendee_key(created['id']))['token'] == created['token']
session = app.login({'email': 'organizer@example.test', 'password': 'correct-long-test-password'}, 'auth-test')
try:
    app.login({'email': 'caf\u00e9@example.test', 'password': 'incorrect-test-password'}, 'unicode-auth-test')
    raise AssertionError('Incorrect Unicode email accepted')
except app.Problem as error:
    assert error.status == 401
assert app.authorize({'authorization': 'Bearer ' + session['token']})
app.table.items[tuple(app.session_key(session['token']).values())]['expiresAt'] = 0
for headers in ({}, {'authorization': 'Bearer fake'}, {'authorization': 'Bearer ' + session['token']}):
    try: app.authorize(headers); raise AssertionError('Unauthorized session accepted')
    except app.Problem as error: assert error.status == 401
for bad in ({**person, 'organization': ''}, {**person, 'email': 'invalid'}, {**person, 'githubUrl': 'https://github.com.evil.test/a'}, {**person, 'linkedinUrl': 'javascript:alert(1)'}, {**person, 'type': 'Unknown'}):
    try: app.validate_registration(bad); raise AssertionError('Invalid input accepted')
    except app.Problem as error: assert error.status == 400
for i in range(5): app.throttle('test', 'ip', 5, 300)
try: app.throttle('test', 'ip', 5, 300); raise AssertionError('Throttle failed')
except app.Problem as error: assert error.status == 429

# Verify attachment bytes and fixed recipient, and preserve the pass on provider failure.
def chunk(kind, value):
    return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))
png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0)) + chunk(b'tEXt', b'Description\0Fictional attachment regression check') + chunk(b'IDAT', zlib.compress(b'\0\xff\xff\xff\xff')) + chunk(b'IEND', b'')
saved = app.read(app.attendee_key(created['id']))
saved['accessToken'] = 'fictional-private-registration-capability'
app.table = MagicMock()
app.table.get_item.return_value = {'Item': dict(saved)}
app.smtplib.SMTP_SSL = MagicMock()
smtp = app.smtplib.SMTP_SSL.return_value.__enter__.return_value
smtp.send_message.return_value = {}
app.throttle = MagicMock()
app._config['senderEmail'] = 'organizer@example.test'
app._config['smtpAppPassword'] = 'test-only-app-password'
data = {'token': created['token'], 'png': base64.b64encode(png).decode(), 'email': 'ignored-attacker@example.test'}
for invalid_token in ('caf\u00e9-AAAAAAAAAAAAAAAAAAAA', '\U0001f600-AAAAAAAAAAAAAAAAAAAA'):
    response = app.handler({'requestContext': {'http': {'method': 'POST', 'sourceIp': 'unicode-test'}},
        'rawPath': '/registrations/' + created['id'] + '/email',
        'body': json.dumps({'token': invalid_token, 'png': 'not-base64'})}, None)
    assert response['statusCode'] == 404
    smtp.send_message.assert_not_called()
    app.table.update_item.assert_not_called()
assert app.email_pass(created['id'], data)['status'] == 'sent'
connection = app.smtplib.SMTP_SSL.call_args
assert connection.args == ('smtp.gmail.com', 465) and connection.kwargs['timeout'] == 5
assert connection.kwargs['context'].check_hostname and connection.kwargs['context'].verify_mode == ssl.CERT_REQUIRED
smtp.login.assert_called_once_with('organizer@example.test', 'test-only-app-password')
assert smtp.send_message.call_args.kwargs == {'from_addr': 'organizer@example.test', 'to_addrs': [saved['email']]}
message = BytesParser(policy=policy.default).parsebytes(smtp.send_message.call_args.args[0].as_bytes())
assert 'demo' not in str(message['Subject']).lower()
assert 'demo registration' not in message.get_body(preferencelist=('plain',)).get_content().lower()
assert message['To'] == saved['email']
assert '/#registration/' + saved['accessToken'] in message.get_body(preferencelist=('plain',)).get_content()
attachments = list(message.iter_attachments())
assert len(attachments) == 1 and attachments[0].get_payload(decode=True) == png
assert attachments[0].get_filename() == 'eventflow-' + created['id'] + '.png'
smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b'test only')
assert app.email_pass(created['id'], data)['status'] == 'failed'
smtp.login.side_effect = OSError('test connection failure')
assert app.email_pass(created['id'], data)['status'] == 'failed'
smtp.login.side_effect = None
smtp.send_message.return_value = {saved['email']: (550, b'test rejected')}
assert app.email_pass(created['id'], data)['status'] == 'failed'
assert app.table.get_item.return_value['Item']['token'] == saved['token']
assert app.table.get_item.return_value['Item']['checkedInAt'] == saved['checkedInAt']
app.table.get_item.return_value['Item']['emailState'] = 'sent'
app.table.update_item.side_effect = conditional()
smtp.reset_mock()
assert app.email_pass(created['id'], data)['status'] == 'sent'
smtp.send_message.assert_not_called()
app._config.pop('smtpAppPassword')
assert app.email_pass(created['id'], data)['status'] == 'unavailable'
try: app.email_pass(created['id'], {**data, 'token': 'wrong'}); raise AssertionError('Wrong pass capability accepted')
except app.Problem as error: assert error.status == 404
print('PASS: concurrent registration/check-in, original timestamp/token, validation, password/session expiry, authorization/throttling, attachment/recipient and email failure preservation.')
