"""Generate a reviewable LaunchAgent; installing it is an explicit separate command."""
import argparse
from pathlib import Path
import plistlib
import shutil
import os
import subprocess

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=root / 'data/com.finance-tracker.start.plist')
parser.add_argument('--install', action='store_true', help='Install and load the reviewed user LaunchAgent')
args = parser.parse_args()
docker = shutil.which('docker')
if not docker:
    candidates = [Path.home() / '.docker/bin/docker',
                  Path('/Applications/Docker.app/Contents/Resources/bin/docker')]
    docker = next((str(path) for path in candidates if path.is_file()), None)
if not docker:
    raise SystemExit('找不到 docker，請先安裝 Docker Desktop 或 OrbStack。')
args.output.parent.mkdir(parents=True, exist_ok=True)
runtime = Path.home() / 'Library/Application Support/FinanceTracker'
log_dir = runtime / 'logs'
config = {
    'Label': 'com.finance-tracker.start',
    'ProgramArguments': ['/usr/bin/python3', str(runtime / 'autostart.py'), '--docker', docker,
                         '--state-dir', str(runtime)],
    'WorkingDirectory': str(runtime), 'RunAtLoad': True,
    'StartInterval': 60, 'ProcessType': 'Background',
    'EnvironmentVariables': {'PATH': str(Path(docker).parent) + ':/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin'},
    'StandardOutPath': str(log_dir / 'launchd.log'),
    'StandardErrorPath': str(log_dir / 'launchd-error.log'),
}
with args.output.open('wb') as file:
    plistlib.dump(config, file)
print(args.output)
if args.install:
    target = Path.home() / 'Library/LaunchAgents/com.finance-tracker.start.plist'
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = plistlib.loads(target.read_bytes())
        if existing.get('Label') != config['Label']:
            raise SystemExit('Unexpected existing LaunchAgent; refusing replacement.')
        subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}', str(target)], check=False)
    log_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / 'scripts/autostart.py', runtime / 'autostart.py')
    (runtime / 'autostart.py').chmod(0o600)
    shutil.copyfile(args.output, target)
    target.chmod(0o644)
    subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(target)], check=True)
    print('Installed and loaded com.finance-tracker.start.')
