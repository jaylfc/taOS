import subprocess
import os
import sys

def run_tests(worktree_path):
    """Run sparkle tests and capture output"""
    test_script = os.path.join(worktree_path, 'tests', 'sparkle_tests.bats')
    
    if not os.path.exists(test_script):
        return f"ERROR: Test script not found: {test_script}", 1
    
    # Try to run bats directly if available
    try:
        result = subprocess.run(
            ['bats', test_script],
            cwd=worktree_path,
            capture_output=True,
            text=True
        )
        return result.stdout + result.stderr, result.returncode
    except FileNotFoundError:
        # If bats is not available, try to run the bash script directly
        try:
            result = subprocess.run(
                [sys.executable, '-c', f"""
import os
os.chdir('{worktree_path}')
exec(open('{test_script}').read())
"""],
                capture_output=True,
                text=True
            )
            return result.stdout + result.stderr, result.returncode
        except Exception as e:
            return f"ERROR running tests: {e}", 1

if __name__ == '__main__':
    # Run tests on cleanup_final
    worktree = '/tmp/cleanup_final'
    print(f"Running sparkle tests in {worktree}...")
    output, exit_code = run_tests(worktree)
    print(output)
    print(f"Exit code: {exit_code}")
