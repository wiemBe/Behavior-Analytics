#!/usr/bin/env bash
# Load the QRadar SEC token and CA bundle into the `qradar_secrets` volume.
#
# Why not a bind mount of ./.secrets?
#   On an SELinux-enforcing host the repository is labelled user_home_t, which
#   no container process can read — not even root in the container. The usual
#   answer is a `:z`/`:Z` mount, but that relabels the host source tree as a
#   side effect. Streaming the files in over stdin touches no host label at all,
#   so SELinux stays enforcing and ./.secrets keeps its 0600/user_home_t state.
#
# Re-running is safe: it overwrites both files in place.
#
#   ./deploy/load-secrets.sh            # QRadar token + CA (the default)
#   ./deploy/load-secrets.sh proxy      # TLS certificate + key for the bundled proxy
#   ./deploy/load-secrets.sh all
#
# The proxy certificate goes into its own volume, never the QRadar one, because
# the backend mounts the QRadar volume and has no use for the application's
# private key. SECRETS_VOLUME and PROXY_CERTS_VOLUME override the volume names
# (the installer sets them when a second stack shares the host).
set -euo pipefail

cd "$(dirname "$0")/.."

# Must match volumes.qradar_secrets.name in docker-compose.yml.
VOLUME=${SECRETS_VOLUME:-qradar-observability-secrets}
PROXY_VOLUME=${PROXY_CERTS_VOLUME:-qradar-observability-proxy-certs}
TOKEN_SRC=.secrets/qradar.sec
CA_SRC=.secrets/qradar-ca.pem

# uid of `appuser` in backend/Dockerfile. The files are mounted read-only and
# must be readable by that account and by nobody else.
APP_UID=10001

load_qradar() {
  local f
  for f in "$TOKEN_SRC" "$CA_SRC"; do
    [ -r "$f" ] || { echo "missing: $f" >&2; exit 1; }
  done

  docker volume create "$VOLUME" >/dev/null

  # One container, both files over a tar stream on stdin. The token never appears
  # in argv, so it stays out of `ps` output and the shell history.
  tar -cf - -C .secrets qradar.sec qradar-ca.pem |
    docker run -i --rm -v "$VOLUME:/dst" alpine sh -c '
      set -e
      tar -xf - -C /dst
      mv /dst/qradar.sec     /dst/qradar_sec_token
      mv /dst/qradar-ca.pem  /dst/qradar_ca.pem
      chown '"$APP_UID"':'"$APP_UID"' /dst/qradar_sec_token /dst/qradar_ca.pem
      chmod 400 /dst/qradar_sec_token
      chmod 444 /dst/qradar_ca.pem
    '

  echo "loaded into $VOLUME:"
  docker run --rm -v "$VOLUME:/dst" alpine ls -l /dst
}

load_proxy() {
  local f
  for f in .secrets/tls.crt .secrets/tls.key; do
    [ -r "$f" ] || { echo "missing: $f" >&2; exit 1; }
  done
  docker volume create "$PROXY_VOLUME" >/dev/null
  # The proxy master process runs as root and reads both files.
  tar -cf - -C .secrets tls.crt tls.key |
    docker run -i --rm -v "$PROXY_VOLUME:/dst" alpine sh -c '
      set -e
      tar -xf - -C /dst
      chown 0:0 /dst/tls.crt /dst/tls.key
      chmod 444 /dst/tls.crt
      chmod 400 /dst/tls.key
    '
  echo "loaded into $PROXY_VOLUME:"
  docker run --rm -v "$PROXY_VOLUME:/dst" alpine ls -l /dst
}

case "${1:-qradar}" in
  qradar) load_qradar ;;
  proxy) load_proxy ;;
  all) load_qradar; load_proxy ;;
  *) echo "usage: $0 [qradar|proxy|all]" >&2; exit 2 ;;
esac
