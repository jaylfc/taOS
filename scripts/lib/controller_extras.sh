taos_controller_extras() {
    local is_handset=0
    if command -v systemctl >/dev/null 2>&1 && systemctl cat taos-kiosk.service >/dev/null 2>&1; then
        is_handset=1
    fi

    local extras="proxy"
    case "${TAOS_EXTRAS_BLE:-}" in
        "1"|"true")
            extras="proxy,ble"
            ;;
        "0"|"false")
            extras="proxy"
            ;;
        *)
            if [[ $is_handset -eq 1 ]]; then
                extras="proxy,ble"
            else
                extras="proxy"
            fi
            ;;
    esac
    echo "$extras"
}