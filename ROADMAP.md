# easyget Roadmap

`easyget` targets Python `3.12+` and standard-library-only implementation.

## Guiding Principles
- Maintain one core HTTP engine used by both sync and async APIs.
- Keep CLI and Python API behavior consistent for retries, status handling, and output safety.
- Prefer compatibility-first expansion for `wget`, `curl`, `requests`, and `aiohttp` migration.
- Preserve zero external runtime dependencies.

## Compatibility Tracks

### 1) Downloader CLI (`wget`-style)
- Stable: multithreaded file download, resume, wildcard expansion, speed limit.
- Next:
- Better `wget` option parity (`--continue`, richer retry knobs, timestamping).
- Deterministic exit codes and machine-friendly output contracts.

### 2) HTTP Request CLI (`curl`-style)
- Stable: `-X`, `-d`, `--json-data`, `-I`, `-L`, `--fail`, `-i`.
- Next:
- TLS/proxy/auth/body option parity (`--proxy`, `--cacert`, `--cert/--key`, form and urlencode variants).
- Response filtering and richer output formats.

### 3) Python Sync API (`requests`-style)
- Stable: `Session`, top-level method helpers, params/data/json/files/auth/cookies, redirects, stream handling.
- Next:
- Transport controls (`verify`, `cert`, `proxies`, compression toggles).
- More complete exception taxonomy and hook-like extension points.

### 4) Python Async API (`aiohttp`-style)
- Stable: `AsyncSession`, `async with session.get(...)`, response async helpers.
- Next:
- Full parity with sync transport options and timeout controls.
- Better concurrency/backpressure primitives and cancellation behavior.

## Immediate Milestones
- M1: Lock runtime policy (`3.12+`, stdlib-only) in docs + package metadata.
- M2: Add transport controls to sync/async core (`verify/cert/proxy/compressed`).
- M3: Extend curl-like CLI options to use the same transport controls.
- M4: Expand test matrix around parity and regression safety.

## Quality Gates
- `python -m py_compile easyget/*.py test_*.py`
- `python -m unittest -v`
- Every functional change is committed with focused scope.
