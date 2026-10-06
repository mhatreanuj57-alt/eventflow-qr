"""Bounded, non-mutating public checks; never prints secret values or attendee data."""
import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SITE = 'https://eventflow-qr.vercel.app'
API = 'https://7ctg987wi2.execute-api.ap-southeast-2.amazonaws.com'
results = []


def request(url, method='GET', body=None, headers=None):
    time.sleep(1)
    req = Request(url, method=method, data=body, headers=headers or {})
    try:
        response = urlopen(req, timeout=30)
    except HTTPError as error:
        response = error
    return response.code, dict(response.headers), response.read(5_000_000)


patterns = {
    'AWS access key identifier': r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'private key': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'provider API secret': r'\b(?:sk-proj-|sk-ant-)[A-Za-z0-9_-]{20,}',
    'literal secret assignment': r'(?i)(?:smtpPassword|sessionSecret|aws_secret_access_key)\s*[=:]\s*[\"\'][^\"\']{8,}[\"\']',
}


def inspect(label, content):
    decoded = content.decode('utf-8', errors='replace')
    for kind, pattern in patterns.items():
        if re.search(pattern, decoded):
            results.append({'check': 'secret-pattern', 'location': label, 'kind': kind, 'result': 'REVIEW'})


status, headers, html = request(SITE)
results.append({'check': 'homepage', 'status': status})
results.append({'check': 'security-headers', 'present': [h for h in ('content-security-policy', 'x-frame-options', 'x-content-type-options', 'referrer-policy', 'strict-transport-security') if h in {k.lower() for k in headers}]})
inspect('/', html)
assets = re.findall(r'(?:src|href)=[\"\'](/assets/[^\"\']+\.(?:js|css))[\"\']', html.decode())
for asset in assets:
    code, _, content = request(SITE + asset)
    inspect(asset, content)
    results.append({'check': 'public-asset', 'path': asset, 'status': code})
    if asset.endswith('.js'):
        code, _, source_map = request(SITE + asset + '.map')
        results.append({'check': 'source-map', 'status': code, 'exposed': code == 200 and b'"sources"' in source_map})
for path in ('/.env', '/.env.production', '/.git/config', '/backend/handler.py', '/infra/template.json'):
    code, _, content = request(SITE + path)
    inspect(path, content)
    results.append({'check': 'sensitive-path', 'path': path, 'status': code, 'html_fallback': b'<!doctype html' in content.lower()})

fake_id = '0' * 32
for path, method, body in (
    ('/platform/organizer/events', 'GET', None),
    (f'/platform/organizer/events/{fake_id}/attendance', 'GET', None),
    (f'/platform/organizer/events/{fake_id}/pass/EF-0000000000', 'POST', b'{"identityConfirmed":true}'),
    (f'/platform/organizer/events/{fake_id}/checkins', 'POST', b'{"token":"invalid-security-test"}'),
    ('/attendance', 'GET', None),
    ('/registrations/EF-0000000000/pass', 'GET', None),
):
    code, _, content = request(API + path, method, body, {'Content-Type': 'application/json'})
    results.append({'check': 'unauthenticated-protected-route', 'path': path, 'status': code, 'blocked': code in (401, 403, 404)})
    inspect(path, content)

code, _, content = request(API + '/platform/organizer/events', headers={'Authorization': 'Bearer ' + 'x' * 43})
results.append({'check': 'forged-session', 'status': code, 'blocked': code == 401})
code, headers, content = request(API + '/platform/events', headers={'Origin': 'https://attacker.invalid'})
lower = {k.lower(): v for k, v in headers.items()}
results.append({'check': 'untrusted-origin', 'status': code, 'allowed_origin': lower.get('access-control-allow-origin'), 'attacker_allowed': lower.get('access-control-allow-origin') in ('*', 'https://attacker.invalid')})
catalog = json.loads(content)
results.append({'check': 'public-event-field-leak', 'leaked_field_names': sorted({k for event in catalog.get('events', []) for k in event if k in ('ownerEmail', 'ownerId', 'passwordHash', 'passwordSalt', 'token', 'sessionSecret', 'pk', 'sk')})})

destination = Path('.audit/public-security-results.json')
destination.parent.mkdir(exist_ok=True)
destination.write_text(json.dumps(results, indent=2), encoding='utf-8')
print(json.dumps(results, indent=2))
print('Preliminary checks only; this is not a completed Strix pentest.')
