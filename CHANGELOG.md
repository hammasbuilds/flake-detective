# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

First release.

### Added

- `python -m flake_detective`, the same as the `flake-detective` command.
- `scripts/reproduce_third_party.sh`: plants one flake per cause into toolz and sqlparse
  at pinned commits and scores the result, so the detection claim no longer rests only on
  the author's own repositories. `inject_and_score.py` gained `--jobs`/`--seed`, defaults
  to 7 runs per arm, and finds tests kept inside the package (`toolz/tests`).
- `flake-detective investigate REPO [TARGET]` - runs a pytest suite repeatedly, changing
  one thing per arm, and reports **which cause** makes each flaky test flip: test order,
  hash seed, the wall clock, or nothing at all (nondeterminism). Every arm's failure rate
  is printed beside the verdict.
- Opt-in arms with `--arms`: `timezone` and `locale` (POSIX only; they skip themselves
  with a message on Windows, where the variables do not reach the interpreter),
  `parallel` (needs pytest-xdist in the target environment) and `isolation` (every test
  alone in its own process). `--arms all` turns on everything.
- `--localise` - runs each order-dependent test alone to find which way the dependence
  points, then bisects for the other test: the one that breaks it, or the one it needs.
  The result is in the report and the JSON.
- Order dependence is reported with a direction: `order` (another test leaks state into
  it) or `needs-other-test` (it relies on state another test creates). Without the
  isolation arm or `--localise` the direction is reported as undetermined.
- `--seed N` for the order arm's shuffles; the default is random and printed in the report.
- Ctrl-C kills running pytest processes, reports the runs that finished, and exits 130.
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
  interpreter, a collection error, no tests, every test skipped, or an arm with no
  scoreable run - and 130 on Ctrl-C. Error reports go to stderr.
- Zero runtime dependencies. pytest is run as a subprocess in the target interpreter,
  never imported.

### Fixed (found before release)

- Verdicts read "failed 3 of 7 runs when the wall clock frozen at a different date"
  (and the same for the parallel and isolation arms); the arm descriptions are now
  clauses that read correctly in both the header and the verdict.
- Outcomes were scraped from pytest's `-rf` summary. Now a plugin records pytest's own
  reports, which fixes: setup/teardown errors being invisible (a fixture-level order
  dependence appeared nowhere); a test that did not run (`-x`, `--maxfail`, `--sw` in
  addopts) counting as a pass; a test id containing `" - "` being dropped; and `-q`/`-v`
  in addopts breaking collection. `-x`, `--maxfail`, `--sw`, `--lf`, `--ff`, `--nf` and
  xdist `-n` in addopts are neutralised; the rest of addopts is kept.
- With REPO below pytest's rootdir (`mono/pkg` with `mono/pytest.ini`), every order,
  isolation and localise run failed with "file or directory not found".
- Under `--arms all`, a polluter/victim pair came back `unknown` because order, parallel
  and isolation were treated as rival causes; they now corroborate each other.
- The headline count listed only five of the nine causes.
- A test that needs another test's state was reported as polluted by it, and
  `--localise` called it "broken, not order-dependent".
- A suite where every test skipped reported "No flaky tests found".
- Ctrl-C printed a traceback and discarded finished runs.
- The file-as-REPO hint suggested the file's own folder as REPO, which changes the rootdir.
- `ruff check`/`ruff format --check` on `demo.py`, which the release workflow runs, failed.

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
