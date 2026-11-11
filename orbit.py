"""ORBIT work application entry point."""
import os
os.environ['ORBIT_APP_PROFILE']='work'
from main import main
if __name__=='__main__': raise SystemExit(main())
