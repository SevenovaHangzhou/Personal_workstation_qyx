"""LUMA learning application entry point."""
import os
os.environ['ORBIT_APP_PROFILE']='study'
from main import main
if __name__=='__main__': raise SystemExit(main())
