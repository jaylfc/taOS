# Images for the device-link test bed.
#
#   target net   tailscale + tools + the bed scripts. Used by the relay stand-in
#                and the personal-tailnet stand-in (no taOS inside).
#   target node  net + the taOS controller installed from the named build
#                context "taos" (a checkout or `git archive` of the taOS ref
#                under test; bed.sh supplies it). Used by every taOS node.
#
# tailscale 1.102.4 is the version the two-tailscaled test measured.
ARG TS_VERSION=1.102.4
FROM tailscale/tailscale:v${TS_VERSION} AS ts

FROM python:3.12-slim AS net
COPY --from=ts /usr/local/bin/tailscale /usr/local/bin/tailscaled /usr/local/bin/
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         ca-certificates curl iproute2 iptables nftables netcat-openbsd \
         procps util-linux iputils-ping \
    && rm -rf /var/lib/apt/lists/*
COPY bed-stub.py node-init.sh mesh-join.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/node-init.sh /usr/local/bin/mesh-join.sh \
    && mkdir -p /run/taos/mesh /var/lib/taos/mesh
# The two-tailscaled test forced nftables (the stock image's iptables is the
# legacy backend and many kernels have no legacy tables).
ENV TS_DEBUG_FIREWALL_MODE=nftables
ENTRYPOINT ["/usr/local/bin/node-init.sh"]

FROM net AS node
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libsqlcipher-dev git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=taos / /src
# Editable, so PROJECT_DIR is /src (the controller resolves its data dir from it).
RUN pip install --no-cache-dir -e /src \
    # The web desktop bundle is not built here (the test suite stubs it the same
    # way); the controller's API does not need it.
    && mkdir -p /src/static/desktop \
    && { [ -f /src/static/desktop/index.html ] || echo '<!doctype html><title>bed</title>' > /src/static/desktop/index.html; }
