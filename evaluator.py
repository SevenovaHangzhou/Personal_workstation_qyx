"""Isolated evaluator branch entry; explicit --data-dir can use a work space."""
import os
from pathlib import Path
import sys

os.environ['ORBIT_APP_PROFILE'] = 'work'
os.environ['ORBIT_EVALUATOR_BUILD'] = '1'
os.environ['ORBIT_SKIP_OTA'] = '1'

if __name__ == '__main__':
    arguments = sys.argv[1:]
    if not any(a == '--data-dir' or a.startswith('--data-dir=') for a in arguments) and '--self-test' not in arguments:
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData/Local') if os.name == 'nt' else Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')
        sys.argv.extend(['--data-dir', str(base/'Sevenova/OrbitEvaluator')])
    from main import main
    raise SystemExit(main())
