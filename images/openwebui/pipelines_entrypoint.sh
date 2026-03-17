#!/bin/sh
set -eu

SRC_DIR="${PIPELINES_BUNDLED_DIR:-/opt/ukbgpt/pipelines-src}"
DST_DIR="${PIPELINES_DIR:-/app/pipelines}"

mkdir -p "${DST_DIR}"

rm -f "${DST_DIR}/rate_limit_filter_pipeline.py"

for src in "${SRC_DIR}"/*.py; do
    [ -e "${src}" ] || continue
    install -m 0644 "${src}" "${DST_DIR}/$(basename "${src}")"
done

exec bash /app/start.sh --mode run
