#!/bin/sh
set -eu

if [ "${1:-}" = "sh" ] || [ "${1:-}" = "/bin/sh" ]; then
    shift
    exec /bin/sh "$@"
fi

exec uvicorn main:app "$@"
