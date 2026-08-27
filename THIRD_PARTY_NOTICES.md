# Third-party notices

ChanAnalyzer includes and modifies portions of the open-source project
[`Vespa314/chan.py`](https://github.com/Vespa314/chan.py).

The retained implementation is located in `backend/app/chan_core/vendor` and
provides the Chan-theory structures used by the in-memory analysis adapter.
The imported code was reorganized into package-local imports and adapted so
the application supplies market bars directly, without allowing the vendored
core to open network or database data sources.

Upstream reference used to document the imported snapshot:

- Repository: https://github.com/Vespa314/chan.py
- Reference commit: `8a975ebdecee0ec86825dbc7edd60fe3cd38d07b`
- License: MIT
- Upstream copyright: Copyright (c) 2022 Memos

The upstream MIT license is reproduced in
`backend/app/chan_core/vendor/LICENSE` and applies to the retained and modified
upstream portions. ChanAnalyzer's own code is distributed under the root MIT
license.

Dependency packages installed through Python or npm retain their respective
licenses. Generated dependency lock files are not a transfer of ownership of
those packages.
