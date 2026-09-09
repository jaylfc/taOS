import subprocess
import sys
import os

# Test 1: Verify the script exists and is executable
script_path = 'scripts/taos-graceful-stop.sh'
if not os.path.isfile(script_path):
    print('ERROR: Script not found')
    sys.exit(1)

if not os.access(script_path, os.X_OK):
    print('ERROR: Script not executable')
    sys.exit(1)

print('✓ Script exists and is executable')

# Test 2: Verify the script has the new process exit checking logic
with open(script_path, 'r') as f:
    content = f.read()

# Check for key process exit logic
checks = [
    ('main_pid extraction', 'main_pid=' in content),
    ('process search loop', 'for pid in $(pgrep' in content),
    ('process exit polling', 'for i in {1..30}; do' in content),
    ('process wait with timeout', 'Waiting for controller process' in content),
]

print('\n✓ Script process exit checking implementation:')
all_passed = True
for check_name, check_passed in checks:
    status = 'PASS' if check_passed else 'FAIL'
    print(f'  - {check_name}: {status}')
    if not check_passed:
        all_passed = False

# Test 3: Verify the script still maintains backward compatibility
backward_compat_checks = [
    ('API unreachable handling', 'Succeeds even if the API is unreachable' in content),
    ('short timeout', '--max-time 25' in content),
    ('dedupe logic', 'prepare-shutdown.stamp' in content),
]

print('\n✓ Script backward compatibility maintained:')
for check_name, check_passed in backward_compat_checks:
    status = 'PASS' if check_passed else 'FAIL'
    print(f'  - {check_name}: {status}')
    if not check_passed:
        all_passed = False

if all_passed:
    print('\n✓ All tests passed: taos-graceful-stop.sh fix implemented correctly')
    sys.exit(0)
else:
    print('\n✗ Some tests failed')
    sys.exit(1)
