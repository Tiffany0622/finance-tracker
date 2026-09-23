"""Generate a reviewable LaunchAgent; installing it is an explicit separate command."""
import argparse
from pathlib import Path
import plistlib
import shutil

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=root / 'data/com.finance-tracker.start.plist')
args = parser.parse_args()
docker = shutil.which('docker')
if not docker:
    raise SystemExit('找不到 docker，請先安裝 Docker Desktop 或 OrbStack。')
args.output.parent.mkdir(parents=True, exist_ok=True)
log_dir = root / 'data/logs'
log_dir.mkdir(parents=True, exist_ok=True)
config = {
    'Label': 'com.finance-tracker.start',
    'ProgramArguments': ['/bin/sh', str(root / 'scripts/start.sh'), '--no-build'],
    'WorkingDirectory': str(root), 'RunAtLoad': True,
    'StartInterval': 300, 'ProcessType': 'Background',
    'EnvironmentVariables': {'PATH': str(Path(docker).parent) + ':/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin'},
    'StandardOutPath': str(log_dir / 'launchd.log'),
    'StandardErrorPath': str(log_dir / 'launchd-error.log'),
}
with args.output.open('wb') as file:
    plistlib.dump(config, file)
print(args.output)
