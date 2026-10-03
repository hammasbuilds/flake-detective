#!/usr/bin/env sh
# Reproduce the "third-party suites" table in the README: plant one flake of each
# known cause into toolz and sqlparse at pinned commits, and report what came back.
# Neither project shares an author with this tool. Needs git, network for the clones,
# and an interpreter with pytest (the one flake-detective is installed in is fine).
# About 30-40 minutes on a laptop at the defaults (7 runs per arm, 4 jobs).
#
# Usage: sh scripts/reproduce_third_party.sh [python-with-pytest] [workdir]
set -eu
PY=${1:-python}
WORK=${2:-third-party}
mkdir -p "$WORK"

fetch() {  # name url commit
    if [ ! -d "$WORK/$1/.git" ]; then
        git clone --quiet "$2" "$WORK/$1"
    fi
    git -C "$WORK/$1" checkout --quiet "$3"
}

fetch toolz    https://github.com/pytoolz/toolz.git         451af60dec590a6010e2babdbf391ea8f815122f
fetch sqlparse https://github.com/andialbrecht/sqlparse.git 60cdc649726bf1bc4f1b336050560b336da715ec

here=$(dirname "$0")
"$PY" "$here/inject_and_score.py" "$WORK/toolz" "$WORK/sqlparse" \
    --python "$PY" --runs 7 --jobs 4 --seed 0 --json "$WORK/plants.json"
