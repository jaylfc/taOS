#!/bin/bash

# TinyAgentOS Armbian build extension
# Hooks into the Armbian build process to customise the image.

function extension_prepare_config__tinyagentos() {
    display_alert "TinyAgentOS" "Configuring build" "info"
    EXTRA_IMAGE_SUFFIXES+=("-tinyagentos")
}

function post_customize_image__tinyagentos() {
    display_alert "TinyAgentOS" "Post-customization" "info"
    # Verify critical paths exist in the image
    [[ -d "${SDCARD}/opt/tinyagentos" ]] || \
        display_alert "TinyAgentOS" "/opt/tinyagentos missing — customize-image.sh may have failed" "warn"

    # Create the dedicated 'taos' system user that the controller service runs
    # as.  The service unit in the overlay already references User=taos; without
    # this user present at first boot the service fails to start.
    #
    # chroot into the image target so useradd writes to the image's /etc/passwd
    # rather than the build host.  -r = system account, -M = no home dir,
    # -s /usr/sbin/nologin = non-interactive, -d = home field in passwd (not
    # an actual directory on disk).
    display_alert "TinyAgentOS" "Creating 'taos' system user" "info"
    chroot "${SDCARD}" /bin/bash -c "
        id taos >/dev/null 2>&1 && exit 0
        useradd -r -M -s /usr/sbin/nologin -d /opt/tinyagentos taos \
            || useradd -r -M -s /sbin/nologin -d /opt/tinyagentos taos
    " || display_alert "TinyAgentOS" "useradd taos failed — service will not start as non-root" "warn"

    # Add 'taos' to the incus and docker groups so the controller can reach
    # those sockets without root.  These groups may not exist at image-build
    # time (the packages are installed at first-boot or post-flash), so we
    # create them if absent and add the user idempotently.
    for grp in incus docker; do
        chroot "${SDCARD}" /bin/bash -c "
            getent group ${grp} >/dev/null 2>&1 || groupadd -r ${grp}
            usermod -aG ${grp} taos 2>/dev/null || true
        " || display_alert "TinyAgentOS" "could not add taos to '${grp}' group" "warn"
    done

    # Set ownership of the entire install directory so the 'taos' user can
    # write to .git/, .venv/, and static/desktop/ during non-root in-app
    # self-updates (git pull, pip install -e ., npm run build).
    # Security trade-off: taos owns its own code, which is required for
    # self-update without a root helper.  Full update-privilege-separation
    # (a signed updater suid helper) is a post-beta hardening task.
    # The data directory is then tightened on top so secrets stay 0700/0600.
    if [[ -d "${SDCARD}/opt/tinyagentos" ]]; then
        chroot "${SDCARD}" /bin/bash -c "
            chown -R taos:taos /opt/tinyagentos 2>/dev/null || true
            chmod 0700 /opt/tinyagentos/data 2>/dev/null || true
        "
        display_alert "TinyAgentOS" "Set /opt/tinyagentos ownership to taos:taos; data/ → 0700" "info"
    fi
}
