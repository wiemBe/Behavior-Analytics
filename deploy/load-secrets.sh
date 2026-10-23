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

# The helper image that touches the token, the CA and the TLS key. Pinned by
# digest (the tag stays for readability): a moved `alpine` tag would run as
# root on a secrets volume. This is the only place the reference is written;
# `qradar-dash` reads it here for the backup, the way `redis_ref` reads the
# production compose file. How to bump it: docs/DEVELOPMENT.md "Base image pins".
SECRETS_HELPER_IMAGE=alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc

load_qradar() {
  local f
  for f in "$TOKEN_SRC" "$CA_SRC"; do
    [ -r "$f" ] || { echo "missing: $f" >&2; exit 1; }
  done

  docker volume create "$VOLUME" >/dev/null

  # One container, both files over a tar stream on stdin. The token never appears
  # in argv, so it stays out of `ps` output and the shell history.
  #
  # Hardening: no network, every capability dropped, no setuid route back. The
  # copy adds CHOWN back — it is the only capability the chown below needs:
  # `tar -o` extracts without restoring the host's ownership (busybox and GNU
  # tar both read `-o` that way), so the files stay root-owned until then and
  # the chmods run as their owner.
  tar -cf - -C .secrets qradar.sec qradar-ca.pem |
    docker run -i --rm --network none --cap-drop ALL --cap-add CHOWN \
      --security-opt no-new-privileges -v "$VOLUME:/dst" \
      "$SECRETS_HELPER_IMAGE" sh -c '
      set -e
      tar -xf - -o -C /dst
      mv /dst/qradar.sec     /dst/qradar_sec_token
      mv /dst/qradar-ca.pem  /dst/qradar_ca.pem
      chmod 400 /dst/qradar_sec_token
      chmod 444 /dst/qradar_ca.pem
      chown '"$APP_UID"':'"$APP_UID"' /dst/qradar_sec_token /dst/qradar_ca.pem
    '

  # The listing only reads, so the volume is mounted read-only and nothing is
  # added back to the capability drop.
  echo "loaded into $VOLUME:"
  docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
    -v "$VOLUME:/dst:ro" "$SECRETS_HELPER_IMAGE" ls -l /dst
}

load_proxy() {
  local f
  for f in .secrets/tls.crt .secrets/tls.key; do
    [ -r "$f" ] || { echo "missing: $f" >&2; exit 1; }
  done
  docker volume create "$PROXY_VOLUME" >/dev/null
  # The proxy master process runs as root and reads both files. Same hardening
  # as load_qradar: offline, capability-free except the chown, and `tar -o` so
  # the chmods run as the owner of the extracted files.
  tar -cf - -C .secrets tls.crt tls.key |
    docker run -i --rm --network none --cap-drop ALL --cap-add CHOWN \
      --security-opt no-new-privileges -v "$PROXY_VOLUME:/dst" \
      "$SECRETS_HELPER_IMAGE" sh -c '
      set -e
      tar -xf - -o -C /dst
      chmod 444 /dst/tls.crt
      chmod 400 /dst/tls.key
      chown 0:0 /dst/tls.crt /dst/tls.key
    '
  # Read-only listing, nothing added back.
  echo "loaded into $PROXY_VOLUME:"
  docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
    -v "$PROXY_VOLUME:/dst:ro" "$SECRETS_HELPER_IMAGE" ls -l /dst
}

case "${1:-qradar}" in
  qradar) load_qradar ;;
  proxy) load_proxy ;;
  all) load_qradar; load_proxy ;;
  *) echo "usage: $0 [qradar|proxy|all]" >&2; exit 2 ;;
esac
