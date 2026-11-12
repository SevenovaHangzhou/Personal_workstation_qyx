"""Distribution identity; Evaluator keeps its existing work-profile data format."""
import os
from types import SimpleNamespace
from app_profile import profile


def build_product():
    app = profile(os.environ.get('ORBIT_APP_PROFILE', 'work'))
    evaluator = os.environ.get('ORBIT_EVALUATOR_BUILD') == '1'
    if evaluator and app.key != 'work':
        raise ValueError('Evaluator packages require the work runtime profile')
    return SimpleNamespace(
        key=app.key,
        product='evaluator' if evaluator else app.key,
        slug='orbit-evaluator' if evaluator else app.slug,
        entry='evaluator.py' if evaluator else app.slug+'.py',
        name='星序评估' if evaluator else app.name,
        wordmark='EVALUATOR' if evaluator else app.wordmark,
        display_name='星序评估 / EVALUATOR' if evaluator else app.display_name,
        data_name='OrbitEvaluator' if evaluator else app.data_name,
        app_id='com.sevenova.OrbitEvaluator' if evaluator else app.app_id,
        icon_slug='orbit' if evaluator else app.slug,
    )
