# Security policy

## Supported version

Security fixes are applied to the current `main` branch. This project is a
single-user research tool and does not provide automated trading or order
execution.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do
not open a public issue containing credentials, personal research data,
database contents, exploit details, or unredacted logs.

Include the affected version or commit, reproduction steps, impact, and any
suggested mitigation. If private reporting is unavailable, contact the
repository owner without attaching secrets and request a private channel.

## Secrets and personal data

Never commit `.env`, API tokens, SQLite databases, backups, logs, generated
research reports, or files under runtime user/cache directories. A value that
has reached Git history must be considered disclosed and revoked or rotated;
deleting it in a later commit is not sufficient.

## Deployment boundary

`AUTH_MODE=local` and `ENVIRONMENT=local` are only for direct loopback use.
Any reverse proxy, LAN, tunnel, container ingress, or public deployment must
use `ENVIRONMENT=server`, administrator authentication, secure cookies, and
explicit HTTPS origins as described in `docs/DEPLOYMENT_V2.md`.
