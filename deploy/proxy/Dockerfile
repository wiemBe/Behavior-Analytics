# The bundled TLS proxy (docs/OPERATIONS-DESIGN.md section 3.4).
#
# The config is baked in rather than bind-mounted, for the same reason as
# deploy/postgres/Dockerfile: the checkout is user_home_t on an SELinux host and
# a container cannot read a bind mount of it. The certificate and key are not in
# the image. They arrive in the `proxy_certs` volume, which only this container
# mounts, so the backend never sees the application's private key.
FROM nginx:1.27-alpine

COPY default.conf /etc/nginx/conf.d/default.conf
