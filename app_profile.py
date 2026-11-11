"""Immutable product boundaries, selected by the executable entry point."""
from dataclasses import dataclass
import os

@dataclass(frozen=True)
class AppProfile:
    key: str
    name: str
    wordmark: str
    categories: tuple
    data_name: str
    app_id: str
    work: bool
    study: bool
    @property
    def display_name(self): return self.name+' / '+self.wordmark
    @property
    def slug(self): return 'luma' if self.key=='study' else 'orbit'

PROFILES = {
    'work': AppProfile('work','星序','ORBIT',('work','demo','life'),'OrbitWork','com.sevenova.Orbit',True,False),
    'study': AppProfile('study','知序','LUMA',('learn',),'Luma','com.sevenova.Luma',False,True),
    # Only for compatibility tests and tools that import the old combined API.
    'combined': AppProfile('combined','星序','ORBIT',('work','learn','demo','life'),'Orbit','com.sevenova.Orbit',True,True),
}

def profile(value=None):
    if isinstance(value,AppProfile): return value
    key=value or os.environ.get('ORBIT_APP_PROFILE','combined')
    if key not in PROFILES: raise ValueError('未知应用类型：'+str(key))
    return PROFILES[key]

PROFILE=profile()
