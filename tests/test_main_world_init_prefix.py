"""Execute the shipping init-prefix parser and world dispatch using Node vm."""

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "additions/juggler/content/FrameTree.js"

HARNESS = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const parser = source.slice(source.indexOf('const INIT_SCRIPT_WRAPPER'),
                            source.indexOf('export class FrameTree'));
const method = source.slice(source.indexOf('  _evaluateInitScript(executionContext, script) {'),
                            source.indexOf('  _updateJavaScriptDisabled() {'));
const moduleContext = vm.createContext({
  ChromeUtils: {camouGetBool: () => enabled}, dump: message => messages.push(message),
});
let enabled = true;
const messages = [];
vm.runInContext(parser + '\nthis.parse = mainWorldInitScript;\nthis.dispatch = ({' +
                method + '})._evaluateInitScript;', moduleContext);
const {parse, dispatch} = moduleContext;
const guid = '0123456789abcdef0123456789abcdef';
function wrap(body, guarded = true) {
  return `(() => {
    ${guarded ? `globalThis.__pwInitScripts = globalThis.__pwInitScripts || {};
    const hasInitScript = globalThis.__pwInitScripts["${guid}"];
    if (hasInitScript)
      return;
    globalThis.__pwInitScripts["${guid}"] = true;` : ''}
    ${body}
  })();`;
}
const name = process.argv[2];
if (name === 'guarded' || name === 'installed-playwright') {
  const context = vm.createContext({});
  const body = 'mw:const localOnly = 1; globalThis.runs = (globalThis.runs || 0) + localOnly;';
  const input = name === 'installed-playwright'
    ? new (require(process.argv[3]).InitScript)(body, false).source : wrap(body);
  const result = parse(input);
  assert.notEqual(result, null, 'guard hid the main-world prefix');
  vm.runInContext(result, context);
  vm.runInContext(result, context);
  assert.equal(context.runs, 1, 'Playwright once-per-document guard was lost');
  assert.equal(vm.runInContext('typeof localOnly', context), 'undefined');
  const nextDocument = vm.createContext({});
  vm.runInContext(result, nextDocument);
  assert.equal(nextDocument.runs, 1);
} else if (name === 'legacy') {
  for (const script of ['  mw:globalThis.runs = 1;', wrap('mw:globalThis.runs = 1;', false)]) {
    const context = vm.createContext({});
    vm.runInContext(parse(script), context);
    assert.equal(context.runs, 1);
  }
} else if (name === 'ordinary') {
  for (const body of [
    'globalThis.text = "mw:globalThis.marker = true";',
    '// mw:globalThis.marker = true;\nglobalThis.text = 1;',
    '/* mw: */ globalThis.text = 1;',
    'globalThis.text = 1; mw:globalThis.marker = true;',
    'if (true) { mw:globalThis.marker = true; }',
  ]) {
    assert.equal(parse(body), null);
    assert.equal(parse(wrap(body)), null);
  }
} else if (name === 'unknown-guard') {
  const script = wrap('mw:globalThis.runs = 1;');
  assert.equal(parse(script.replace('if (hasInitScript)', 'if (otherCondition)')), null);
  assert.equal(parse(script.replace(`["${guid}"] = true`, '["different"] = true')), null);
} else if (name === 'dispatch') {
  const main = vm.createContext({});
  const isolated = vm.createContext({});
  const context = {
    evaluateScriptSafely: script => vm.runInContext(script, isolated),
    mainWorldContext: () => ({evaluateScriptSafely: script => vm.runInContext(script, main)}),
  };
  dispatch(context, wrap('mw:globalThis.marker = true;'));
  assert.equal(main.marker, true, 'prefixed script was dispatched to the sandbox');
  assert.equal(isolated.marker, undefined);
  dispatch(context, wrap('globalThis.privateMarker = true;'));
  assert.equal(main.privateMarker, undefined);
  assert.equal(isolated.privateMarker, true);
} else if (name === 'disabled') {
  enabled = false;
  let evaluated = 0;
  const context = {
    evaluateScriptSafely: () => ++evaluated,
    mainWorldContext: () => ({evaluateScriptSafely: () => ++evaluated}),
  };
  dispatch(context, wrap('mw:globalThis.marker = true;'));
  assert.equal(evaluated, 0, 'explicit main-world request must fail closed');
  assert.equal(messages.length, 1);
} else {
  throw new Error('unknown test: ' + name);
}
process.stdout.write(JSON.stringify({case: name, passed: true}));
'''


@pytest.mark.parametrize("case", ["guarded", "installed-playwright", "legacy", "ordinary", "unknown-guard", "dispatch", "disabled"])
def test_main_world_init_prefix(case):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to execute the native JS parser")
    args = [node, "-e", HARNESS, str(SOURCE), case]
    if case == "installed-playwright":
        playwright = importlib.util.find_spec("playwright")
        if not playwright:
            pytest.skip("Playwright is required for the real wrapper compatibility test")
        args.append(str(Path(playwright.origin).parent / "driver/package/lib/server/page.js"))
    result = subprocess.run(args,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"case": case, "passed": True}
