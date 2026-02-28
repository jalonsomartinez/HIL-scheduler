#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env.public-dashboard"
VENV_ACTIVATE="${REPO_ROOT}/venv/bin/activate"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}"
  echo "Create it from ${REPO_ROOT}/.env.public-dashboard.example and set credentials."
  exit 1
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"

if [[ -z "${HIL_PUBLIC_DASH_USER:-}" || -z "${HIL_PUBLIC_DASH_PASS:-}" ]]; then
  echo "HIL_PUBLIC_DASH_USER and HIL_PUBLIC_DASH_PASS must be set in ${ENV_FILE}."
  exit 1
fi

if [[ ! -f "${VENV_ACTIVATE}" ]]; then
  echo "Missing virtual environment activate script: ${VENV_ACTIVATE}"
  echo "Create it first: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

# shellcheck disable=SC1090
source "${VENV_ACTIVATE}"
exec python3 "${REPO_ROOT}/hil_scheduler.py"
