# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

First release.

### Added

- `flake-detective investigate REPO [TARGET]` - runs a pytest suite repeatedly, changing
  one thing per arm, and reports **which cause** makes each flaky test flip: test order,
  hash seed, the wall clock, or nothing at all (nondeterminism). Every arm's failure rate
  is printed beside the verdict.
- Opt-in arms with `--arms`: `timezone` and `locale` (POSIX only; they skip themselves
  with a message on Windows, where the variables do not reach the interpreter),
  `parallel` (needs pytest-xdist in the target environment) and `isolation` (every test
  alone in its own process). `--arms all` turns on everything.
- `--localise` - bisects each order dependence to name the earlier test that causes it.
- `--python PATH` - the interpreter, or venv directory, the suite runs with.
- `--jobs N` - run N pytest processes at once. Default 1 for `investigate`, because
  concurrent copies of a real suite can collide on shared files or ports; up to 4 for
  `bench`, whose suite shares nothing.
- Per-run progress on stderr with an estimate of the time left; `--quiet` turns it off.
- `--fail-on-flake`, `--json FILE`, `--timeout`, `--no-freeze-clock`, `--version`.
- `flake-detective bench` - a built-in suite with one planted flake per cause and six
  stable tests, scored for detection, attribution and false positives. `--sweep` scores
  it at 1, 2, 3, 5, 7 and 11 runs per arm.
- `flake-detective fixture DIR` - writes that suite out to read.
- Exit status: 0 clean, 1 flaky tests found with `--fail-on-flake`, 2 when nothing (or
  not everything) could be examined - bad arguments, pytest missing from the target
  interpreter, a collection error, no tests, or an arm with no scoreable run.
- Zero runtime dependencies. pytest is run as a subprocess in the target interpreter,
  never imported.

### Fixed (found before release)

- `investigate --help` crashed on Python 3.11-3.13 (`TypeError: %o format`) because of a
  literal `%` in the `--runs` help.
- A missing pytest, an empty directory, a collection error or a nonexistent `--python`
  all printed "No flaky tests found" and exited 0; `bench` without pytest reported
  "detection 0/4". All now exit 2 and say why.
- `--runs 0` and unknown `--arms` names were accepted silently.
- The clock arm's first three dates were all weekdays, so fewer than four runs never
  froze the clock on a weekend.
- `bench` printed "9 tests" while pytest collected 10.
- Test names containing characters outside the Windows code page came back escaped and
  could not be passed back to pytest, so every order run was unscoreable.
- A timing-dependent test inside the timing-dependent-test detector: it spun an empty
  loop and asserted `time.monotonic()` had advanced, which a fast machine finishes
  inside the clock's resolution.

[0.1.0]: https://github.com/hammasbuilds/flake-detective/releases/tag/v0.1.0
