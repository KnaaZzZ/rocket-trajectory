---
name: usability-comfort
description: >
  Use when creating, editing, or reviewing UI/interaction code for the Tkinter
  engineering app. Ensures the app is comfortable and efficient for a power user
  who runs it often: fast keyboard flow, sensible layout, no friction, no data
  loss. Trigger on any change to inputs, workflows, dialogs, shortcuts, error
  handling, or window/layout behavior.
---

# Usability & Power-User Comfort (Tkinter engineering app)

Goal: someone using this tool daily should move through it fast, without
surprises, and never lose work. Correctness and flow beat looks.

## Follow these points on every interaction change

1. **Keyboard-first.** Bind Enter to the primary action of each form/dialog and
   Escape to cancel/close. Set a logical tab order (grid order usually gives it).
   Give buttons underlined mnemonics (`underline=`) where useful.

2. **Focus starts where work starts.** On window/dialog open, `.focus_set()` the
   first input the user will type into.

3. **Sensible defaults + remembered state.** Pre-fill fields with the most common
   values. Remember the last-used inputs, window size/position, and last
   directory between sessions (persist to a small config/JSON file).

4. **Validate inputs, don't crash.** Validate numeric/engineering fields on
   submit (and ideally live). Show a clear inline message near the field or a
   concise dialog — never a raw traceback. Reject bad input before computing.

5. **Never lose user work.** Warn on unsaved changes before close/new/open.
   Confirm destructive actions once (with an option to not ask again if it gets
   annoying). Autosave or keep a recovery copy for long tasks.

6. **Give feedback for every action.** Long operations show a progress bar or
   busy cursor and keep the UI responsive (run heavy compute off the main
   thread, e.g. `threading` + a queue, so the window doesn't freeze). Show a
   clear success/failure result.

7. **Efficient layout for repeat use.** Put the most-used controls where the hand
   goes first (top/left, or a fixed toolbar). Keep primary action buttons in a
   consistent place across screens. Don't hide common actions in deep menus.

8. **Show, don't hide, important state.** Display current file, units, mode, and
   status somewhere persistent (title bar or a status bar at the bottom). The
   user should never wonder "what state am I in?"

9. **Undo / reversibility where feasible.** For editable data, support undo or at
   least easy correction. Prefer non-destructive workflows.

10. **Fast data entry.** Support paste into tables/fields, sensible copy of
    results, and Tab-to-next. For repetitive numeric entry, don't force the mouse.

11. **Helpful errors and units.** Label every field with its unit. Error messages
    say what's wrong AND how to fix it ("Length must be > 0 mm"), not just "Invalid
    input."

12. **Respect the expert.** Don't over-confirm routine actions or add hand-holding
    wizards for things a daily user does dozens of times. Make the fast path the
    default.

## Review checklist before finishing

- [ ] Enter = confirm, Escape = cancel, logical tab order
- [ ] First field focused on open; sensible defaults pre-filled
- [ ] Inputs validated with clear, actionable messages (no tracebacks)
- [ ] Unsaved-work and destructive-action guards in place
- [ ] Heavy work is off the main thread; UI stays responsive with feedback
- [ ] Current file/mode/units/status always visible
- [ ] Window size/position and last inputs remembered between runs
- [ ] Common actions reachable fast; no needless confirmation friction
