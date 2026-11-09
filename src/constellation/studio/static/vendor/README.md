# Vendored browser assets

- `htmx.min.js`: htmx 2.0.4 (https://htmx.org), BSD-2-Clause.
- `codemirror.bundle.js`: a single-file ES module built with esbuild from codemirror 6.0.1,
  @codemirror/lang-markdown 6.3.2, @codemirror/lint 6.8.4, @codemirror/theme-one-dark 6.1.2
  (MIT). Rebuild recipe (needs Node only on the machine that rebuilds it):

      npm install esbuild@0.24.2 codemirror@6.0.1 @codemirror/lang-markdown@6.3.2 @codemirror/lint@6.8.4 @codemirror/theme-one-dark@6.1.2
      npx esbuild entry.js --bundle --format=esm --minify --target=es2020 --outfile=codemirror.bundle.js

  with `entry.js` re-exporting EditorView, EditorState, Compartment, basicSetup, keymap, lineNumbers,
  gutter, GutterMarker, Decoration, markdown, markdownLanguage, linter, lintGutter, setDiagnostics,
  oneDark and indentWithTab.
