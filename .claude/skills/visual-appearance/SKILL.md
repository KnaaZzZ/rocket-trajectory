---
name: visual-appearance
description: >
  Use when creating, editing, or reviewing UI code for the Tkinter engineering
  app. Ensures a clean, consistent, non-distracting visual appearance. Trigger
  on any change that adds or modifies widgets, windows, dialogs, layout, colors,
  fonts, spacing, or icons. This is a utility engineering tool: it must look
  tidy and legible, not fancy.
---

# Visual Appearance (Tkinter engineering app)

Goal: the app should look calm, consistent, and legible. Not pretty for its own
sake — just never ugly, cramped, or confusing. When in doubt, choose the plainer
option.

## Follow these points on every UI change

1. **Use ttk, not classic tk widgets.** Import `from tkinter import ttk` and use
   `ttk.Button`, `ttk.Label`, `ttk.Entry`, `ttk.Frame`, etc. They respect the
   native theme and look far less dated than classic `tk` widgets.

2. **Pick one theme and set it once.** At startup call
   `ttk.Style().theme_use("clam")` (or `vista`/`aqua` if on that OS). Never mix
   themed and unthemed widgets in the same view.

3. **One font family, at most three sizes.** Define fonts once (e.g. body 10,
   heading 12 bold, small 9) and reuse. Do not set fonts ad-hoc per widget.

4. **Limited color palette.** Default background, one accent for primary actions,
   red only for destructive/error, green/orange only for status. No decorative
   colors. Ensure text contrast is readable (dark text on light bg).

5. **Consistent spacing.** Use a spacing unit (e.g. 4 or 8 px) for all `padx`,
   `pady`, and grid gaps. Never let widgets touch window edges — pad container
   frames.

6. **Align everything to a grid.** Prefer `.grid()` over `.pack()` for forms so
   labels and inputs line up in columns. Labels right-aligned or left-aligned
   consistently; inputs share a common left edge and width.

7. **Group related controls in labeled frames.** Use `ttk.LabelFrame` to visually
   separate sections (inputs / results / actions) rather than a flat wall of
   widgets.

8. **Fixed, sensible window sizing.** Set a reasonable minimum size with
   `root.minsize()`. Let content dictate size; avoid huge empty margins or
   controls that get cut off. Make resizing behave (configure `columnconfigure`
   / `rowconfigure` weights).

9. **No visual clutter.** Avoid 3D bevels, borders on everything, and stacked
   frames with visible relief. Flat and simple reads as modern and clean.

10. **Consistent widget states.** Disabled widgets should look disabled
    (`state="disabled"`). Show which field has focus. Don't leave dead-looking
    active controls.

11. **Icons/emoji sparingly.** A small unicode symbol (⚠ ✓) for status is fine;
    do not decorate every button.

## Review checklist before finishing

- [ ] Only ttk widgets, one theme set globally
- [ ] Fonts and colors come from a single defined set
- [ ] Uniform padding; nothing touches window edges
- [ ] Inputs aligned in a grid, sections in LabelFrames
- [ ] Window has minsize and sane resize behavior
- [ ] Text is high-contrast and readable at default size
- [ ] No unnecessary borders, bevels, or decorative color
