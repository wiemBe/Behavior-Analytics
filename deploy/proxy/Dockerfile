# The bundled TLS proxy (docs/OPERATIONS-DESIGN.md section 3.4).
#
# The config is baked in rather than bind-mounted, for the same reason as
# deploy/postgres/Dockerfile: the checkout is user_home_t on an SELinux host and
# a container cannot read a bind mount of it. The certificate and key are not in
# the image. They arrive in the `proxy_certs` volume, which only this container
# mounts, so the backend never sees the application's private key.
# The base is pinned by digest (docs/DEVELOPMENT.md "Base image pins"); the tag
# stays for readability.
FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10

COPY default.conf /etc/nginx/conf.d/default.conf

# Build-time identity, declared after the COPY so a new version or SHA does not
# invalidate the cached layer.
ARG APP_VERSION
ARG GIT_SHA
LABEL org.opencontainers.image.version=$APP_VERSION \
      org.opencontainers.image.revision=$GIT_SHA
