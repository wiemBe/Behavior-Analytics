# The qradar-mcp image the stack runs: IBM/qradar-mcp at the pinned commit, unmodified,
# plus one file. `upstream` is a BuildKit named context holding the build of ./qradar-mcp
# with its own Dockerfile (compose: `additional_contexts`, release workflow:
# `build-contexts`). Nothing of the upstream Dockerfile is repeated here.
#
# feature_toggles.json is baked in rather than bind-mounted. On an SELinux-enforcing host
# the checkout is user_home_t, which container_t cannot read; the server then refuses to
# start (see docs/OPERATIONS.md OPS-R13). COPY puts the file in an image layer
# (container_file_t), so nothing on the host is touched and SELinux stays enforcing.
# Upstream ships POST and DELETE enabled; this file switches the whole write surface off
# (docs/mcp-capability-matrix.md section 3).
FROM upstream

# The server runs as appuser (uid 1001); the file is read-only for everybody.
COPY --chown=1001:1001 --chmod=0444 feature_toggles.json /opt/app-root/qradar-mcp/feature_toggles.json

LABEL org.opencontainers.image.title="qradar-mcp" \
      org.opencontainers.image.description="IBM/qradar-mcp at the pinned commit, unmodified, with feature_toggles.json replaced (writes off). Apache-2.0. Not an IBM release."
