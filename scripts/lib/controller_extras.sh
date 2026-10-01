#!/bin/bash

# Script to compute the controller extras based on device type and environment variable
# This is the shell helper that the installer compares against

taos_controller_extras() {
    local is_handset=0
    if systemctl cat taos-kiosk.service >/dev/null 2>&1; then
        is_handset=1
    fi

    local extras=""
    case "${TAOS_EXTRAS_BLE:-}" in
        "1"|"true")
            extras="ble"
            ;;
        "0"|"false")
            extras=""
            ;;
        *)
            if [[ $is_handset -eq 1 ]]; then
                extras="ble"
            else
                extras=""
            fi
            ;;
    esac
    echo "$extras"
}
