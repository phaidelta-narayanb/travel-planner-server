#!/bin/sh
set -eu

if [ "${1:-}" = "sh" ] || [ "${1:-}" = "/bin/sh" ] || [ "${1:-}" = "/bin/bash" ]; then
    shift
    exec /bin/bash "$@"
fi

exec uvicorn main:app "$@"
