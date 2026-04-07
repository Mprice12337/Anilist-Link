#!/bin/sh
# Container entrypoint — copies dev tools into /config if this is a dev build,
# then execs the main command.

if [ -d /opt/dev-tools ] && [ "$(ls /opt/dev-tools/*.sh 2>/dev/null)" ]; then
    mkdir -p /config/dev-tools
    cp /opt/dev-tools/*.sh /config/dev-tools/
    chmod +x /config/dev-tools/*.sh
    echo "[entrypoint] Dev tools installed to /config/dev-tools/"
fi

exec "$@"
