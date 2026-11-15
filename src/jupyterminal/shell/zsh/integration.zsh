# jupyterminal zsh integration.
#
# Reports to the terminal, through private escape sequences ("marks"),
# when a command starts, what its exact text is, when it ends and with
# which status - so the terminal never has to guess from what the output
# looks like.  Every mark carries a per-session nonce; text that merely
# contains such a sequence (a file printed with cat, say) is ignored.
#
#   E;status         a command (or an empty prompt cycle) has ended
#   P;status;notify;cwd;keytimeout;plugins
#                    the prompt cycle begins.  plugins is 1 if the
#                    configuration has plugins that have something to say
#                    about a command line (see "look" below)
#   A                the prompt is being drawn from its first character
#   I                the line editor is ready for input
#   F;buffer         the line editor is handing over this text
#   T                the line editor moved below its display to let
#                    something else print
#   C;cwd;command    this command is about to run
#   S;seq;count      reply to a sync request: the line editor is idle
#   B;cursor;buffer;suggestion;colors
#                    reply to a request of the text box: the line editor's
#                    text, and where its cursor is, after doing what a key
#                    does in this shell; and what the plugins make of it
#   V;number;suggestion;colors
#                    reply to a request to look at a text: what the
#                    plugins make of it

[[ -o interactive ]] || return 0
(( ${+JUPYTERMINAL_NONCE} )) || return 0
if [[ ${JUPYTERMINAL_SHELL_PID:-$$} != $$ ]] || (( ${+_jt_nonce} )); then
  # A child shell, or this shell is integrated already.
  unset JUPYTERMINAL_NONCE JUPYTERMINAL_DIR JUPYTERMINAL_SHELL_PID \
        JUPYTERMINAL_USER_ZDOTDIR
  return 0
fi

typeset -g _jt_nonce=$JUPYTERMINAL_NONCE
typeset -g _jt_dir=${JUPYTERMINAL_DIR:-${_jt_shim_dir:-$ZDOTDIR}}
unset JUPYTERMINAL_NONCE JUPYTERMINAL_DIR JUPYTERMINAL_SHELL_PID \
      JUPYTERMINAL_USER_ZDOTDIR

zmodload zsh/zle zsh/parameter zsh/zleparameter 2>/dev/null
zmodload zsh/system 2>/dev/null
autoload -Uz add-zsh-hook add-zle-hook-widget 2>/dev/null

# Marks go straight to the terminal, so they still arrive when the
# shell's own output has been redirected.
typeset -gi _jt_tty=1
if ! { (( ${+builtins[sysopen]} )) &&
       sysopen -w -o cloexec -u _jt_tty /dev/tty 2>/dev/null }; then
  exec {_jt_tty}>/dev/tty 2>/dev/null || _jt_tty=1
fi

typeset -g _jt_a_mark=$'%{\e]7717;'"${_jt_nonce}"$';A\a%}'
typeset -g _jt_t_mark=$'\e]7717;'"${_jt_nonce}"$';T\a'
typeset -g _jt_eol_mark=$'%{\e]7717;'"${_jt_nonce}"$';E;%?\a%}'

_jt_mark() {
  builtin print -rn -u $_jt_tty -- $'\e]7717;'"${_jt_nonce};$1"$'\a'
}

# REPLY = $1 made safe to travel in a mark: control characters, ';' and
# '%' become %XX.
_jt_esc() {
  emulate -L zsh
  setopt extendedglob
  REPLY=${1//(#m)[[:cntrl:]\;%]/%${(l:2::0:)$(( [##16] #MATCH ))}}
}

# -- the prompt cycle --------------------------------------------------------

# Runs first among the precmd hooks, so that what the others print belongs
# to the prompt and not to the command that just ended.
_jt_precmd() {
  local st=$?
  (( ${_jt_handoff:-0} )) && _jt_handoff_undo
  _jt_esc "$PWD"
  _jt_mark "P;$st;${options[notify]};$REPLY;${KEYTIMEOUT:-40};$((
    ${+functions[_zsh_highlight]} ||
    ${+functions[_zsh_autosuggest_fetch_suggestion]} ))"
}

# _jt_precmd first and _jt_precmd_last last among the prompt hooks,
# _jt_preexec last among those that run before a command.  Under zsh's own
# options: with KSH_ARRAYS the arrays would be read differently.
_jt_order_hooks() {
  emulate -L zsh
  typeset -ga precmd_functions preexec_functions
  if [[ ${precmd_functions[1]-} != _jt_precmd ||
        ${precmd_functions[-1]-} != _jt_precmd_last ]]; then
    precmd_functions=(_jt_precmd
                      ${precmd_functions:#_jt_precmd(|_last)}
                      _jt_precmd_last)
  fi
  if [[ ${preexec_functions[-1]-} != _jt_preexec ]]; then
    preexec_functions=(${preexec_functions:#_jt_preexec} _jt_preexec)
  fi
}

# Runs last: by now every theme has set the prompt it wants.
_jt_precmd_last() {
  _jt_order_hooks
  # The end-of-line mark is written by zsh before anything else once a
  # command returns: the earliest, most exact end-of-command signal.
  setopt promptsp promptcr
  # Whatever the configuration sets: with NO_UNSET a parameter nobody has
  # set must not be read bare, and with WARN_NESTED_VAR a global is set
  # by saying that it is one.
  typeset -g PROMPT_EOL_MARK=$_jt_eol_mark
  [[ ${POSTEDIT-} == *"$_jt_t_mark"* ]] ||
    typeset -g POSTEDIT=${POSTEDIT-}$_jt_t_mark
  if [[ -o promptpercent && ${PS1-} != *"$_jt_a_mark"* ]]; then
    typeset -g PS1=${_jt_a_mark}${PS1-}
  fi
  _jt_zle_hooks
}

# Runs last among the preexec hooks, so nothing they print lands in the
# command's block.
_jt_preexec() {
  emulate -L zsh
  setopt extendedglob
  local cmd=${1:-$3}
  cmd=${cmd%%$'\n'##}
  _jt_esc "$PWD"
  local cwd=$REPLY
  _jt_esc "$cmd"
  _jt_mark "C;$cwd;$REPLY"
  # "exec zsh" replaces this shell.  Hand the integration over to the new
  # one; if the exec does not happen, the next prompt undoes this.
  if _jt_execs_zsh "${3:-$cmd}"; then
    _jt_handoff_do
  fi
}

# True when a command line is about to replace this shell with zsh:
# "exec zsh", "exec -l $SHELL", "cd . && exec zsh", an alias for one.
_jt_execs_zsh() {
  emulate -L zsh
  setopt extendedglob
  local -a words
  words=(${(z)1})
  local -i i=1 n=$#words
  local word
  while (( i <= n )); do
    if [[ $words[i] == exec ]] && { (( i == 1 )) ||
         [[ $words[i-1] == (\;|\&\&|\|\||\||\&|\{|\(|then|do|else|\!|time|nocorrect|noglob) ]] }; then
      (( i++ ))
      while (( i <= n )) && [[ $words[i] == -* ]]; do
        [[ $words[i] == -[cl]#a ]] && (( i++ ))
        (( i++ ))
      done
      word=${(Q)words[i]-}
      [[ $word == (\$SHELL|\$\{SHELL\}) ]] && word=$SHELL
      [[ ${word:t} == zsh ]] && return 0
    fi
    (( i++ ))
  done
  return 1
}

_jt_handoff_do() {
  typeset -g _jt_handoff=1
  unset JUPYTERMINAL_USER_ZDOTDIR
  if (( ${+ZDOTDIR} )); then
    typeset -g _jt_saved_zdotdir=$ZDOTDIR
    if [[ ${(t)ZDOTDIR} == *export* ]]; then
      typeset -g _jt_saved_zdotdir_x=1
      # Only an exported ZDOTDIR would have reached the new shell.
      export JUPYTERMINAL_USER_ZDOTDIR=$ZDOTDIR
    else
      typeset -g _jt_saved_zdotdir_x=0
    fi
  else
    unset _jt_saved_zdotdir
  fi
  export ZDOTDIR=$_jt_dir JUPYTERMINAL_NONCE=$_jt_nonce \
         JUPYTERMINAL_DIR=$_jt_dir JUPYTERMINAL_SHELL_PID=$$
}

_jt_handoff_undo() {
  typeset -g _jt_handoff=0
  unset JUPYTERMINAL_NONCE JUPYTERMINAL_DIR JUPYTERMINAL_SHELL_PID \
        JUPYTERMINAL_USER_ZDOTDIR
  if (( ${+_jt_saved_zdotdir} )); then
    ZDOTDIR=$_jt_saved_zdotdir
    if (( ${_jt_saved_zdotdir_x:-0} )); then
      typeset -gx ZDOTDIR
    else
      typeset -g +x ZDOTDIR
    fi
    unset _jt_saved_zdotdir _jt_saved_zdotdir_x
  else
    unset ZDOTDIR
  fi
}

# -- the line editor ---------------------------------------------------------

_jt_line_init() { _jt_mark I }

_jt_line_finish() {
  _jt_esc "$BUFFER"
  _jt_mark "F;$REPLY"
}

_jt_zle_hooks() {
  # Under zsh's own options: add-zle-hook-widget gives up where the
  # configuration has set ERR_RETURN.
  emulate -L zsh
  (( ${+functions[add-zle-hook-widget]} )) || return 0
  if (( ${_jt_zle_ok:-0} )) &&
     [[ ${widgets[zle-line-init]} == user:azhw:zle-line-init &&
        ${widgets[zle-line-finish]} == user:azhw:zle-line-finish ]]; then
    return 0
  fi
  add-zle-hook-widget zle-line-init _jt_line_init 2>/dev/null
  add-zle-hook-widget zle-line-finish _jt_line_finish 2>/dev/null
  typeset -g _jt_zle_ok=1
}

# The terminal asks "are you idle?" by writing a sequence number to a
# fifo.  zsh calls this handler only while the line editor waits for a
# key with nothing left to draw, so the reply marks a point at which any
# further output cannot be the line editor's own - unless other handlers
# are registered that may redraw it, which is why their count is sent.
_jt_sync() {
  local line seq
  local -i count=0
  builtin zle -FL >&$_jt_sync_fd 2>/dev/null
  while builtin read -r -t 0 -u $_jt_sync_fd line 2>/dev/null; do
    if [[ $line == zle\ * ]]; then
      (( count++ ))
    else
      seq=$line
    fi
  done
  [[ -n $seq ]] && _jt_mark "S;$seq;$count"
  return 0
}

if (( ${+builtins[sysopen]} )) && [[ -p $_jt_dir/sync ]]; then
  typeset -gi _jt_sync_fd=-1
  if sysopen -rw -o cloexec -u _jt_sync_fd $_jt_dir/sync 2>/dev/null; then
    builtin zle -F $_jt_sync_fd _jt_sync 2>/dev/null
  fi
fi

# "Re-run command" puts text into the editing buffer, unsubmitted: the
# terminal writes it to a file and sends this private key.
_jt_set_buffer() {
  local file=$_jt_dir/buffer
  [[ -r $file ]] || return 0
  BUFFER=$(<$file)
  CURSOR=$#BUFFER
}
builtin zle -N _jt_set_buffer

# The text box asks the line editor.  Completing a word and walking
# through the history are the shell's to do, and so is what its plugins
# know: how zsh-syntax-highlighting colors a command line, how
# zsh-autosuggestions would go on with it.  The text box leaves a request
# in a file and sends a private key:
#
#   edit   its text, the place of its cursor, a key (Tab, Up, Down) and
#          whether the text was edited since the last such request.  What
#          that key runs in this shell is run on the text; text, cursor
#          and what the plugins say go back in the mark B.
#   look   its text.  What the plugins say goes back in the mark V, with
#          the number of the request.
#
# _jt_request reads such a file: it sets _jt_text, _jt_cursor, _jt_edited,
# _jt_seq and _jt_widget of its caller.  A key that runs nothing useful
# here falls back on the widget the request names.
_jt_request() {
  emulate -L zsh
  local file=$_jt_dir/$1 key fallback bound=
  [[ -r $file ]] || return 1
  {
    IFS= read -r _jt_cursor && IFS= read -r key && IFS= read -r fallback &&
      IFS= read -r _jt_edited && IFS= read -r _jt_seq || return 1
    IFS= read -r -d '' _jt_text
  } < $file
  [[ $_jt_cursor == <-> ]] || return 1
  # The text ends in a full stop, which keeps its last newlines.
  _jt_text=${_jt_text%.}
  if [[ -n $key ]]; then
    bound=$(builtin bindkey -M main -- $key 2>/dev/null)
    bound=${bound##* }
    [[ $bound == (undefined-key|self-insert|self-insert-unmeta) ]] && bound=
  fi
  _jt_widget=${bound:-$fallback}
}

# The text of a request becomes the text of the line editor.  This and
# the widgets below run under the options of the configuration, as the
# widgets and plugins they call expect; nothing in them may trip over one.
_jt_take() {
  # Text the line editor has not seen belongs on the newest line of the
  # history, not on one that was fetched from it.  (The dot names zsh's
  # own widget: what a plugin has wrapped around it is not meant here.)
  [[ $BUFFER == "$_jt_text" ]] || builtin zle .end-of-history || :
  # Setting text and cursor, even to what they are, ends what the line
  # editor was in the middle of, a walk through a menu of completions
  # say: as typing does.
  BUFFER=$_jt_text
  CURSOR=$_jt_cursor
}

# REPLY = what the plugins of the configuration make of the line editor's
# text, as two fields of a mark: the line zsh-autosuggestions would go on
# to, and the colors zsh-syntax-highlighting gives the text.  Each is
# empty where its plugin is not there, or has been told to keep quiet.
_jt_describe() {
  local _jt_spans= _jt_go=
  if [[ -n $BUFFER ]] && (( ! ${+_ZSH_AUTOSUGGEST_DISABLED} )) &&
     (( ${+functions[_zsh_autosuggest_fetch_suggestion]} )) &&
     { [[ -z ${ZSH_AUTOSUGGEST_BUFFER_MAX_SIZE-} ]] ||
       (( $#BUFFER <= ZSH_AUTOSUGGEST_BUFFER_MAX_SIZE )) }; then
    _zsh_autosuggest_fetch_suggestion "$BUFFER" || :
    _jt_go=${suggestion-}
  fi
  if (( ${+functions[_zsh_highlight]} )); then
    _zsh_highlight || :
    _jt_spans_of
  fi
  _jt_esc "$_jt_go"
  _jt_go=$REPLY
  _jt_esc "$_jt_spans"
  # (Said to be a global: WARN_NESTED_VAR complains about one that is
  # set from inside a function without a word.)
  typeset -g REPLY="$_jt_go;$REPLY"
}

# _jt_spans of the caller = the colors of the line editor's text, a
# "start end style" to the line.
_jt_spans_of() {
  emulate -L zsh
  local span
  local -a spans
  for span in "${region_highlight[@]}"; do
    # (Those that begin with a P are about what stands before the text.)
    [[ $span == <->' '<->' '* ]] && spans+=("${span% memo=*}")
  done
  _jt_spans=${(F)spans}
}

# Runs a widget for the text box.  zsh-autosuggestions looks for a
# suggestion after every widget and draws it behind the line editor's
# text; here the text box shows what it suggests (see _jt_describe), so
# for the time of the widget that plugin is told that it is switched off.
_jt_run() {
  local _ZSH_AUTOSUGGEST_DISABLED=
  # With -w the widget finds its own name in WIDGET, as it does when a
  # key runs it: several widgets may share one function and tell by the
  # name what to do.
  builtin zle "$1" -w || :
}

# Makes $1 the widget that was used last (LASTWIDGET) for the one that
# runs next.  A widget that goes on where its last use stopped, such as a
# search through the history, knows by that name that nothing came in
# between.  zsh gives the name only to a widget that has just run; so the
# name runs nothing for a moment and gets its widget back.
_jt_nop() { : }
_jt_was() {
  emulate -L zsh
  [[ -n $1 && $1 != .* && -n ${widgets[$1]-} ]] || return 0
  builtin zle -A -- $1 _jt_kept 2>/dev/null || return 0
  {
    builtin zle -N -- $1 _jt_nop && builtin zle -- $1
  } always {
    builtin zle -A -- _jt_kept $1
    builtin zle -D -- _jt_kept
  }
}

_jt_edit() {
  local _jt_text _jt_cursor _jt_edited _jt_seq _jt_widget _jt_said
  _jt_request edit || return 0
  if [[ -z $_jt_edited && $BUFFER == "$_jt_text" &&
        $CURSOR == "$_jt_cursor" ]]; then
    # Nothing has happened to the text since the line editor last had it.
    # It is left alone, so that the next Tab goes on where the last one
    # stopped; and if that was a request too, this key follows the key of
    # that one as if nothing of the text box had been in between.
    [[ $LASTWIDGET != _jt_edit ]] || _jt_was "${_jt_last-}"
  else
    _jt_take
  fi
  [[ -z $_jt_widget ]] || _jt_run "$_jt_widget"
  typeset -g _jt_last=$_jt_widget
  # Whatever a plugin shows behind the text is for a display the text box
  # has taken the place of; under the text there is to be nothing but
  # what the widget lists.
  [[ -z ${POSTDISPLAY-} ]] || POSTDISPLAY=
  # The display comes first.  If the widget asks something ("do you wish
  # to see all 150 possibilities?") or opens a menu, it does so before
  # the answer below, and the terminal gives it the keyboard meanwhile.
  builtin zle -R || :
  _jt_describe
  _jt_said=$REPLY
  _jt_esc "$BUFFER"
  _jt_mark "B;$CURSOR;$REPLY;$_jt_said"
}
builtin zle -N _jt_edit

_jt_look() {
  local _jt_text _jt_cursor _jt_edited _jt_seq _jt_widget
  _jt_request look || return 0
  _jt_take
  _jt_describe
  _jt_mark "V;$_jt_seq;$REPLY"
}
builtin zle -N _jt_look

() {
  local keymap
  for keymap in emacs viins vicmd; do
    builtin bindkey -M $keymap '\e[7717;1~' _jt_set_buffer 2>/dev/null
    builtin bindkey -M $keymap '\e[7717;2~' _jt_edit 2>/dev/null
    builtin bindkey -M $keymap '\e[7717;3~' _jt_look 2>/dev/null
  done
}

# -- installation ------------------------------------------------------------

_jt_order_hooks
setopt promptsp promptcr
PROMPT_EOL_MARK=$_jt_eol_mark
[[ ${POSTEDIT-} == *"$_jt_t_mark"* ]] || POSTEDIT=${POSTEDIT-}$_jt_t_mark
_jt_zle_hooks
