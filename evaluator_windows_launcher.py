"""Independent administrator launcher; never follow employee OTA pointers."""
import os
os.environ['ORBIT_APP_PROFILE'] = 'work'
os.environ['ORBIT_EVALUATOR_BUILD'] = '1'
os.environ['ORBIT_SKIP_OTA'] = '1'
from windows_launcher import main
if __name__ == '__main__':
    raise SystemExit(main())
