"""Launch existing project containers without reading a protected Documents folder."""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import time
import urllib.request


def run(docker, state):
    if (state / 'maintenance').exists():
        return

    def call(*args, timeout=10):
        return subprocess.run([docker, *args], capture_output=True, timeout=timeout)

    def ready():
        try:
            with urllib.request.urlopen('http://localhost:8080/api/health/ready', timeout=5) as response:
                return json.load(response).get('message') == 'ready'
        except (OSError, ValueError):
            return False

    try:
        available = call('info').returncode == 0
    except subprocess.TimeoutExpired:
        available = False
    if not available:
        print('Starting Docker Desktop for Finance Tracker.', flush=True)
        call('desktop', 'start', '--detach', timeout=30).check_returncode()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if call('info').returncode == 0:
                    break
            except subprocess.TimeoutExpired:
                pass
            time.sleep(3)
        else:
            raise SystemExit('Docker not ready; retry on the next launchd run.')
    names = ['finance-tracker-' + service + '-1' for service in ('db', 'api', 'worker', 'web')]
    containers = call('inspect', '--format', '{{json .State.Running}} {{index .Config.Labels "com.docker.compose.project"}}', *names)
    containers.check_returncode()
    rows = [line.decode().split() for line in containers.stdout.splitlines()]
    if len(rows) != 4 or any(len(row) != 2 or row[1] != 'finance-tracker' for row in rows):
        raise SystemExit('Container ownership mismatch; refusing to start.')
    if all(row[0] == 'true' for row in rows) and ready():
        return
    call('start', names[0], timeout=30).check_returncode()
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        result = call('inspect', '--format', '{{.State.Health.Status}}', names[0])
        if result.stdout.strip() == b'healthy':
            break
        time.sleep(3)
    else:
        raise SystemExit('Database not ready; retry on the next launchd run.')
    call('start', *names[1:], timeout=60).check_returncode()
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if ready():
            print('Finance Tracker ready.', flush=True)
            return
        time.sleep(3)
    raise SystemExit('Web not ready; retry on the next launchd run.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--docker', required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    args = parser.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    with (args.state_dir / 'autostart.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(0)
        run(args.docker, args.state_dir)
