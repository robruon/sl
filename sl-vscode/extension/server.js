'use strict';

const {
  createConnection, TextDocuments, ProposedFeatures,
  CompletionItemKind, TextDocumentSyncKind, InsertTextFormat,
} = require('vscode-languageserver/node');
const { TextDocument } = require('vscode-languageserver-textdocument');

const connection = createConnection(ProposedFeatures.all);
const documents  = new TextDocuments(TextDocument);

// ── Static completions ────────────────────────────────────────────────

const KEYWORDS = [
  { label: '->',   detail: 'return',            kind: CompletionItemKind.Keyword },
  { label: '->|',  detail: 'yield (generator)', kind: CompletionItemKind.Keyword },
  { label: '<<|',  detail: 'send to generator', kind: CompletionItemKind.Operator },
  { label: '!!',   detail: 'while loop',         kind: CompletionItemKind.Keyword },
  { label: '?',    detail: 'if',                 kind: CompletionItemKind.Keyword },
  { label: '??',   detail: 'else-if',            kind: CompletionItemKind.Keyword },
  { label: '--',   detail: 'else',               kind: CompletionItemKind.Keyword },
  { label: ':<',   detail: 'declare + assign',   kind: CompletionItemKind.Operator },
  { label: '@:<',  detail: 'mutate',             kind: CompletionItemKind.Operator },
  { label: '~>',   detail: 'import module',      kind: CompletionItemKind.Keyword },
  { label: 'true',  kind: CompletionItemKind.Value },
  { label: 'false', kind: CompletionItemKind.Value },
  { label: 'null',  kind: CompletionItemKind.Value },
];

const TYPES = ['int','str','float','bool','void','arr','obj'].map(t => ({
  label: `_${t}`, detail: `${t} type annotation`,
  kind: CompletionItemKind.TypeParameter,
}));

const BUILTINS = [
  { label:'print',     kind:CompletionItemKind.Function, detail:'print:val  or  print:"fmt {}",val',
    insertText:'print:${1:value}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'fmt',       kind:CompletionItemKind.Function, detail:'fmt:"template {}",val → string',
    insertText:'fmt:"${1:{}}",${2:val}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'print_err', kind:CompletionItemKind.Function, detail:'print_err:msg → stderr' },
  { label:'len',       kind:CompletionItemKind.Function, detail:'len:s → length of string or array' },
  { label:'abs',       kind:CompletionItemKind.Function, detail:'abs:x → int' },
  { label:'min',       kind:CompletionItemKind.Function, detail:'min:a,b' },
  { label:'max',       kind:CompletionItemKind.Function, detail:'max:a,b' },
  { label:'clamp',     kind:CompletionItemKind.Function, detail:'clamp:x,lo,hi' },
  { label:'sqrt',      kind:CompletionItemKind.Function, detail:'sqrt:x → float' },
  { label:'floor',     kind:CompletionItemKind.Function, detail:'floor:x → float' },
  { label:'ceil',      kind:CompletionItemKind.Function, detail:'ceil:x → float' },
  { label:'round',     kind:CompletionItemKind.Function, detail:'round:x → float' },
  { label:'sin',       kind:CompletionItemKind.Function, detail:'sin:x → float' },
  { label:'cos',       kind:CompletionItemKind.Function, detail:'cos:x → float' },
  { label:'tan',       kind:CompletionItemKind.Function, detail:'tan:x → float' },
  { label:'log',       kind:CompletionItemKind.Function, detail:'log:x → float' },
  { label:'pow',       kind:CompletionItemKind.Function, detail:'pow:base,exp → float' },
  { label:'int_to_str',   kind:CompletionItemKind.Function, detail:'int_to_str:n → str' },
  { label:'float_to_str', kind:CompletionItemKind.Function, detail:'float_to_str:f → str' },
  { label:'bool_to_str',  kind:CompletionItemKind.Function, detail:'bool_to_str:b → str' },
  { label:'str_to_int',   kind:CompletionItemKind.Function, detail:'str_to_int:s → int' },
  { label:'str_to_float', kind:CompletionItemKind.Function, detail:'str_to_float:s → float' },
  { label:'read_line',    kind:CompletionItemKind.Function, detail:'read_line → str' },
  { label:'read_file',    kind:CompletionItemKind.Function, detail:'read_file:"path" → str',
    insertText:'read_file:${1:"path"}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'write_file',   kind:CompletionItemKind.Function, detail:'write_file:"path",content',
    insertText:'write_file:${1:"path"},${2:content}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'append_file',  kind:CompletionItemKind.Function, detail:'append_file:"path",content' },
  { label:'file_exists',  kind:CompletionItemKind.Function, detail:'file_exists:"path" → 1 or 0' },
  { label:'sort',         kind:CompletionItemKind.Function, detail:'sort:arr (in-place)' },
  { label:'reverse',      kind:CompletionItemKind.Function, detail:'reverse:arr (in-place)' },
];

const SNIPPETS = [
  { label:'fn',   kind:CompletionItemKind.Snippet, detail:'Function definition',
    insertText:':${1:name}:${2:param}_${3:int}[${4:int}]\n    $0\n    -> ${5:0}',
    insertTextFormat:InsertTextFormat.Snippet },
  { label:'fnv',  kind:CompletionItemKind.Snippet, detail:'Void function',
    insertText:':${1:name}:${2:param}_${3:int}\n    $0',
    insertTextFormat:InsertTextFormat.Snippet },
  { label:'gen',  kind:CompletionItemKind.Snippet, detail:'Generator definition',
    insertText:'|:${1:name}:${2:param}_${3:int}[|${4:int}]\n    ${5:i} :< 0\n    !! ${5:i} < ${2:param}\n        ->| ${5:i}\n        ${5:i} @:< + 1',
    insertTextFormat:InsertTextFormat.Snippet },
  { label:'class',kind:CompletionItemKind.Snippet, detail:'Class definition',
    insertText:'.${1:ClassName}:${2:field}_${3:int}\n\n    :init:@[void]\n        $0\n\n    :${4:method}:@[${5:int}]\n        -> @:${2:field}',
    insertTextFormat:InsertTextFormat.Snippet },
  { label:'newmod',kind:CompletionItemKind.Snippet, detail:'New namespace module file',
    insertText:'# ${1:module_name}.sl\n\n~[${1:module_name}]\n\n    :${2:function}:${3:param}_${4:int}[${5:int}]\n        $0',
    insertTextFormat:InsertTextFormat.Snippet },
  { label:'newpkg',kind:CompletionItemKind.Snippet, detail:'New package with C FFI bindings',
    insertText:'# ${1:package_name}.sl  —  ${2:description}\n# ~> ${1:package_name}\n\n~[${1:package_name}]\n\n    # ── C bindings ──────────────────────\n    ~C :${3:c_fn}:${4:param}_${5:int}[${6:int}]\n\n    # ── Public API ───────────────────────\n    :${3:c_fn}:${4:param}_${5:int}[${6:int}] -> ${3:c_fn}:${4:param}\n$0',
    insertTextFormat:InsertTextFormat.Snippet },
  { label:'while', kind:CompletionItemKind.Snippet, detail:'While loop',
    insertText:'!! ${1:condition}\n    $0', insertTextFormat:InsertTextFormat.Snippet },
  { label:'if',    kind:CompletionItemKind.Snippet, detail:'If statement',
    insertText:'? ${1:condition}\n    $0', insertTextFormat:InsertTextFormat.Snippet },
  { label:'ife',   kind:CompletionItemKind.Snippet, detail:'If / else',
    insertText:'? ${1:condition}\n    $2\n--\n    $0', insertTextFormat:InsertTextFormat.Snippet },
  { label:'main',  kind:CompletionItemKind.Snippet, detail:'Main entry point',
    insertText:':main[int]\n    $0\n    -> 0', insertTextFormat:InsertTextFormat.Snippet },
  { label:'print', kind:CompletionItemKind.Snippet, detail:'Print value',
    insertText:'print:${1:value}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'printf',kind:CompletionItemKind.Snippet, detail:'Print formatted',
    insertText:'print:"${1:{}}",${2:val}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'fmt',   kind:CompletionItemKind.Snippet, detail:'Format to variable',
    insertText:'${1:s} :< fmt:"${2:{}}",${3:val}', insertTextFormat:InsertTextFormat.Snippet },
];

// String method completions (after ::)
const STRING_METHODS = [
  'trim','trim_start','trim_end','to_upper','to_lower','to_int','to_float','is_empty',
].map(m => ({ label:m, kind:CompletionItemKind.Method, detail:`str::${m}` })).concat([
  { label:'contains',   kind:CompletionItemKind.Method, detail:'s::contains:"sub" → 1 or 0',
    insertText:'contains:${1:"sub"}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'starts_with',kind:CompletionItemKind.Method, detail:'s::starts_with:"pre"',
    insertText:'starts_with:${1:"prefix"}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'ends_with',  kind:CompletionItemKind.Method, detail:'s::ends_with:"suf"',
    insertText:'ends_with:${1:"suffix"}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'index_of',   kind:CompletionItemKind.Method, detail:'s::index_of:"sub" → int',
    insertText:'index_of:${1:"sub"}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'slice',      kind:CompletionItemKind.Method, detail:'s::slice:start,end',
    insertText:'slice:${1:0},${2:5}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'replace',    kind:CompletionItemKind.Method, detail:'s::replace:"from","to"',
    insertText:'replace:${1:"from"},${2:"to"}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'repeat',     kind:CompletionItemKind.Method, detail:'s::repeat:n',
    insertText:'repeat:${1:2}', insertTextFormat:InsertTextFormat.Snippet },
]);

// Array method completions
const ARRAY_METHODS = [
  { label:'sort',     kind:CompletionItemKind.Method, detail:'arr::sort (in-place)' },
  { label:'reverse',  kind:CompletionItemKind.Method, detail:'arr::reverse (in-place)' },
  { label:'pop',      kind:CompletionItemKind.Method, detail:'arr::pop → last element' },
  { label:'len',      kind:CompletionItemKind.Method, detail:'arr::len → int' },
  { label:'push',     kind:CompletionItemKind.Method, detail:'arr::push:val',
    insertText:'push:${1:val}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'get',      kind:CompletionItemKind.Method, detail:'arr::get:i → element',
    insertText:'get:${1:0}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'contains', kind:CompletionItemKind.Method, detail:'arr::contains:val → 1 or 0',
    insertText:'contains:${1:val}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'index_of', kind:CompletionItemKind.Method, detail:'arr::index_of:val → int',
    insertText:'index_of:${1:val}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'slice',    kind:CompletionItemKind.Method, detail:'arr::slice:start,end',
    insertText:'slice:${1:0},${2:3}', insertTextFormat:InsertTextFormat.Snippet },
  { label:'concat',   kind:CompletionItemKind.Method, detail:'arr::concat:other → new array',
    insertText:'concat:${1:other}', insertTextFormat:InsertTextFormat.Snippet },
];

// ── Symbol extraction ─────────────────────────────────────────────────

function extractSymbols(text) {
  const functions  = [];
  const generators = [];
  const classes    = [];
  const variables  = [];
  const namespaces = [];

  const lines = text.split('\n');
  let currentClass = null;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Class definition:  .ClassName:fields
    const clsMatch = line.match(/^\.([A-Z][a-zA-Z0-9_]*)((?::[a-zA-Z_][a-zA-Z0-9_]*)*)?/);
    if (clsMatch) {
      const fields = clsMatch[2]
        ? clsMatch[2].slice(1).split(':').map(f => f.replace(/_\w+$/, ''))
        : [];
      currentClass = { name: clsMatch[1], methods: [], fields };
      classes.push(currentClass);
      continue;
    }

    // Method inside class:  :method_name:@[ret]
    const methodMatch = line.match(/^\s{4,}(:)([a-zA-Z_][a-zA-Z0-9_]*)/);
    if (methodMatch && currentClass && methodMatch[2] !== 'init') {
      currentClass.methods.push(methodMatch[2]);
      continue;
    }

    // Reset class context on unindented line that isn't a method
    if (line.length > 0 && !line.match(/^\s/) && !clsMatch) {
      currentClass = null;
    }

    // Function:  :name:params[ret]
    const fnMatch = line.match(/^:([a-zA-Z_][a-zA-Z0-9_]*)((?::[a-zA-Z_][a-zA-Z0-9_]*)*)(?:\[([^\]]*)\])?/);
    if (fnMatch && fnMatch[1] !== 'main') {
      const params = fnMatch[2] ? fnMatch[2].slice(1).split(':').filter(Boolean) : [];
      const ret    = fnMatch[3] || 'void';
      functions.push({ name: fnMatch[1], params, ret,
        insertText: params.length
          ? `${fnMatch[1]}:${params.map((p,i) => `\${${i+1}:${p}}`).join(',')}`
          : fnMatch[1],
        insertTextFormat: InsertTextFormat.Snippet,
      });
    }

    // Generator:  |:name:params[|ret]
    const genMatch = line.match(/^\|:([a-zA-Z_][a-zA-Z0-9_]*)((?::[a-zA-Z_][a-zA-Z0-9_]*)*)(?:\[\|([^\]]*)\])?/);
    if (genMatch) {
      const params = genMatch[2] ? genMatch[2].slice(1).split(':').filter(Boolean) : [];
      generators.push({ name: genMatch[1], params,
        insertText: params.length
          ? `${genMatch[1]}:${params.map((p,i) => `\${${i+1}:${p}}`).join(',')}`
          : genMatch[1],
        insertTextFormat: InsertTextFormat.Snippet,
      });
    }

    // Namespace:  ~[name]
    const nsMatch = line.match(/^~\[([a-zA-Z_][a-zA-Z0-9_]*)\]/);
    if (nsMatch) namespaces.push(nsMatch[1]);

    // Variables:  name :<
    const varMatch = line.match(/^\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+:<\s/);
    if (varMatch && !['true','false','null'].includes(varMatch[1])) {
      // Try to detect class type from RHS  e.g.  v :< Vec2:3,4
      const rest = line.slice(line.indexOf(':<') + 2).trim();
      const classType = rest.match(/^([A-Z][a-zA-Z0-9_]*):/);
      variables.push({ name: varMatch[1], classType: classType ? classType[1] : null });
    }
  }

  return { functions, generators, classes, variables, namespaces };
}

function symbolsToCompletions(symbols, triggerAfterColons) {
  const items = [];

  // If triggered after ::, offer methods for any known class variable
  // (We offer all class methods since we don't have type inference)
  if (triggerAfterColons) {
    for (const cls of symbols.classes) {
      for (const method of cls.methods) {
        items.push({
          label: method,
          kind: CompletionItemKind.Method,
          detail: `${cls.name}::${method}`,
          insertText: method,
        });
      }
      // Fields too
      for (const field of cls.fields) {
        items.push({
          label: field,
          kind: CompletionItemKind.Field,
          detail: `${cls.name}.${field} (field)`,
          insertText: field,
        });
      }
    }
    return items;  // only methods/fields after ::
  }

  for (const fn of symbols.functions) {
    items.push({
      label: fn.name, kind: CompletionItemKind.Function,
      detail: `fn  :${fn.name}${fn.params.length ? ':'+fn.params.join(',') : ''}[${fn.ret}]`,
      insertText: fn.insertText, insertTextFormat: fn.insertTextFormat,
    });
  }

  for (const gen of symbols.generators) {
    items.push({
      label: gen.name, kind: CompletionItemKind.Function,
      detail: `gen  |:${gen.name}`,
      documentation: `Generator — use  var :< ${gen.name}:...  then  var <<| val`,
      insertText: gen.insertText, insertTextFormat: gen.insertTextFormat,
    });
  }

  for (const cls of symbols.classes) {
    items.push({
      label: cls.name, kind: CompletionItemKind.Class,
      detail: `class  .${cls.name}  (${cls.fields.join(', ')})`,
      insertText: `${cls.name}:\${1:${cls.fields.join(',') || 'args'}}`,
      insertTextFormat: InsertTextFormat.Snippet,
    });
  }

  for (const v of symbols.variables) {
    items.push({ label: v.name, kind: CompletionItemKind.Variable, detail: 'local variable' });
  }

  for (const ns of symbols.namespaces) {
    items.push({ label: ns, kind: CompletionItemKind.Module, detail: `namespace ~[${ns}]` });
  }

  return items;
}

// ── Server lifecycle ──────────────────────────────────────────────────

connection.onInitialize(() => ({
  capabilities: {
    textDocumentSync: TextDocumentSyncKind.Incremental,
    completionProvider: {
      resolveProvider: false,
      triggerCharacters: [':', '.', '|', '-', '@', '!', '?', '~'],
    },
  },
}));

connection.onCompletion(params => {
  const doc = documents.get(params.textDocument.uri);
  if (!doc) return [];

  const text    = doc.getText();
  const symbols = extractSymbols(text);

  // Detect if we're right after ::
  const pos     = params.position;
  const lineText = text.split('\n')[pos.line] || '';
  const prefix   = lineText.slice(0, pos.character);
  const afterDoubleColon = prefix.endsWith('::');

  const dynamic = symbolsToCompletions(symbols, afterDoubleColon);

  if (afterDoubleColon) {
    return [...STRING_METHODS, ...ARRAY_METHODS, ...dynamic];
  }

  return [...KEYWORDS, ...TYPES, ...BUILTINS, ...SNIPPETS,
          ...STRING_METHODS, ...ARRAY_METHODS, ...dynamic];
});

documents.listen(connection);
connection.listen();
