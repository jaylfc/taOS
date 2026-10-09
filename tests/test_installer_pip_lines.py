import glob
import os

def test_installer_pip_install_lines_do_not_swallow_failure():
    script_dir = 'tinyagentos/scripts'
    install_scripts = glob.glob(os.path.join(script_dir, 'install_*.sh'))
    assert install_scripts, f"No installer scripts found in {script_dir}"
    
    for script_path in install_scripts:
        with open(script_path, 'r') as f:
            lines = f.readlines()
        for line_num, line in enumerate(lines, start=1):
            stripped = line.rstrip('\n\r')
            if stripped.startswith('pip3 install '):
                # Check that the line does not end with '|| true'
                assert not stripped.endswith('|| true'), \
                    f"Line {line_num} in {script_path} ends with '|| true': {stripped}"