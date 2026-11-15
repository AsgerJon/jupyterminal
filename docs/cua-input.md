# CUA input: addendum to the functional spec

This addendum changes one decision of *Block Terminal: Cleanroom
Functional Spec* and leaves the rest of it standing. Like the spec, it
describes behavior only. Where it decides something by itself, it says
(decision).

## What changes, and why

The spec says: "Shell editing is preserved: the user's shell keeps doing
line editing … The terminal does not replace these with its own editor",
and lists "its own command-line editor replacing the shell's" as out of
scope. That keeps the one part of a terminal that works unlike everything
else on the desktop.

A shell's line editor takes its keys from the editors of the 1970s:
Ctrl+A goes to the start of the line, nothing is selected with Shift, and
the mouse does not place the cursor. Every other text field follows the
conventions IBM published in 1987 as Common User Access (CUA): a click
places the cursor, Shift and the arrows select, typing replaces what is
selected, Ctrl+C, Ctrl+X and Ctrl+V are the clipboard, Ctrl+Z takes back.

**The input area is a text box that follows CUA.** The shell still runs
every command; it no longer edits them.

## The text box

The input area sits where the spec puts it: pinned at the bottom of the
window, below the newest block. It is a text box and behaves like one:

- A click places the cursor. Dragging selects; a double-click selects a
  word, a triple-click a line.
- Shift with the arrows, Home, End, and with Ctrl for whole words,
  selects. Ctrl+A selects everything in the box.
- Typing replaces the selection. Backspace and Delete remove it.
- Ctrl+C copies the selection, Ctrl+X cuts it, Ctrl+V pastes. Ctrl+Z takes
  the last change back and Ctrl+Y redoes it.
- The box holds several lines. It grows with its text up to eight lines
  (decision) and scrolls beyond that.

Above the box, one line says where the shell is: its working directory
and, in a git repository, the branch. The shell's own prompt is not shown
(decision): it would be the prompt of a line editor nobody types in.

## Running a command

- **Ctrl+Enter** hands the text to the shell, which runs it exactly as if
  it had been typed at its prompt: with its aliases, its functions and its
  history. The box empties.
- **Enter** starts a new line, as in every text box of several lines. So
  does Enter with Shift, with Alt, or with Ctrl and another modifier.
  (decision: a command is sent on purpose; no key of the text sends it.)
- Ctrl+Enter on a box that holds nothing but blanks does nothing, as an
  empty submission creates no block.
- Several lines are handed over together and run as one command.
- A command the shell cannot run because it is **not complete** (a loop
  without its end, an open quote) is not left half entered in the shell:
  the shell is told to drop it, the text comes back into the box, and the
  line above the box says that it was not run. (decision)
- While a command runs, the keyboard belongs to it, as the spec has it:
  the box is inactive and what is typed goes to the program. When the
  command ends, the keyboard returns to the box, unless a block is
  selected or the find field is in use.
- If the shell asks something before it runs a command (whether to
  correct a misspelt name, say), its own input line is shown for as long
  as it asks, and the keys go to the shell. (decision: the question is
  shown once the command has not started for 0.3 seconds.)

## Ctrl+C

Ctrl+C is "copy" wherever there is something to copy, and "interrupt"
where there is not. (decision)

| With | Ctrl+C |
|---|---|
| Text selected in the box | Copies it |
| Text or blocks selected in the output | Copies them, and clears that selection |
| Nothing selected, a command running | Interrupts the command |
| Nothing selected, nothing running | Empties the box (Ctrl+Z brings the text back) and starts the walk through the history over |

Because copying clears the selection in the output, a second Ctrl+C
always reaches the running command. A full-screen program gets every
Ctrl+C. The spec's Ctrl+Shift+C keeps working everywhere.

## One selection, and letting go of it

Whatever is selected, the same things let go of it, with either way of
typing commands. (decision)

- **Escape**, pressed in the output or in the box. While something is
  selected in the output it is not a key for the shell or for a running
  program. A full-screen program gets every Escape.
- **A click.** With text selected in the output, a plain click lets go of
  it and does nothing else, as a click in any text does; the click that
  selects a block is the next one. A second click on the one selected
  block lets go of that block. A click in the box lets go of what is
  selected in the output.
- **Typing**, in the box or with the keyboard in the output.
- **Ctrl+C**, which copies first (see above).

There is one selection in the window at a time: selecting text in the box
lets go of what is selected in the output, and selecting or clicking in
the output lets go of what is selected in the box.

## What only the shell can do: completing, the history

The box edits the text; what a command line means is the shell's to
know. Three keys hand the text to the shell and take its answer.
(decision)

- **Tab** completes at the cursor. **Up** on the first line and **Down**
  on the last one fetch lines of the history; elsewhere in the text they
  move the cursor.
- Each does **what that key is bound to in the user's shell**, on the
  text of the box: the user's completion system and its settings, a
  history search bound to Up, Shift+Tab where it is bound to something.
  The text and the place of the cursor come back and take the place of
  what was in the box, as one step that Ctrl+Z takes back.
- A key does what it would do **typed at the shell's own prompt in the
  same place**. Pressed again with nothing done in between, it goes on
  where it stopped: the next entry of the completions, the next line of
  a search. If something was typed or the cursor moved in between, it
  starts anew, even where the text is the same again.
- The history is the shell's own: it holds earlier sessions, follows the
  shell's options, and keeps what was being typed for the way back down.
  Text the shell has not seen (something typed, the box emptied) is on
  the newest line: the walk starts over from there.
- A **list** the shell shows for the box (the possible completions)
  appears between the blocks and the box, without the shell's own line.
  It goes when the text changes.
- If the shell needs **keys of its own** for what it was asked (a menu to
  choose from, a question such as whether to list several hundred
  matches), its own input line is shown and the keyboard is the shell's
  until it has answered. Then the box has the result and the keyboard.
- Keys pressed while the shell is at work wait for its answer and are
  typed after it, so nothing typed quickly is lost or lands in the old
  text. If the shell turns out to need the keyboard, they are its own.

(decision: the shell is given 0.15 seconds from the moment it puts
something on the screen, and 3 seconds if it shows nothing, before it is
taken to need the keyboard.)

## What the shell's plugins see

Two plugins that many configurations of zsh load draw on the shell's own
command line, which the box takes the place of. Where the shell has
them, the box shows what they have to say. (decision)

- **zsh-syntax-highlighting** colors the command line as it is typed: the
  name of a command that exists, one that does not, quoted text, a path.
  The box gives its text those colors, in the styles the plugin is
  configured with. Colors lie on the text; they are no part of it.
- **zsh-autosuggestions** shows, dimmed and behind the cursor, how the
  line might go on, as a rule the way an earlier command did. The box
  shows that behind its cursor, where the cursor is at the end of the
  text and nothing is selected. It is no part of the text either.
  **Right** or **End** makes it text, **Ctrl+Right** the next word of
  it; each is a step that Ctrl+Z takes back. Typed along, it stays;
  typed away from, it goes at once.
- Of a suggestion of several lines the first is shown, with a sign that
  there is more; one that is too long for the line is cut short.
- The shell says with every prompt whether it has such plugins. One that
  has none is not asked: nothing passes between the box and the shell
  while a command is typed.
- What the plugins themselves would draw on the shell's line stays off
  it while the box is the input.

(decision: the shell is asked about a text when it has answered about
the one before; of texts that follow each other faster than that, only
the last is asked about.)

## Other keys in the box

- **Ctrl+D** on an empty box ends the shell's input, which ends the
  session. **Ctrl+L** clears the screen as the spec describes.
- The view's shortcuts work from the box, except the two that mean
  something else in a text box: Shift+Home and Shift+End select text
  there and do not scroll.

## Pasting

Pasting at the prompt puts the text into the box and runs nothing, however
many lines it has; nothing runs before Ctrl+Enter, so the spec's
confirmation of a multi-line paste is not needed there. Pasting into a
running program is unchanged, confirmation included.

## Re-run

"Re-run command" puts the block's command into the box, unsubmitted.

## Keeping the old input

A setting chooses how commands are typed: `input = "editor"` (the text
box, the default) or `input = "shell"` (the shell's own line editor, as
the spec describes it). (decision)

## Not yet

- **Editing an earlier block in place** and running it again there.
- **Programs that read whole lines** (a REPL, a yes/no question) still
  get their keys one by one, edited by the program itself.

## Acceptance

In a zsh session on Linux, with the default configuration:

- Typing `echo hi`, selecting `hi` with Shift+Ctrl+Left and typing `ho`
  leaves `echo ho` in the box; Ctrl+Enter creates a block for `echo ho`
  whose output is `ho`, and the box is empty.
- Ctrl+A, Ctrl+C in the box, then Ctrl+V twice, doubles the text; Ctrl+Z
  takes one paste back. The shell receives nothing until Ctrl+Enter.
- A click in the middle of the text places the cursor there; what is
  typed next is inserted at that point.
- Enter starts a new line and runs nothing; three lines typed that way
  run as one command on Ctrl+Enter.
- `for i in 1 2; do` and Ctrl+Enter creates no block; the text is back in
  the box, marked as not run, and completing it there runs it.
- With text selected in a block's output, Ctrl+C puts it on the clipboard;
  pressing Ctrl+C again while `ping` runs stops `ping`.
- Pasting three lines at the prompt runs nothing.
- In a directory with `alpha.txt`, `alphabet.txt` and `beta.txt`:
  `ls be` and Tab leaves `ls beta.txt ` in the box; `ls al` and Tab
  leaves `ls alpha`; another Tab lists the two files under the blocks;
  the next ones put `ls alpha.txt` and then `ls alphabet.txt` in the box,
  in the order the shell lists them in. Nothing has run.
- After `echo one` and `echo two`, with `dra` typed: Up shows `echo two`,
  Up again `echo one`, Down `echo two`, Down `dra`.
- With `zstyle ':completion:*' menu select` in the shell's configuration,
  the Tab that opens the menu gives it the keyboard: an arrow key moves
  in the menu, Enter takes the entry, and the box has it and the
  keyboard.
- With Up and Down bound to `history-beginning-search-backward-end` and
  `-forward-end` in the shell's configuration, after `echo three`,
  `echo one`, `true`, `echo two`, `false` and with `ec` typed: Up shows
  `echo two`, `echo one`, `echo three`, and Down the way back to `ec`,
  as the same keys do at the shell's own prompt. With the cursor moved
  three to the left in `echo two`, Up shows `echo one`.
- With zsh-syntax-highlighting and zsh-autosuggestions loaded, after
  `echo one two`: typing `echo o` shows `echo` in the color of a command
  that exists and `ne two` dimmed behind the cursor; Right leaves
  `echo one two` in the box, and Ctrl+Z `echo o`. Typing `nosuch` shows
  it in the color of a command that does not exist. Nothing of this is
  printed, and no block is created.
- With `setopt nounset`, `errreturn`, `ksharrays` or `warnnestedvar` in
  the shell's configuration, all of the above still holds and the shell
  prints no complaint.
- With a block selected, each of these leaves nothing selected: a second
  click on it, a click in the box, Escape pressed in the box, a letter
  typed.
- With text selected in a block's output, a click on a block lets go of
  the text and selects no block; the next click selects the block.
- Text selected in the box is no longer selected once a block is.
- With `input = "shell"`, every acceptance check of the spec passes
  unchanged.
