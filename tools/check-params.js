// Checks every workflow's node parameters against n8n's own node definitions
// (n8n-nodes-base's dist/types/nodes.json, the data the n8n editor draws its forms from),
// then asks n8n-workflow for the same parameter issues the editor shows as red badges.
// Usage: node tools/check-params.js workflows
// Prints GitHub annotations and a Markdown summary; exits 1 if any workflow has an error.
'use strict';

const fs = require('fs');
const path = require('path');

const dir = process.argv[2] || 'workflows';
const summary = [];
let errorCount = 0;

function annotate(kind, file, message) {
  // GitHub annotations are one line each.
  console.log('::' + kind + ' file=' + file + '::' + String(message).split('\n').join(' '));
  if (kind === 'error') errorCount++;
}

function packageDir(name) {
  const local = path.join(process.cwd(), 'node_modules', name);
  if (fs.existsSync(path.join(local, 'package.json'))) return local;
  return path.dirname(require.resolve(name + '/package.json', { paths: [process.cwd()] }));
}

function loadNodeTypes() {
  const base = packageDir('n8n-nodes-base');
  const candidates = ['dist/types/nodes.json', 'dist/known/nodes.json', 'types/nodes.json'];
  for (const c of candidates) {
    const p = path.join(base, c);
    if (fs.existsSync(p)) {
      const version = JSON.parse(fs.readFileSync(path.join(base, 'package.json'), 'utf8')).version;
      return { types: JSON.parse(fs.readFileSync(p, 'utf8')), source: c, version };
    }
  }
  const dist = fs.existsSync(path.join(base, 'dist')) ? fs.readdirSync(path.join(base, 'dist')).join(', ') : 'no dist';
  throw new Error('nodes.json not found in n8n-nodes-base; dist holds: ' + dist);
}

function versionsOf(desc) {
  return Array.isArray(desc.version) ? desc.version : [desc.version];
}

function fullName(desc) {
  return desc.name.includes('.') ? desc.name : 'n8n-nodes-base.' + desc.name;
}

function findDescription(types, node) {
  const all = types.filter(d => fullName(d) === node.type);
  const exact = all.find(d => versionsOf(d).includes(node.typeVersion));
  const latest = Math.max(...all.map(d => Math.max(...versionsOf(d))));
  return { desc: exact || null, known: all.length > 0, latest };
}

function isExpression(v) {
  return typeof v === 'string' && v.startsWith('=');
}

function optionValues(props) {
  const values = [];
  for (const p of props) for (const o of p.options || []) if ('value' in o) values.push(o.value);
  return values;
}

// Checks one parameter value against every property of that name (names repeat across
// resources and operations, so a value is fine if any of them accepts it).
function checkValue(file, where, props, value) {
  if (isExpression(value)) return;
  const types = new Set(props.map(p => p.type));
  if (types.size === 1 && types.has('options')) {
    const allowed = optionValues(props);
    if (!allowed.includes(value)) annotate('error', file, where + ': "' + value + '" is not one of ' + JSON.stringify(allowed));
  } else if (types.size === 1 && types.has('multiOptions')) {
    const allowed = optionValues(props);
    for (const v of Array.isArray(value) ? value : [value]) {
      if (!allowed.includes(v)) annotate('error', file, where + ': "' + v + '" is not one of ' + JSON.stringify(allowed));
    }
  } else if (types.size === 1 && types.has('boolean')) {
    if (typeof value !== 'boolean') annotate('error', file, where + ' should be true or false');
  } else if (types.size === 1 && types.has('number')) {
    if (typeof value !== 'number') annotate('error', file, where + ' should be a number');
  } else if (types.size === 1 && types.has('collection')) {
    const children = props.flatMap(p => p.options || []);
    checkObject(file, where, children, value);
  } else if (types.size === 1 && types.has('fixedCollection')) {
    const groups = props.flatMap(p => p.options || []);
    if (typeof value !== 'object' || value === null) {
      annotate('error', file, where + ' should be an object');
      return;
    }
    for (const [key, inner] of Object.entries(value)) {
      const group = groups.filter(g => g.name === key);
      if (!group.length) {
        annotate('error', file, where + '.' + key + ' is not a known option; known: ' + groups.map(g => g.name).join(', '));
        continue;
      }
      const children = group.flatMap(g => g.values || []);
      for (const entry of Array.isArray(inner) ? inner : [inner]) checkObject(file, where + '.' + key, children, entry);
    }
  }
}

function checkObject(file, where, props, obj) {
  if (typeof obj !== 'object' || obj === null || Array.isArray(obj)) {
    annotate('error', file, where + ' should be an object');
    return;
  }
  for (const [key, value] of Object.entries(obj)) {
    const matches = props.filter(p => p.name === key);
    if (!matches.length) {
      annotate('error', file, where + '.' + key + ' is not a parameter this node knows');
      continue;
    }
    checkValue(file, where + '.' + key, matches, value);
  }
}

// The editor's own check. Its signature has changed between n8n releases, so a failure
// here is reported as a warning, not an error.
function editorIssues(file, NodeHelpers, node, desc) {
  try {
    const full = NodeHelpers.getNodeParameters(desc.properties, node.parameters, true, false, node, desc);
    const issues = NodeHelpers.getNodeParametersIssues(desc.properties, { ...node, parameters: full || {} }, desc);
    if (issues && issues.parameters) {
      for (const [name, messages] of Object.entries(issues.parameters)) {
        annotate('error', file, node.name + ' > ' + name + ': ' + [].concat(messages).join('; '));
      }
    }
  } catch (e) {
    annotate('warning', file, node.name + ': the editor parameter check could not run: ' + e.message);
  }
}

function main() {
  const { types, source, version } = loadNodeTypes();
  let NodeHelpers = null;
  try {
    NodeHelpers = require(require.resolve('n8n-workflow', { paths: [process.cwd()] })).NodeHelpers;
  } catch (e) {
    console.log('::warning::n8n-workflow could not be loaded: ' + e.message);
  }
  summary.push('## n8n parameter check', '', 'Node definitions: n8n-nodes-base ' + version + ' (' + source + ', ' + types.length + ' entries)', '');
  summary.push('| Workflow | Nodes | Errors |', '|---|---|---|');

  const files = fs.readdirSync(dir).filter(f => f.endsWith('.json')).sort();
  for (const f of files) {
    const file = path.join(dir, f).split(path.sep).join('/');
    const before = errorCount;
    const wf = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
    for (const node of wf.nodes) {
      const { desc, known, latest } = findDescription(types, node);
      if (!known) {
        annotate('error', file, node.name + ': unknown node type ' + node.type);
        continue;
      }
      if (!desc) {
        annotate('error', file, node.name + ': ' + node.type + ' has no version ' + node.typeVersion + ' (latest ' + latest + ')');
        continue;
      }
      if (latest > node.typeVersion) {
        annotate('notice', file, node.name + ': ' + node.type + ' ' + node.typeVersion + ' works; the newest version is ' + latest);
      }
      checkObject(file, node.name, desc.properties, node.parameters || {});
      if (NodeHelpers && node.type !== 'n8n-nodes-base.stickyNote') editorIssues(file, NodeHelpers, node, desc);
    }
    summary.push('| ' + f + ' | ' + wf.nodes.length + ' | ' + (errorCount - before) + ' |');
  }

  console.log('::notice::checked ' + files.length + ' workflows against n8n-nodes-base ' + version + ': ' + errorCount + ' errors');
  if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, summary.join('\n') + '\n');
  process.exit(errorCount ? 1 : 0);
}

try {
  main();
} catch (e) {
  console.log('::error::check-params could not run: ' + String(e && e.message).split('\n').join(' '));
  process.exit(1);
}
