# The init scripts are baked in rather than bind-mounted.
#
# On an SELinux-enforcing host the repository is labelled user_home_t, which
# container_t cannot read: the upstream entrypoint dies on `ls
# /docker-entrypoint-initdb.d/` with "Permission denied" and Postgres never
# boots. The usual workaround is a `:z`/`:Z` mount, but that relabels the host
# source tree as a side effect. COPY puts the files in an image layer
# (container_file_t), so nothing on the host is touched and SELinux stays
# enforcing.
# The base is pinned by digest (docs/DEVELOPMENT.md "Base image pins"); the tag
# stays for readability.
FROM timescale/timescaledb:2.17.2-pg16@sha256:4e459e217f00cbb09920c34d245501e63427e6767a495de57ce76823ff280f12

COPY init/ /docker-entrypoint-initdb.d/

# Build-time identity, declared after the COPY so a new version or SHA does not
# invalidate the cached layer.
ARG APP_VERSION
ARG GIT_SHA
LABEL org.opencontainers.image.version=$APP_VERSION \
      org.opencontainers.image.revision=$GIT_SHA
