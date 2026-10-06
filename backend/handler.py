"""One event, one Lambda. No passwords, tokens, or attendee data in logs."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import ssl
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.parse import urlsplit

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
EVENT = "builders-breakout-2026"
EVENT_DETAILS = {"name": "Builders Breakout", "start": "2026-10-06T10:00:00+05:30", "end": "2026-10-06T15:00:00+05:30", "timezone": "Asia/Kolkata", "venue": "Architecture Building, CSMU"}
table = boto3.resource("dynamodb", region_name=REGION).Table(os.environ["TABLE_NAME"])
ddb = boto3.client("dynamodb", region_name=REGION)
secret_client = boto3.client("secretsmanager", region_name=REGION)
_config = None


class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def config():
    global _config
    if _config is None:
        _config = json.loads(secret_client.get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"])
    return _config


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def key(pk, sk="VALUE"):
    return {"pk": pk, "sk": sk}


def attendee_key(registration_id):
    return key("EVENT#" + EVENT, "ATTENDEE#" + registration_id)


def read(item_key):
    return table.get_item(Key=item_key, ConsistentRead=True).get("Item")


def stamp():
    return datetime.now(timezone.utc).isoformat()


def text_field(data, field, maximum, required=False):
    value = data.get(field, "")
    if not isinstance(value, str):
        raise Problem(400, "Invalid " + field)
    value = value.strip()
    if len(value) > maximum or any(ord(c) < 32 for c in value) or (required and not value):
        raise Problem(400, "Please check " + field)
    return value


def validate_registration(data):
    person = {name: text_field(data, name, maximum, name in ("name", "email", "type"))
              for name, maximum in (("name", 100), ("email", 254), ("type", 20),
                                    ("organization", 150), ("team", 100), ("githubUrl", 500), ("linkedinUrl", 500))}
    person["email"] = person["email"].lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", person["email"]):
        raise Problem(400, "Please enter a valid email address")
    if person["type"] not in ("Student", "Professional", "Other"):
        raise Problem(400, "Please select an attendee type")
    if person["type"] != "Other" and not person["organization"]:
        raise Problem(400, "College or organization is required")
    for field, hosts, prefix in (("githubUrl", ("github.com", "www.github.com"), "/"),
                                 ("linkedinUrl", ("linkedin.com", "www.linkedin.com"), "/in/")):
        if not person[field]:
            continue
        try:
            url = urlsplit(person[field])
            valid = (url.scheme == "https" and url.hostname in hosts and not url.username and
                     not url.password and url.port is None and url.path.startswith(prefix) and
                     bool(url.path[len(prefix):].strip("/")) and not url.query and not url.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise Problem(400, "Please enter a valid HTTPS " + field.replace("Url", "") + " profile URL")
    return person


def throttle(scope, identity, limit, seconds):
    now = int(time.time())
    bucket = now // seconds
    try:
        table.update_item(Key=key(f"RATE#{scope}#{digest(identity)}#{bucket}"),
                          UpdateExpression="SET expiresAt = :ttl ADD attempts :one",
                          ConditionExpression="attribute_not_exists(attempts) OR attempts < :limit",
                          ExpressionAttributeValues={":ttl": (bucket + 2) * seconds, ":one": 1, ":limit": limit})
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise Problem(429, "Too many attempts. Please wait and try again.") from None
        raise


def public_person(person, include_token=False):
    fields = ("id", "name", "email", "type", "organization", "team", "githubUrl", "linkedinUrl", "createdAt", "checkedInAt")
    result = {field: person.get(field, "") for field in fields}
    result.update(registrationStatus=person.get('registrationStatus', 'approved'), ticketId=person.get('ticketId', 'standard'), ticketName=person.get('ticketName', 'Standard'), answers=person.get('answers', {}))
    if include_token:
        result['token'] = person['token'] if person.get('registrationStatus', 'approved') == 'approved' else ''
        result['accessToken'] = person.get('accessToken', '')
    return result


def register(data, ip):
    person = validate_registration(data)
    throttle("registration", ip, 100, 3600)
    person.update(id="EF-" + secrets.token_hex(5).upper(), token=secrets.token_urlsafe(32), createdAt=stamp(), event=EVENT_DETAILS)
    person.update(attendee_key(person["id"]))
    items = [person, {**key("EMAIL#" + EVENT + "#" + digest(person["email"])), "id": person["id"]},
             {**key("TOKEN#" + digest(person["token"])), "id": person["id"]}]
    from boto3.dynamodb.types import TypeSerializer
    serializer = TypeSerializer()
    try:
        ddb.transact_write_items(TransactItems=[{"Put": {"TableName": table.name,
            "Item": {k: serializer.serialize(v) for k, v in item.items()},
            "ConditionExpression": "attribute_not_exists(pk)"}} for item in items])
    except ClientError as error:
        if error.response["Error"]["Code"] == "TransactionCanceledException":
            reasons = error.response.get("CancellationReasons", [])
            if any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons):
                raise Problem(409, "Already registered—use your saved pass. Ask an organizer for help if it is lost.") from None
        raise
    return public_person(person, True)


def password_matches(password, settings):
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(settings["passwordSalt"]), 600_000).hex()
    return hmac.compare_digest(candidate, settings["passwordHash"])


def session_key(token):
    return key("SESSION#" + hmac.new(config()["sessionSecret"].encode(), token.encode(), hashlib.sha256).hexdigest())


def login(data, ip):
    email = text_field(data, "email", 254, True).lower()
    password = text_field(data, "password", 256, True)
    throttle("login-ip", ip, 5, 300)
    throttle("login-global", EVENT, 30, 300)
    settings = config()
    # Compute the password hash even when the email is wrong.
    matched = password_matches(password, settings)
    if not (hmac.compare_digest(email.encode('utf-8'), settings["organizerEmail"].encode('utf-8')) and matched):
        raise Problem(401, "Email or password is incorrect")
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + 3600
    table.put_item(Item={**session_key(token), "expiresAt": expires})
    return {"token": token, "expiresAt": expires}


def authorize(headers):
    authorization = headers.get("authorization", "")
    if not authorization.startswith("Bearer ") or len(authorization) > 150:
        raise Problem(401, "Please sign in to continue")
    item_key = session_key(authorization[7:])
    session = read(item_key)
    if not session or int(session["expiresAt"]) <= time.time():
        raise Problem(401, "Your session has expired. Please sign in again.")
    return item_key


def checkin(data):
    value = text_field(data, "token", 100, True)
    if re.fullmatch(r"EF-[A-Fa-f0-9]{10}", value):
        registration_id = value.upper()
    else:
        lookup = read(key("TOKEN#" + digest(value)))
        if not lookup:
            return {"status": "invalid_pass"}
        registration_id = lookup["id"]
    item_key = attendee_key(registration_id)
    try:
        changed = table.update_item(Key=item_key, UpdateExpression="SET checkedInAt = :now",
                     ConditionExpression="attribute_exists(pk) AND attribute_not_exists(checkedInAt)",
                     ExpressionAttributeValues={":now": stamp()}, ReturnValues="ALL_NEW")["Attributes"]
        return {"status": "checked_in", "attendee": public_person(changed)}
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        person = read(item_key)
        return {"status": "already_checked_in", "attendee": public_person(person)} if person else {"status": "invalid_pass"}


def attendance():
    # ponytail: one event query; paginate before returning, add server-side search for large events.
    from boto3.dynamodb.conditions import Key
    params = {"KeyConditionExpression": Key("pk").eq("EVENT#" + EVENT) & Key("sk").begins_with("ATTENDEE#"), "ConsistentRead": True}
    people = []
    while True:
        page = table.query(**params)
        people.extend(public_person(person) for person in page["Items"])
        if not page.get("LastEvaluatedKey"):
            break
        params["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    arrived = sum(bool(person["checkedInAt"]) for person in people)
    return {"attendees": people, "counts": {"registered": len(people), "checkedIn": arrived, "notYetArrived": len(people) - arrived}}


def email_pass(registration_id, data, event_id=None, event_details=None):
    """A new pass capability can send one attachment to its stored address only."""
    item_key = key('EVENT#' + event_id, 'ATTENDEE#' + registration_id) if event_id else attendee_key(registration_id)
    person = read(item_key)
    token = text_field(data, "token", 100, True)
    # compare_digest(str, str) raises TypeError for non-ASCII user input.
    if not person or person.get('registrationStatus', 'approved') != 'approved' or not hmac.compare_digest(token.encode('utf-8'), person["token"].encode('utf-8')):
        raise Problem(404, "Pass not found")
    if time.time() - datetime.fromisoformat(person.get('issuedAt') or person['createdAt']).timestamp() > 900:
        raise Problem(403, "Email delivery window ended. Use your saved pass or ask an organizer for help.")
    png = data.get("png", "")
    try:
        if not isinstance(png, str) or len(png) > 4_000_000:
            raise ValueError()
        attachment = base64.b64decode(png, validate=True)
        if not attachment.startswith(b"\x89PNG\r\n\x1a\n") or len(attachment) < 100 or len(attachment) > 3_000_000:
            raise ValueError()
    except (ValueError, TypeError):
        raise Problem(400, "Invalid PNG attachment") from None
    settings = config()
    if not settings.get("senderEmail") or not settings.get("smtpAppPassword"):
        return {"status": "unavailable", "message": "Email sending is not configured. Please download your pass."}
    try:
        table.update_item(Key=item_key, UpdateExpression="SET emailState = :sending",
                          ConditionExpression="attribute_exists(pk) AND attribute_not_exists(emailState)", ExpressionAttributeValues={":sending": "sending"})
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {"status": person.get("emailState", "sending"), "message": "Email delivery has already been attempted. Your pass is still available to download."}
        raise
    message = EmailMessage()
    message["From"] = "EventFlow QR <" + settings["senderEmail"] + ">"
    message["To"] = person["email"]
    details = event_details or EVENT_DETAILS
    message["Subject"] = "Your EventFlow QR pass — " + details['name']
    body = "Your pass is attached. Save it for check-in.\n" + details['name'] + "\n" + details['start'] + " to " + details['end'] + " (Asia/Kolkata)\n" + details['venue'] + "\nRegistration ID: " + person["id"]
    if person.get('accessToken'):
        body += "\n\nYour private registration status and cancellation link:\n" + os.environ.get('FRONTEND_ORIGIN', 'https://eventflow-qr.vercel.app') + '/#registration/' + person['accessToken'] + '\nKeep this link private.'
    message.set_content(body)
    message.add_attachment(attachment, maintype="image", subtype="png", filename="eventflow-" + person["id"] + ".png")
    try:
        throttle("email-daily", "shared-gmail-sender", 100, 86400)
        # ponytail: Gmail for the small demo; use a transactional provider if volume grows.
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=5, context=ssl.create_default_context()) as smtp:
            smtp.login(settings["senderEmail"], settings["smtpAppPassword"])
            refused = smtp.send_message(message, from_addr=settings["senderEmail"], to_addrs=[person["email"]])
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
        state, note = "sent", "Pass accepted by the email service. Check your inbox and spam folder; delivery is not guaranteed."
    except (ClientError, Problem, smtplib.SMTPException, OSError) as error:
        # Safe diagnostic code only; never log exception messages, addresses or attachment data.
        print("email_send_failed", "RateLimited" if isinstance(error, Problem) else "ProviderError")
        state, note = "failed", "Email could not be sent. Your registration is saved—download your pass here."
    table.update_item(Key=item_key, UpdateExpression="SET emailState = :state", ConditionExpression="attribute_exists(pk)", ExpressionAttributeValues={":state": state})
    return {"status": state, "message": note}


def handler(event, context):
    if event.get('emailJob') == 'event-mail' and 'requestContext' not in event:
        import event_management
        try:
            event_management.mail_worker(__import__(__name__), event['eventId'])
        except Exception:
            print('event_mail_worker_failed')
        return {'ok': True}
    if event.get('emailJob') == 'password-reset' and 'requestContext' not in event:
        import platform_api
        try:
            platform_api.reset_mail_job(__import__(__name__), event['email'])
        except Exception:
            print('password_reset_mail_failed')
        return {'ok': True}
    status = 200
    try:
        method = event["requestContext"]["http"]["method"]
        path = event.get("rawPath", "/")
        ip = event["requestContext"]["http"].get("sourceIp", "unknown")
        headers = {k.lower(): v for k, v in event.get("headers", {}).items()}
        # Browser preflight is public; API Gateway supplies the restricted CORS headers.
        if method == "OPTIONS":
            return {"statusCode": 204, "headers": {"cache-control": "no-store"}, "body": ""}
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        if len(raw) > 4_100_000:
            raise Problem(413, "Request is too large")
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            raise Problem(400, "Invalid request") from None
        if not isinstance(data, dict):
            raise Problem(400, "Invalid request")
        if path.startswith('/platform/'):
            import platform_api
            output, status = platform_api.dispatch(__import__(__name__), method, path, data, headers, ip)
        elif method == "POST" and path == "/registrations":
            output, status = register(data, ip), 201
        elif method == "POST" and path == "/organizer/login":
            output = login(data, ip)
        elif method == "POST" and re.fullmatch(r"/registrations/EF-[A-F0-9]{10}/email", path):
            output = email_pass(path.split("/")[2], data)
        else:
            session = authorize(headers)
            if method == "POST" and path == "/organizer/logout":
                table.delete_item(Key=session)
                output = {"ok": True}
            elif method == "POST" and path == "/checkins":
                output = checkin(data)
            elif method == "GET" and path == "/attendance":
                output = attendance()
            elif method == "GET" and re.fullmatch(r"/registrations/EF-[A-F0-9]{10}/pass", path):
                person = read(attendee_key(path.split("/")[2]))
                if not person:
                    raise Problem(404, "Pass not found")
                output = public_person(person, True)
            else:
                raise Problem(404, "Not found")
    except Problem as error:
        status, output = error.status, {"message": error.message}
    except Exception:
        # Never log request bodies, auth headers, or exception payloads containing data.
        status, output = 500, {"message": "Something went wrong. Please try again; contact an organizer if it continues."}
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store", "x-content-type-options": "nosniff"}, "body": json.dumps(output)}
