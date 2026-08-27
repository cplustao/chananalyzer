# Contributing

ChanAnalyzer accepts changes that can be reviewed for correctness, security,
and source provenance.

## Code origin

- Do not paste code from another project without recording its repository,
  exact version or commit, license, and local modifications.
- Preserve copyright and license notices for copied or modified third-party
  code. Do not describe the complete repository as wholly original when it
  contains vendored components.
- AI-assisted code must be reviewed by a person. The contributor remains
  responsible for testing it, checking for copied expressions or incompatible
  licenses, and explaining non-obvious security decisions.
- Changes under `backend/app/chan_core/vendor` must update `UPSTREAM.md` and
  deterministic golden tests. Do not update that directory automatically.

## Sensitive material

Never commit API tokens, `.env`, databases, backups, logs, personal research
notes, or runtime cache/user files. Run the repository secret scan before
pushing. If a secret enters history, revoke it before attempting history
cleanup.

## Verification

Run the backend test/static-check suite and the frontend lint, unit, build, and
relevant E2E suites. Generated OpenAPI files and dependency locks must be
updated in the same change that alters their inputs.
