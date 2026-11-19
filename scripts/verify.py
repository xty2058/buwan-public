"""Run isolated engineering checks, without touching the live database."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'MVP1.0/步骤4-前后端与数据基础'

def main():
    output = ROOT / 'artifacts'
    output.mkdir(exist_ok=True)
    python = APP / '.venv/bin/python3'
    node = shutil.which('node')
    commands = [[str(python) if python.exists() else sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests']]
    if node:
        commands += [[node, '--test', *[str(p.relative_to(APP)) for p in sorted((APP/'tests').glob('*.cjs'))]], [node, '--check', 'frontend/app.js']]
    checks = []
    for index, command in enumerate(commands):
        print('Running:', ' '.join(command), flush=True)
        try:
            result = subprocess.run(command, cwd=APP, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
            code, log = result.returncode, result.stdout
        except subprocess.TimeoutExpired:
            code, log = 124, 'Check exceeded 180 seconds; not passed.\n'
        (output/f'check-{index+1}.txt').write_text(log)
        checks.append({'command': command, 'exit_code': code, 'log': f'artifacts/check-{index+1}.txt'})
        print('PASS' if code == 0 else 'FAIL', flush=True)
    passed = bool(node) and all(c['exit_code'] == 0 for c in checks)
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'passed': passed, 'missing_node': not bool(node), 'checks': checks, 'scope': 'isolated engineering only; no live quality acceptance'}
    (output/'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    return 0 if passed else 1

if __name__ == '__main__':
    sys.exit(main())
