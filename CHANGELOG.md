# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-09-24

First release.

### Added

- `flake-detective investigate <path>` — runs a suite repeatedly under controlled
  conditions and reports **which cause** makes each test flaky, not merely that it is:
  clock dependency, hash-seed ordering, unseeded randomness, or test-order dependence.
- `flake-detective bench` — a built-in benchmark with one planted flake per cause, so
  the diagnosis can be checked against a known answer rather than read and believed.
- `demo.py` — the benchmark in one command, no arguments.
- Zero runtime dependencies. pytest is invoked as a subprocess in the repository under
  investigation, using that repository's own environment rather than being imported.

### Fixed

- A timing-dependent test inside the timing-dependent-test detector. It spun 200,000
  empty loop iterations and asserted `time.monotonic()` had advanced; on a fast machine
  the loop finishes inside the clock's resolution and the assertion fails. Elapsed time
  now comes from `time.sleep`.

[0.1.0]: https://github.com/hammasbuilds/flake-detective/releases/tag/v0.1.0
