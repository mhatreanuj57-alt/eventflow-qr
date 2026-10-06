"""Create local deployment artifacts; never print credentials."""
import hashlib
import json
import secrets
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
artifacts = root / '.audit'
artifacts.mkdir(exist_ok=True)
bootstrap = artifacts / 'organizer-bootstrap.json'
if not bootstrap.exists():
    password = secrets.token_urlsafe(24)
    salt = secrets.token_bytes(32)
    settings = {'organizerEmail': '', 'senderEmail': '', 'passwordSalt': salt.hex(),
                'passwordHash': hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600_000).hex(),
                'sessionSecret': secrets.token_urlsafe(48)}
    bootstrap.write_text(json.dumps(settings), encoding='utf-8')
    (artifacts / 'organizer-password.txt').write_text(password, encoding='utf-8')
with zipfile.ZipFile(artifacts / 'backend.zip', 'w', zipfile.ZIP_DEFLATED) as package:
    for path in (root / 'backend').glob('*.py'):
        package.write(path, path.name)
print('Deployment package prepared. Credentials saved only under ignored .audit/.')
