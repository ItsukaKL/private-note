Private Note vendored Python runtime
Version: 3.11.7
Profile: windows amd64 embedded

This directory makes the source repository runnable offline after clone.

Included:
- Python 3.11.7 embedded runtime
- Tk runtime files needed by the desktop launcher
- repository-local site-packages for source launch and offline packaging

Used by:
- run.ps1 / run.bat
- stop.ps1
- packaging/build.ps1

Design notes:
- no automatic dependency download during source launch
- no automatic dependency download during packaging
- Python dependencies are pinned in pyproject.toml and materialized here
