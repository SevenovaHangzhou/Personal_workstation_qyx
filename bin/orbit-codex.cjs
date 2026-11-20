#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const ROOT = path.resolve(__dirname, '..');
const VERSION = require('../package.json').version;

function help() {
  return `星序 / ORBIT Codex 插件 ${VERSION}

用法：
  orbit-codex install [安装参数]
  orbit-codex --version

安装参数：
  --python PATH             Python 3.11+ 的解释器路径
  --headless                仅核心 MCP，不下载图形依赖
  --use-existing-python     使用已有 PyQt5 的解释器，不创建独立环境
  --dry-run                 预览，不写文件或下载
  --data-dir PATH           插件数据目录（绝对路径）
  --credentials-from PATH   只读取已有星序凭据目录
  --codex PATH              Codex CLI 可执行文件
  --install-dir PATH        专用 marketplace 目录
  --no-codex                只准备插件分发，不注册 Codex

npm 安装只提供本命令；运行 install 才配置 Codex 和 Python 环境。
升级：先更新 npm 包，再运行 orbit-codex install。
完整安装参数：orbit-codex install --help
`;
}

function explicitPython(args) {
  let selected;
  for (let index = 0; index < args.length; index++) {
    const argument = args[index];
    if (argument === '--python') {
      selected = args[++index];
      if (!selected || selected.startsWith('--')) throw new Error('--python 后需要解释器路径。');
    } else if (argument.startsWith('--python=')) {
      selected = argument.slice('--python='.length);
      if (!selected) throw new Error('--python 后需要解释器路径。');
    }
  }
  return selected;
}

function pythonCandidates(args, platform = process.platform) {
  const selected = explicitPython(args);
  if (selected) return [[selected]];
  const local = path.join(ROOT, '.venv', platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const candidates = fs.existsSync(local) ? [[local]] : [];
  if (platform === 'win32') candidates.push(['py', '-3'], ['python'], ['python3']);
  else candidates.push(['python3'], ['python']);
  candidates.push(...['3.14', '3.13', '3.12', '3.11'].map(version => ['python' + version]));
  return candidates;
}

function selectPython(args, probe = spawnSync, platform = process.platform) {
  const code = 'import json,sys; print(json.dumps({"executable":sys.executable,"supported":sys.version_info >= (3,11)}))';
  for (const [command, ...prefix] of pythonCandidates(args, platform)) {
    const response = probe(command, [...prefix, '-c', code], {
      encoding: 'utf8', timeout: 10000, windowsHide: true, shell: false,
    });
    if (response.error || response.status !== 0) continue;
    try {
      const result = JSON.parse(response.stdout);
      if (result.supported === true && typeof result.executable === 'string' && path.isAbsolute(result.executable)) {
        return result.executable;
      }
    } catch (_) { /* Try another supported interpreter; never print child diagnostics. */ }
  }
  throw new Error('未找到 Python 3.11+。请安装 Python 并加入 PATH，或用 --python 指定解释器。');
}

function main(args = process.argv.slice(2)) {
  if (args.length === 0 || ['--help', '-h', 'help'].includes(args[0])) {
    process.stdout.write(help());
    return;
  }
  if (args.length === 1 && ['--version', '-v'].includes(args[0])) {
    process.stdout.write(VERSION + '\n');
    return;
  }
  if (args[0] !== 'install') {
    process.stderr.write('未知命令。请运行 orbit-codex --help。\n');
    process.exitCode = 2;
    return;
  }
  if (Number(process.versions.node.split('.')[0]) < 18) {
    process.stderr.write('需要 Node.js 18+，建议使用当前 LTS。\n');
    process.exitCode = 2;
    return;
  }
  const options = args.slice(1);
  let python;
  try {
    python = selectPython(options);
  } catch (error) {
    process.stderr.write(error.message + '\n');
    process.exitCode = 2;
    return;
  }
  // npm global installs and npx caches share this launcher. Always copy into the
  // installer's stable directory; no shell interpolation or npm lifecycle hooks.
  const child = spawn(python, [path.join(ROOT, 'plugins/codex-orbit/scripts/install.py'), ...options], {
    stdio: 'inherit', shell: false, windowsHide: true,
  });
  let cancelled;
  const handlers = {};
  for (const signal of ['SIGINT', 'SIGTERM']) {
    handlers[signal] = () => { cancelled = signal; child.kill(signal); };
    process.on(signal, handlers[signal]);
  }
  function cleanup() {
    for (const [signal, handler] of Object.entries(handlers)) process.removeListener(signal, handler);
  }
  child.once('error', () => {
    cleanup();
    process.stderr.write('无法启动安装器，请检查 Python 路径和运行权限。\n');
    process.exitCode = 2;
  });
  child.once('exit', (code, signal) => {
    cleanup();
    process.exitCode = signal || cancelled ? (signal === 'SIGINT' || cancelled === 'SIGINT' ? 130 : 143) : code;
  });
}

module.exports = { help, explicitPython, pythonCandidates, selectPython, main };
if (require.main === module) main();
