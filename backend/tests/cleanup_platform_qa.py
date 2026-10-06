"""Delete only fixtures listed in the ignored manifest; preserve all other project data."""
import hashlib
import json
import sys
from pathlib import Path
from boto3.dynamodb.conditions import Attr, Key

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'infra'))
from aws_session import eventflow_session
manifest = root / '.audit/platform-qa.json'
fixture = json.loads(manifest.read_text(encoding='utf-8'))
session = eventflow_session()
assert session.client('sts').get_caller_identity()['Account'] == '122458452281'
table = session.resource('dynamodb').Table('eventflow-qr')
s3 = session.client('s3')
owners = {item['organizer']['id'] for item in fixture['organizers']}
keys = []
for event in fixture['events']:
    pk = 'EVENT#' + event['id']
    meta = table.get_item(Key={'pk': pk, 'sk': 'META'}, ConsistentRead=True).get('Item')
    if not meta: continue
    assert meta['ownerId'] in owners and meta['name'].startswith('Fictional platform QA '), 'Refusing non-QA event deletion'
    params = {'KeyConditionExpression': Key('pk').eq(pk), 'ConsistentRead': True}
    while True:
        page = table.query(**params)
        for item in page['Items']:
            keys.append({'pk': item['pk'], 'sk': item['sk']})
            if item['sk'].startswith('ATTENDEE#'):
                assert item['email'].endswith('@example.test'), 'Refusing non-fictional attendee deletion'
                keys.extend([{'pk': 'EMAIL#' + event['id'] + '#' + hashlib.sha256(item['email'].encode()).hexdigest(), 'sk': 'VALUE'},
                             {'pk': 'TOKEN#' + hashlib.sha256(item['token'].encode()).hexdigest(), 'sk': 'VALUE'}])
        if not page.get('LastEvaluatedKey'): break
        params['ExclusiveStartKey'] = page['LastEvaluatedKey']
    keys.extend([{'pk': 'PUBLIC#EVENTS', 'sk': event['id']}, {'pk': 'OWNER#' + meta['ownerId'], 'sk': 'EVENT#' + event['id']}])
for organizer in fixture['organizers']:
    email = organizer['organizer']['email']
    assert email.startswith('eventflow-qa-') and email.endswith('@example.test')
    keys.append({'pk': 'ORGANIZER_EMAIL#' + hashlib.sha256(email.encode()).hexdigest(), 'sk': 'VALUE'})
    # Session rows are intentionally not indexed; project-size scan is only for QA maintenance.
    params = {'FilterExpression': Attr('ownerId').eq(organizer['organizer']['id']) & Attr('pk').begins_with('PLATFORM_SESSION#'), 'ConsistentRead': True}
    while True:
        page = table.scan(**params)
        keys.extend({'pk': item['pk'], 'sk': item['sk']} for item in page['Items'])
        if not page.get('LastEvaluatedKey'): break
        params['ExclusiveStartKey'] = page['LastEvaluatedKey']
    prefix = 'brands/' + organizer['organizer']['id'] + '/'
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket='eventflow-artifacts-122458452281-ap-southeast-2', Prefix=prefix):
        for obj in page.get('Contents', []):
            assert obj['Key'].startswith(prefix)
            s3.delete_object(Bucket='eventflow-artifacts-122458452281-ap-southeast-2', Key=obj['Key'])
with table.batch_writer() as batch:
    for key in keys: batch.delete_item(Key=key)
assert all(not table.get_item(Key=key, ConsistentRead=True).get('Item') for key in keys)
manifest.unlink()
print('PASS: temporary QA organizers, events, attendees, lookup records, sessions and branding removed. Other data preserved.')
