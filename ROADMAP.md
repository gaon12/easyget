# easyget Roadmap

`easyget` targets Python `3.12+` and standard-library-only implementation.

## Product Direction
- Easy for Human: sane defaults + low-friction API/CLI for everyday use.
- Optimized for AI: compact machine output modes and structured diagnostics to reduce token usage.

## Guiding Principles
- Maintain one core HTTP engine used by both sync and async APIs.
- Keep CLI and Python API behavior consistent for retries, status handling, and output safety.
- Prefer compatibility-first expansion for `wget`, `curl`, `requests`, and `aiohttp` migration.
- Preserve zero external runtime dependencies.

## Compatibility Tracks

### 1) Downloader CLI (`wget`-style)
- Implemented:
- Multithreaded file download, resume (`-c`, `--continue`), wildcard expansion, speed limit.
- Retry controls (`--retry`, `--retry-delay`, `--retry-max-delay`, `--retry-backoff`).
- Timestamp-based skip (`--timestamping`).
- Deterministic exit codes and machine-friendly JSON/AI output contracts.
- Remaining:
- Additional parity options like `--timestamping` variants and conditional requests by ETag.

### 2) HTTP Request CLI (`curl`-style)
- Implemented:
- Core options: `-X`, `-d`, `--json-data`, `-I`, `-L`, `--fail`, `-i`.
- TLS/proxy/body parity: `--proxy`, `--cacert`, `-k`, `--cert/--key`, `-F`, `--data-urlencode`, `--compressed`.
- Response field filtering: `--output-select all|status|headers|body`.
- Remaining:
- Additional curl parity options (`--data-binary`, `--form-string`, richer template formatting).

### 3) Python Sync API (`requests`-style)
- Implemented:
- `Session` + top-level helpers, params/data/json/files/auth/cookies, redirects, stream handling.
- Transport controls (`verify`, `cert`, `proxies`, `compressed`).
- Structured exception taxonomy + response hooks + AI-friendly diagnostics.
- Remaining:
- Optional hooks parity extensions (request/pre-send hooks) and richer typed error hierarchy splits.

### 4) Python Async API (`aiohttp`-style)
- Implemented:
- `AsyncSession`, `async with session.get(...)`, async response helpers.
- Parity with sync kwargs including transport controls and hooks forwarding.
- Concurrency limit primitive (`max_concurrency`) + explicit request timeout support.
- Remaining:
- More advanced queue/backpressure configuration and cancellation instrumentation.

## Milestone Status
- M1 Completed: runtime policy locked to `3.12+` and stdlib-only.
- M2 Completed: transport controls added to sync/async core.
- M3 Completed: curl-like CLI mapped to transport controls.
- M4 Completed: parity/regression tests expanded.

## Next Milestones
- M5: Expand compatibility matrix for remaining curl/wget/requests edges.
- M6: Add benchmark + observability profiles for AI-token-efficient traces.

## Quality Gates
- `python -m py_compile easyget/*.py test_*.py`
- `python -m unittest -v`
- Every functional change is committed with focused scope.
