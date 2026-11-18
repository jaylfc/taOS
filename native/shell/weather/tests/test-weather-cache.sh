#!/bin/sh
# Tests for taos-weather-cache with a stubbed curl (never touches the network).
#   test-weather-cache.sh <weather-dir>
set -u
W=${1:?weather dir}
SCRIPT="$W/taos-weather-cache"
T=$(mktemp -d "${TMPDIR:-/tmp}/taos-weather-test.XXXXXX") || exit 1
trap 'rm -rf "$T"' EXIT
fails=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); }
check() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1: expected '$2' got '$3'"; fi; }

# Stub curl: prints $T/response (or fails when $T/curl-fail exists) and logs its URL.
cat >"$T/curl" <<EOS
#!/bin/sh
for a in "\$@"; do last=\$a; done
echo "\$last" >>"$T/curl.log"
[ -e "$T/curl-fail" ] && exit 22
cat "$T/response"
EOS
chmod +x "$T/curl"
export TAOS_WEATHER_CURL="$T/curl" TAOS_WEATHER_CONF="$T/weather.conf" TAOS_WEATHER_OUT="$T/out/weather.json"
mkdir -p "$T/out"
resp() { # code temp is_day
    printf '{"latitude":0.0,"longitude":0.0,"current_units":{"time":"iso8601","temperature_2m":"C","weather_code":"wmo code","is_day":""},"current":{"time":"2026-11-18T10:15","interval":900,"temperature_2m":%s,"weather_code":%s,"is_day":%s}}' "$2" "$1" "$3" >"$T/response"
}
conf() { printf 'LATITUDE=%s\nLONGITUDE=%s\nPLACE=%s\nUNITS=%s\n' "$1" "$2" "$3" "$4" >"$T/weather.conf"; }
jfield() { sed -n "s/.*\"$1\":\"\{0,1\}\([^\",}]*\).*/\1/p" "$TAOS_WEATHER_OUT"; }

# 1. No config: exit 0, nothing written, curl never called.
rm -f "$T/weather.conf" "$TAOS_WEATHER_OUT" "$T/curl.log"
sh "$SCRIPT"; check "absent config exits 0" 0 $?
[ ! -e "$TAOS_WEATHER_OUT" ] && [ ! -e "$T/curl.log" ] && pass "absent config does nothing" || fail "absent config did something"

# 2. Success.
conf 0.0 0.0 "Example Town" metric
resp 3 8.4 1
sh "$SCRIPT"; check "success exits 0" 0 $?
check "place" "Example Town" "$(jfield place)"
check "temperature" "8.4" "$(jfield temperature)"
check "code" "3" "$(jfield code)"
check "condition" "Overcast" "$(jfield condition)"
check "icon" "cloudy" "$(jfield icon)"
check "units" "metric" "$(jfield units)"
check "is_day" "1" "$(jfield is_day)"
upd=$(jfield updated)
case $upd in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z) pass "updated is ISO 8601 UTC" ;; *) fail "updated format: $upd" ;; esac
url=$(tail -n 1 "$T/curl.log")
case $url in *"latitude=0.0&longitude=0.0&current=temperature_2m,weather_code,is_day&timezone=auto") pass "request URL" ;; *) fail "request URL: $url" ;; esac
[ "$(ls -A "$T/out" | wc -l)" -eq 1 ] && pass "no temp files left" || fail "temp files left: $(ls -A "$T/out")"
[ "$(stat -c %a "$TAOS_WEATHER_OUT")" = 644 ] && pass "mode 0644" || fail "mode"
if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$TAOS_WEATHER_OUT" && pass "output is valid JSON" || fail "output is not JSON"
fi

# 3. Imperial adds the Fahrenheit parameter.
conf 0.0 0.0 "Example Town" imperial
resp 0 61.2 0
sh "$SCRIPT"
case $(tail -n 1 "$T/curl.log") in *'&temperature_unit=fahrenheit') pass "imperial URL" ;; *) fail "imperial URL" ;; esac
check "imperial units" "imperial" "$(jfield units)"

# 4. Every WMO code maps as in wmo-codes.tsv.
conf 0.0 0.0 "Example Town" metric
n=0
while IFS='	' read -r code icon text; do
    case $code in ''|'#'*) continue ;; esac
    resp "$code" 10 1
    sh "$SCRIPT" || { fail "code $code failed"; continue; }
    got="$(jfield icon)|$(jfield condition)"
    [ "$got" = "$icon|$text" ] && n=$((n + 1)) || fail "code $code: expected '$icon|$text' got '$got'"
done <"$W/wmo-codes.tsv"
check "all WMO codes in the table map correctly" "$(grep -cv "^#" "$W/wmo-codes.tsv")" "$n"
resp 42 10 1
cp "$TAOS_WEATHER_OUT" "$T/before"
sh "$SCRIPT"; check "unknown code fails" 1 $?
cmp -s "$T/before" "$TAOS_WEATHER_OUT" && pass "unknown code keeps the old file" || fail "old file changed"

# 5. Malformed responses and curl failure keep the previous file.
for bad in '' 'not json' '{"current":{"temperature_2m":null,"weather_code":3,"is_day":1}}' '{"error":true}' '{"current":{"temperature_2m":9999,"weather_code":3,"is_day":1}}'; do
    printf '%s' "$bad" >"$T/response"
    sh "$SCRIPT" 2>/dev/null; rc=$?
    [ $rc -ne 0 ] && cmp -s "$T/before" "$TAOS_WEATHER_OUT" && pass "bad response rejected: ${bad:-empty}" || fail "bad response accepted: $bad"
done
resp 3 8 1; : >"$T/curl-fail"
sh "$SCRIPT" 2>/dev/null; rc=$?
[ $rc -ne 0 ] && cmp -s "$T/before" "$TAOS_WEATHER_OUT" && pass "curl failure keeps old file" || fail "curl failure handling"
rm -f "$T/curl-fail"

# 6. Config validation.
for badconf in "abc 0" "91 0" "0 181" "0 "; do
    set -- $badconf
    conf "$1" "${2:-}" "X" metric
    sh "$SCRIPT" 2>/dev/null; [ $? -ne 0 ] && pass "rejects lat/lon '$badconf'" || fail "accepted lat/lon '$badconf'"
done
conf 0 0 "X" kelvin
sh "$SCRIPT" 2>/dev/null; [ $? -ne 0 ] && pass "rejects units kelvin" || fail "accepted units kelvin"

# 7. Hostile place text stays valid JSON.
conf 0 0 'Quote " Back \ Slash' metric
resp 1 5 1
sh "$SCRIPT"
if command -v python3 >/dev/null 2>&1; then
    p=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["place"])' "$TAOS_WEATHER_OUT")
    check "place is escaped" 'Quote " Back \ Slash' "$p"
fi
# 8. Comment lines, spaces around keys, no trailing newline.
printf '# comment\n LATITUDE = 0.5 \nLONGITUDE=1\nPLACE=Example Town' >"$T/weather.conf"
sh "$SCRIPT"; check "lenient config parse" 0 $?
check "place from lenient config" "Example Town" "$(jfield place)"

echo "failures: $fails"
[ "$fails" -eq 0 ]
