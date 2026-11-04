#!/bin/bash
#
# Renders charts/*/values-template.yaml.j2 against each platform's env/*.yaml.

set -euo pipefail

GITHUB_PUSH=${GITHUB_PUSH:-false}

SHELL_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

GIT_USERNAME="nalbam-bot"
GIT_USEREMAIL="bot@nalbam.com"

cd "${SHELL_DIR}/.."

if [ "${GITHUB_PUSH}" == "true" ]; then
  if [ -n "$(git status --porcelain)" ]; then
    echo "Publishing requires a clean working tree and index." >&2
    exit 1
  fi
  if [ "$(git branch --show-current)" != "${MAIN_BRANCH:-main}" ]; then
    echo "Publishing requires the ${MAIN_BRANCH:-main} branch." >&2
    exit 1
  fi
  git fetch origin "${MAIN_BRANCH:-main}"
  if [ "$(git rev-list --count FETCH_HEAD..HEAD)" != 0 ]; then
    echo "Publishing requires no unpublished commits." >&2
    exit 1
  fi
  git merge --ff-only FETCH_HEAD
fi

GENERATED_VALUES=()
shopt -s nullglob
# find charts
for PLATFORM in eks local; do
  for CHART in charts/*/; do
    if [ -f "${CHART}/values-template.yaml.j2" ] && [ -d "${CHART}/${PLATFORM}" ]; then
      echo
      echo "Processing.. ${PLATFORM}/$(basename "${CHART}")"
      python3 "${SHELL_DIR}/gen_values.py" -p "${PLATFORM}" -r "$(basename "${CHART}")"
      GENERATED_VALUES+=("${CHART}/${PLATFORM}"/values-*.yaml)
    fi
  done
done

if [ "${GITHUB_PUSH}" == "true" ]; then
  # Nothing is published until both the rendered charts and Python contracts pass.
  python3 "${SHELL_DIR}/validate.py" --all-charts
  python3 -m pytest

  if [ "${#GENERATED_VALUES[@]}" -eq 0 ]; then
    echo "No generated values to publish."
    exit 0
  fi
  git config user.name "${GIT_USERNAME}"
  git config user.email "${GIT_USEREMAIL}"

  git add -- "${GENERATED_VALUES[@]}"

  if git diff --cached --quiet; then
    echo
    echo "Nothing to commit."
    exit 0
  fi

  echo
  echo "Pushing to GitHub..."

  git commit -m "$(date +%Y%m%d-%H%M)"
  git push origin "HEAD:${MAIN_BRANCH:-main}"
fi
