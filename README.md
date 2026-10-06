# EventFlow QR

An event registration and QR check-in platform by **Error 403: Forbidden SSO** — Anuj, Khushal, Karan and Damini.

**Live app:** https://eventflow-qr.vercel.app

## Features

- Separate organizer accounts and event workspaces, with customizable branding and downloadable QR passes.
- Free ticket types, event/ticket capacities and registration windows, approval requests and waitlists.
- Required or optional registration questions; public and unlisted events.
- Organizer guest approval, decline and cancellation; private attendee status links.
- Camera and manual check-in, duplicate rejection, attendance search/filtering, recent check-ins and CSV export.
- Organizer-assisted pass recovery and event-scoped scanner volunteer accounts.
- PNG pass emails, guest invitations, selected-recipient announcements and password reset.
- Calendar downloads and directions; responsive layouts, keyboard navigation and reduced motion.

Registrations use atomic event/email uniqueness. Approval and check-in use DynamoDB conditional transactions. Pending guests have no usable entry QR. Cancellation cannot reset a checked-in guest. Registration closes at its configured time; the event leaves public discovery 15 minutes later, while its organizer keeps the records.

This is an independent platform. Paid tickets and payments are not implemented.

## Stack

React, Vite and JavaScript with plain CSS; `qrcode`, `jsqr` and `html-to-image` for QR rendering, camera decoding and matching PNG exports. Vercel hosts the frontend.

Python Lambda and boto3 serve an API Gateway HTTP API backed by DynamoDB. Private S3 objects store organizer branding. Secrets Manager protects backend credentials, and CloudWatch provides logs. Application resources use **ap-southeast-2**. Gmail SMTP uses certificate-verified TLS; Amazon SES is not used for delivery.

Sessions expire, passwords are salted and hashed, sensitive routes require backend authorization, and CORS permits the intended frontend origin. QR tokens are opaque and contain no personal information. Private registration links grant access to that registration; keep them private.

## Run locally

Use a current Node.js installation compatible with Vite and Python with the backend requirements.

```powershell
cd frontend
npm ci
npm run dev
```

The Vite development proxy is configured for the deployed API. Requests to it use the real backend: use fictional test records deliberately, and avoid sending unwanted emails. The frontend keeps its organizer session in memory.

For a production build, copy `frontend/.env.example` to `frontend/.env.local` and set `VITE_API_BASE_URL` to your API URL. This URL is public configuration, not a secret.

```powershell
cd frontend
npm run build
npm run preview
```

Camera access needs HTTPS or localhost and browser permission. To test on a physical phone, use the HTTPS site.

## Backend and deployment

```powershell
python -m pip install boto3
```

`infra/template.json` defines the stack. The deployment/session helpers are configured for the existing EventFlow AWS project and `eventflow` profile; they are not a generic installer. Adapt the project, bucket, origin and API references before deploying your own instance. Verify AWS login, project identity, assigned Region and service availability first. Keep Regional resources in the project's assigned Region.

Keep organizer and SMTP credentials in Secrets Manager. Never put passwords, AWS credentials or SMTP app passwords in frontend variables, source code or Git. Gmail delivery shares a 100-attempt daily allowance; accepted mail can still arrive in Spam. Guest invitations support 25 addresses per request and announcements 50 selected recipients. A transactional email provider is needed before broad campaigns.

## Checks

These local checks mock storage/mail and do not change AWS data:

```powershell
python backend/tests/check_backend.py
python backend/tests/check_platform_features.py
python backend/tests/check_event_management.py
node frontend/check-platform.mjs
node frontend/check-feature-tools.mjs
node frontend/check-event-calendar.mjs
```

Tests named `*_live.py` contact the configured AWS deployment and may create temporary records. Read them before running. Destructive cleanup scripts must never be run against other people's live events or registrations.

Actual AWS lifecycle and authorization checks and deployed browser/mobile layout checks passed for this release. Automated camera checks use a generated video stream; physical phone scanning and actual invitation/announcement inbox delivery require human verification.

## Project layout

```text
frontend/src/Platform.jsx          Connected app and organizer workspace
frontend/src/App.jsx               Shared scanner
frontend/src/EventSettings.jsx     Ticket types and registration questions
frontend/src/Communications.jsx    Invitations and announcements
frontend/src/RegistrationStatus.jsx Private attendee status and cancellation
backend/handler.py                 Shared validation, auth and pass email
backend/platform_api.py            Multi-organizer/event API
backend/event_management.py        Ticket lifecycle and persistent mail jobs
backend/tests/                     Runnable regression and live checks
infra/                            AWS template and deployment helpers
frontend/public/brand/             Product and team assets
```

Local audit artifacts, generated videos, private handoff notes, credentials, dependencies and build outputs are excluded from this repository.
