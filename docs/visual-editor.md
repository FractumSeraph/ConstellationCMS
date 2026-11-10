# Visual editing

The editor has two modes, switched with **Markdown / Visual** in its toolbar. Visual mode is
word-processor style: type, and use the buttons for bold, italic, strikethrough, inline code,
headings, quotes, lists, dividers and links (Ctrl+B, Ctrl+I, Ctrl+K and Ctrl+S work too).
Images from the Media list or an upload go in where the cursor is.

The file on disk stays Markdown, so Gemini, Gopher, your synced folder and any text editor keep
working, and you can switch modes at any time.

- **Nothing you didn't touch changes.** Each paragraph, heading, list, quote or table you leave
  alone is written back exactly as it was; only the blocks you edit are rewritten.
- **Grey blocks keep their Markdown.** Things visual mode cannot show faithfully stay as locked
  grey boxes showing their Markdown: code blocks, raw HTML, galleries, `::: only-…` blocks,
  footnotes, task lists, definition lists, wiki links, shortcodes and images with captions.
  Switch to Markdown mode to change them.
- **Pasting brings plain text**, so no fonts, colours or stray markup come in from other
  programs.
- Saving, autosave, the previews and the warnings work the same in both modes.

Under the hood the visual editor's changes are turned back into Markdown with the same
converter the Publii importer uses, a moment after you stop typing.
