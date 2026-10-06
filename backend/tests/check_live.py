"""Public HTTPS integration check. Creates one clearly fictional attendee; never resets attendance.
Run: python backend/tests/check_live.py --api URL --email ORGANIZER_EMAIL
Password is prompted, or supplied through EF_QA_PASSWORD. Never prints credentials or pass tokens.
"""
import argparse
import getpass
import json
import os
import secrets
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

parser = argparse.ArgumentParser()
parser.add_argument('--api', required=True)
parser.add_argument('--email', required=True)
args = parser.parse_args()
password = os.environ.get('EF_QA_PASSWORD') or getpass.getpass('Organizer password: ')


def request(path, body=None, token=None, method=None, origin=None):
    headers = {'Content-Type': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    if origin: headers['Origin'] = origin
    req = urllib.request.Request(args.api.rstrip('/') + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method=method or ('POST' if body is not None else 'GET'))
    try:
        response = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    return response.status, json.loads(response.read()), response.headers


preflight = urllib.request.Request(args.api.rstrip('/') + '/registrations', method='OPTIONS', headers={
    'Origin': 'https://eventflow-qr-preview.vercel.app', 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type'})
with urllib.request.urlopen(preflight, timeout=30) as response:
    assert response.status == 204 and response.headers.get('Access-Control-Allow-Origin') == 'https://eventflow-qr-preview.vercel.app'
assert request('/attendance')[0] == 401
assert request('/checkins', {'token': 'invalid'})[0] == 401
assert request('/registrations/EF-0000000000/pass')[0] == 401
status, session, _ = request('/organizer/login', {'email': args.email, 'password': password})
assert status == 200, (status, session)
token = session['token']
assert 0 < session['expiresAt']
_, before, _ = request('/attendance', token=token)
person = {'name': 'Integration Demo — Fictional', 'email': 'qa-' + secrets.token_hex(5) + '@example.test',
          'type': 'Other', 'organization': '', 'team': '', 'githubUrl': 'https://github.com/demo', 'linkedinUrl': 'https://www.linkedin.com/in/demo'}
with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(lambda _: request('/registrations', person), range(4)))
assert sorted(result[0] for result in results) == [201, 409, 409, 409], [(r[0], r[1]) for r in results]
pass_record = next(result[1] for result in results if result[0] == 201)
assert all('token' not in result[1] for result in results if result[0] == 409)
assert request('/registrations', {**person, 'email': 'not-an-email'})[0] == 400
assert request('/registrations', {**person, 'githubUrl': 'https://github.com.evil.test/demo'})[0] == 400
assert request('/checkins', {'token': 'invalid-pass'}, token)[1] == {'status': 'invalid_pass'}
with ThreadPoolExecutor(max_workers=4) as pool:
    scans = list(pool.map(lambda _: request('/checkins', {'token': pass_record['token']}, token), range(4)))
assert all(result[0] == 200 for result in scans), [(r[0],r[1]) for r in scans]
assert sum(result[1]['status'] == 'checked_in' for result in scans) == 1
assert sum(result[1]['status'] == 'already_checked_in' for result in scans) == 3
assert len({result[1]['attendee']['checkedInAt'] for result in scans}) == 1
_, recovery, _ = request('/registrations/' + pass_record['id'] + '/pass', token=token)
assert recovery['token'] == pass_record['token'] and recovery['checkedInAt']
assert request('/checkins', {'token': pass_record['id']}, token)[1]['status'] == 'already_checked_in'
assert request('/registrations/' + pass_record['id'] + '/email', {'token': 'wrong', 'png': 'fake'})[0] == 404
_, after, headers = request('/attendance', token=token, origin='https://eventflow-qr-preview.vercel.app')
assert after['counts']['registered'] == before['counts']['registered'] + 1
assert after['counts']['checkedIn'] == before['counts']['checkedIn'] + 1
assert all('token' not in attendee for attendee in after['attendees'])
assert headers.get('Access-Control-Allow-Origin') == 'https://eventflow-qr-preview.vercel.app'
_, _, headers = request('/attendance', token=token, origin='https://evil.example')
assert headers.get('Access-Control-Allow-Origin') is None
assert request('/organizer/logout', {}, token)[0] == 200
assert request('/attendance', token=token)[0] == 401
print('PASS: actual HTTPS API concurrency, validation, protected endpoints, login/logout, recovery, counts and restricted CORS. One fictional demo attendee added.')
