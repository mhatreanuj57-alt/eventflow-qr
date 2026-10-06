"""Deploy the reviewed platform backend into the existing Sydney stack; no secrets read."""
import json
import time
import zipfile
from pathlib import Path
from aws_session import eventflow_session
from botocore.exceptions import ClientError

root = Path(__file__).resolve().parents[1]
session = eventflow_session()
assert session.client('sts').get_caller_identity()['Account'] == '122458452281', 'Wrong AWS project'
plan = session.client('freetier').get_account_plan_state()
assert plan['accountPlanStatus'] == 'ACTIVE', 'AWS project is not active'
print('Verified project 122458452281, Sydney, plan:', plan['accountPlanType'])
cf = session.client('cloudformation')
stack = cf.describe_stacks(StackName='eventflow-qr')['Stacks'][0]
assert stack['StackStatus'] in ('CREATE_COMPLETE', 'UPDATE_COMPLETE', 'UPDATE_ROLLBACK_COMPLETE'), stack['StackStatus']
parameters = {item['ParameterKey']: item['ParameterValue'] for item in stack['Parameters']}
bucket = parameters['ArtifactBucket']
assert bucket == 'eventflow-artifacts-122458452281-ap-southeast-2'
package = root / '.audit/platform-backend.zip'
with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in (root / 'backend').glob('*.py'): archive.write(path, path.name)
artifact_key = 'platform-backend-' + str(int(time.time())) + '.zip'
s3 = session.client('s3')
assert s3.get_bucket_location(Bucket=bucket)['LocationConstraint'] == 'ap-southeast-2'
s3.upload_file(str(package), bucket, artifact_key, ExtraArgs={'ServerSideEncryption': 'AES256'})
# Browser ticket export must fetch the private, signed branding image. API CORS remains production-only.
s3.put_bucket_cors(Bucket=bucket, CORSConfiguration={'CORSRules': [{'AllowedOrigins': ['https://eventflow-qr.vercel.app', 'http://localhost:4174'], 'AllowedMethods': ['GET'], 'AllowedHeaders': ['*'], 'MaxAgeSeconds': 300}]})
parameters['ArtifactKey'] = artifact_key
parameters['FrontendOrigin'] = 'https://eventflow-qr.vercel.app'
cf.update_stack(StackName='eventflow-qr', TemplateBody=(root / 'infra/template.json').read_text(encoding='utf-8'),
    Parameters=[{'ParameterKey': key, 'ParameterValue': value} for key, value in parameters.items()], Capabilities=['CAPABILITY_IAM'])
print('Backend update submitted; waiting for CloudFormation.')
for _ in range(120):
    current = cf.describe_stacks(StackName='eventflow-qr')['Stacks'][0]['StackStatus']
    if current == 'UPDATE_COMPLETE':
        print('PASS: backend deployed, UPDATE_COMPLETE. Frontend has not been deployed.')
        break
    if current not in ('UPDATE_IN_PROGRESS', 'UPDATE_COMPLETE_CLEANUP_IN_PROGRESS'):
        raise RuntimeError('Deployment status: ' + current)
    time.sleep(3)
else: raise RuntimeError('Deployment still running; inspect CloudFormation before testing.')
