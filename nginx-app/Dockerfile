# Pin to a specific digest so the base image can never silently change
FROM nginx:alpine@sha256:8b1e78743a03dbb2c95171cc58639fef29abc8816598e27fb910ed2e621e589a

LABEL org.opencontainers.image.source="https://github.com/it-scholar/k8s-seminar" \
      org.opencontainers.image.description="Static Hello World served by nginx" \
      org.opencontainers.image.licenses="MIT"

# Apply all available OS-level security patches before adding any content
RUN apk upgrade --no-cache

# Replace the default config with a hardened one (non-root port, security headers)
COPY nginx.conf /etc/nginx/nginx.conf

# Copy static content and lock down permissions
COPY index.html /usr/share/nginx/html/index.html
RUN chown -R nginx:nginx /usr/share/nginx/html \
 && chmod -R 755 /usr/share/nginx/html \
    # Give nginx user write access to log and cache dirs
 && chown -R nginx:nginx /var/cache/nginx \
 && chown -R nginx:nginx /var/log/nginx \
    # Pre-create /tmp dirs needed for non-root nginx temp paths
 && mkdir -p /tmp/client_body /tmp/proxy /tmp/fastcgi /tmp/uwsgi /tmp/scgi \
 && chown -R nginx:nginx /tmp/client_body /tmp/proxy /tmp/fastcgi /tmp/uwsgi /tmp/scgi

# Drop root – the process must never run as UID 0
USER nginx

# Expose the non-privileged port declared in nginx.conf
EXPOSE 8080

# Fail-fast health check so orchestrators know when the container is unhealthy
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO /dev/null http://localhost:8080/ || exit 1

