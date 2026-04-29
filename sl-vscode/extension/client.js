'use strict';

const path = require('path');
const { workspace, ExtensionContext } = require('vscode');
const {
  LanguageClient,
  TransportKind,
} = require('vscode-languageclient/node');

let client;

function activate(context) {
  const serverModule = context.asAbsolutePath(path.join('server.js'));

  const serverOptions = {
    run:   { module: serverModule, transport: TransportKind.ipc },
    debug: {
      module: serverModule,
      transport: TransportKind.ipc,
      options: { execArgv: ['--nolazy', '--inspect=6009'] },
    },
  };

  const clientOptions = {
    documentSelector: [{ scheme: 'file', language: 'sl' }],
    synchronize: {
      fileEvents: workspace.createFileSystemWatcher('**/*.sl'),
    },
  };

  client = new LanguageClient(
    'slLanguageServer',
    'SL Language Server',
    serverOptions,
    clientOptions,
  );

  client.start();
}

function deactivate() {
  if (client) return client.stop();
}

module.exports = { activate, deactivate };
