"""Use AWS CLI's renewed login in memory; never save or print temporary credentials."""
import json
import subprocess
import boto3


def eventflow_session():
    cli = r'C:\Users\ADMIN\AppData\Local\Programs\Amazon\AWSCLIV2\aws.exe'
    result = subprocess.run([cli, 'configure', 'export-credentials', '--profile', 'eventflow', '--format', 'process'], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('AWS credentials unavailable. Renew aws login --profile eventflow.')
    value = json.loads(result.stdout)
    return boto3.Session(aws_access_key_id=value['AccessKeyId'], aws_secret_access_key=value['SecretAccessKey'],
                         aws_session_token=value['SessionToken'], region_name='ap-southeast-2')
