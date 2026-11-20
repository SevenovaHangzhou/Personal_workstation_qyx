'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { spawnSync } = require('node:child_process');
const cli = require('../bin/orbit-codex.cjs');
const root = path.resolve(__dirname, '..');
const pkg = require('../package.json');
let passed = 0;
function test(name, check) {
  check(); passed++;
  process.stdout.write('ok ' + name + '\n');
}

test('package and plugin versions agree; npm installation has no side effects', () => {
  assert.equal(pkg.version, require('../plugins/codex-orbit/plugin.json').version);
  assert.ok(pkg.private);
  assert.equal(pkg.engines.node, '>=18');
  assert.equal(pkg.bin['orbit-codex'], 'bin/orbit-codex.cjs');
  assert.deepEqual(Object.keys(pkg.scripts), ['test']);
  for (const name of pkg.files) {
    assert.ok(fs.statSync(path.join(root, name)).isFile(), name);
    assert.ok(!/settings\.json|\.credentials|__pycache__|\.venv|\.sqlite/.test(name), name);
  }
});

test('help and version work without Python', () => {
  for (const args of [['--version'], ['--help'], []]) {
    const result = spawnSync(process.execPath, [path.join(root,'bin/orbit-codex.cjs'),...args], {
      encoding:'utf8',env:{...process.env,PATH:''},
    });
    assert.equal(result.status,0,result.stderr);
    assert.ok(result.stdout.includes(pkg.version));
  }
});

test('unknown command does not run Python', () => {
  const result=spawnSync(process.execPath,[path.join(root,'bin/orbit-codex.cjs'),'publish'],{encoding:'utf8',env:{...process.env,PATH:''}});
  assert.equal(result.status,2);
  assert.ok(result.stderr.includes('未知命令'));
});

test('explicit Python paths and equal syntax are preserved', () => {
  const name=path.resolve('研发 space/py$(echo untouched);thon');
  assert.deepEqual(cli.pythonCandidates(['--python',name]),[[name]]);
  assert.deepEqual(cli.pythonCandidates(['--python='+name]),[[name]]);
  assert.throws(()=>cli.explicitPython(['--python','--headless']));
  assert.throws(()=>cli.explicitPython(['--python=']));
  const probes=[];
  const selected=cli.selectPython(['--python',name],(command,args,options)=>{
    probes.push({command,args,options});
    return {status:0,stdout:JSON.stringify({executable:name,supported:true})};
  });
  assert.equal(selected,name);
  assert.equal(probes[0].command,name);
  assert.equal(probes[0].options.shell,false);
});

test('old or missing Python does not prevent trying newer versions', () => {
  const attempts=[];
  const python=path.resolve('Python3.12');
  assert.equal(cli.selectPython([],command=>{
    attempts.push(command);
    if (command==='python3.12') return {status:0,stdout:JSON.stringify({executable:python,supported:true})};
    return {status:0,stdout:JSON.stringify({executable:command,supported:false})};
  }),python);
  assert.ok(attempts.includes('python3.12'));
});

test('explicit invalid Python does not silently fall back; errors omit child output', () => {
  let calls=0;
  assert.throws(()=>cli.selectPython(['--python','invalid-python'],()=>{
    calls++;return {status:1,stderr:'sensitive-diagnostic',stdout:'not-json'};
  }),error=>!error.message.includes('sensitive-diagnostic')&&error.message.includes('Python 3.11+'));
  assert.equal(calls,1);
});

test('Windows discovery includes py launcher with a distinct -3 argument', () => {
  const candidates=cli.pythonCandidates([],'win32');
  assert.ok(candidates.some(row=>row[0]==='py'&&row[1]==='-3'));
});

process.stdout.write(`${passed} Node checks passed\n`);
