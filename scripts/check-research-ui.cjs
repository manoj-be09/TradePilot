// Render actual component with isolated demo output; never call an account API.
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const runtime = createRequire(path.join(root, 'work/web-production-20261013/package.json'));
const ts = runtime('typescript');
const React = runtime('react');
const {renderToStaticMarkup} = runtime('react-dom/server');
const source = fs.readFileSync(path.join(root, 'apps/web/app/ResearchWorkspace.tsx'), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {
  jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
}}).outputText;
const mod = {exports: {}};
new Function('require', 'module', 'exports', compiled)(runtime, mod, mod.exports);
const demo = JSON.parse(fs.readFileSync(path.join(root, 'work/research-demo-20261016.json'), 'utf8').replace(/^\uFEFF/, ''));
assert.equal(demo.evidence, 'SYNTHETIC_DEMO_NOT_MARKET_EVIDENCE');
const manifest = {experiment_family: 'SYNTHETIC DEMO ONLY', trial_number: 1,
  declared_search_budget: 1, cost_model_id: demo.engine_result.report.cost_model.id,
  train_start: '2020-01-01', train_end: '2021-06-23', validation_start: '2021-06-26',
  validation_end: '2021-12-20', holdout_start: '2021-12-23', holdout_end: '2022-06-18'};
const trial = {id: 'synthetic-only', manifest, manifest_digest: 'fixture', result: null,
  readiness: demo.readiness,
  engine_evaluation: {result: demo.engine_result, result_digest: demo.result_digest}};
const render = value => renderToStaticMarkup(React.createElement(mod.exports.ResearchTrialCard, {trial: value}));
const populated = render(trial);
assert.match(populated, /Insufficient sample/);
assert.match(populated, /33 closes/);
assert.match(populated, /12 closes/);
assert.match(populated, /Profit probability is unavailable/);
assert.match(populated, /Operator evidence/);
assert.match(populated, /illustrative costs/);
assert.equal(demo.readiness.status, 'BLOCKED');
assert.match(populated, /Evidence readiness · Blocked/);
assert.match(populated, /Protected holdout/);
assert.match(populated, /UNKNOWN/);
assert.match(populated, /Refreshing does not renew/);
assert.match(populated, /Research inspection only/);
const failed = render({...trial, readiness: undefined, engine_evaluation: {result: {outcome: 'FAILED', reason_code: 'INPUT_OR_IDENTITY_CHECK_FAILED'}}});
assert.match(failed, /Evaluation failed/);
assert.match(failed, /input or identity check failed/);
const pending = render({...trial, readiness: undefined, engine_evaluation: null});
assert.match(pending, /Not evaluated/);
assert.doesNotMatch(pending, /33 closes/);
const legacy = render({...trial, engine_evaluation: undefined, readiness: undefined});
assert.match(legacy, /updated API is required/);
assert.match(legacy, /Evidence readiness is unavailable/);
const unsupported = render({...trial, readiness: {...demo.readiness, schema_version: 99}});
assert.match(unsupported, /No qualification can be inferred/);
if (process.argv[2]) {
  const css = fs.readFileSync(path.join(root, 'apps/web/app/styles.css'), 'utf8');
  fs.writeFileSync(process.argv[2], `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Synthetic research UI validation</title><style>${css}</style></head><body><main class="paper-workspace research-workspace" style="max-width:1100px;margin:20px auto"><h1>SYNTHETIC UI VALIDATION ONLY</h1><p>Disposable demo output. No owner account, market data or qualified performance evidence.</p><div class="research-trials">${populated}${failed}${pending}${legacy}</div></main></body></html>`);
}
console.log('Research UI checks passed: populated, failed, pending and legacy evidence states.');
