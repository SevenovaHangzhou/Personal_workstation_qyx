"""Exercise independent package identity and the real Linux bootstrap."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from packaging_product import build_product
from typedefine.TypeDefine import APP_VERSION

ROOT=Path(__file__).resolve().parents[1]


class EvaluatorPackagingTests(unittest.TestCase):
    def test_distribution_names_and_storage_do_not_collide(self):
        with patch.dict(os.environ,{'ORBIT_EVALUATOR_BUILD':'1','ORBIT_APP_PROFILE':'work'}):
            product=build_product()
        self.assertEqual((product.slug,product.product,product.app_id,product.data_name),
                         ('orbit-evaluator','evaluator','com.sevenova.OrbitEvaluator','OrbitEvaluator'))
        self.assertEqual(product.entry,'evaluator.py')
        with patch.dict(os.environ,{'ORBIT_EVALUATOR_BUILD':'0','ORBIT_APP_PROFILE':'study'}):
            self.assertEqual(build_product().slug,'luma')
        with patch.dict(os.environ,{'ORBIT_EVALUATOR_BUILD':'1','ORBIT_APP_PROFILE':'study'}):
            with self.assertRaises(ValueError):build_product()

    @unittest.skipIf(os.name=='nt','Linux bash launcher')
    def test_packaged_launcher_selects_evaluator_and_disables_ota(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'PRODUCT').write_text('evaluator\n')
            (root/'VERSION').write_text('0.10.1\n')
            (root/'ota-launch.sh').write_text('orbit_ota_launch() { [[ "$ORBIT_SKIP_OTA" == 1 ]]; }\n')
            binary=root/'orbit-evaluator-bin'
            binary.write_text('#!/usr/bin/env python3\nimport os,json,sys\nprint(json.dumps({k:os.environ.get(k) for k in ["ORBIT_APP_PROFILE","ORBIT_EVALUATOR_BUILD","ORBIT_SKIP_OTA"]}))\nassert sys.argv[1:]==["--version"]\n')
            binary.chmod(0o755)
            launcher=root/'orbit-evaluator';launcher.write_bytes((ROOT/'packaging/launch.sh').read_bytes());launcher.chmod(0o755)
            result=subprocess.run([str(launcher),'--version'],capture_output=True,text=True,timeout=10,check=True)
            self.assertEqual(json.loads(result.stdout),{'ORBIT_APP_PROFILE':'work','ORBIT_EVALUATOR_BUILD':'1','ORBIT_SKIP_OTA':'1'})

    def test_frozen_entry_keeps_independent_name(self):
        result=subprocess.run([__import__('sys').executable,str(ROOT/'evaluator.py'),'--version'],
                              capture_output=True,text=True,timeout=20,check=True)
        self.assertIn('EVALUATOR '+APP_VERSION,result.stdout)
