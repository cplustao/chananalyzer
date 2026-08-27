# Vendored Chan core provenance

This directory contains a locally maintained subset of
[`Vespa314/chan.py`](https://github.com/Vespa314/chan.py), licensed under MIT.

## Provenance

- Upstream snapshot reference: `8a975ebdecee0ec86825dbc7edd60fe3cd38d07b`
- Original copyright: Copyright (c) 2022 Memos
- Initial ChanAnalyzer import: preserved in the pre-remediation recovery bundle
- Package-local migration: completed during the v2 package baseline

Some imported files came from earlier revisions already present in the
initial ChanAnalyzer history. The snapshot reference above is therefore a
provenance anchor, not a claim that every file is byte-identical to that
single upstream commit.

## Local changes

- Imports point to `backend.app.chan_core.vendor`.
- Data-source loading is disabled; callers supply in-memory bars.
- `backend.app.chan_core.engine.PureChanEngine` provides the supported adapter.
- Unsafe dynamic configuration and pickle-loading entry points are not part
  of the supported interface and are removed by ChanAnalyzer hardening.

When updating this directory, record the new upstream commit here, preserve
the MIT license, review the diff manually, and update the deterministic golden
tests before changing the algorithm version.
