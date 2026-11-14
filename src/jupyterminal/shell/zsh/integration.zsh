# jupyterminal zsh integration.
#
# Reports to the terminal, through private escape sequences ("marks"),
# when a command starts, what its exact text is, when it ends and with
# which status - so the terminal never has to guess from what the output
# looks like.  Every mark carries a per-session nonce; text that merely
# contains such a sequence (a file printed with cat, say) is ignored.
#
#   E;status         a command (or an empty prompt cycle) has ended
#   P;status;notify;cwd;keytimeout   the prompt cycle begins
#   A                the prompt is being drawn from its first character
#   I                the line editor is ready for input
#   F;buffer         the line editor is handing over this text
#   T                the line editor moved below its display to let
#                    something else print
#   C;cwd;command    this command is about to run
#   S;seq;count      reply to a sync request: the line editor is idle

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
  _jt_mark "P;$st;${options[notify]};$REPLY;${KEYTIMEOUT:-40}"
}

# Runs last: by now every theme has set the prompt it wants.
_jt_precmd_last() {
  if [[ ${precmd_functions[1]} != _jt_precmd ||
        ${precmd_functions[-1]} != _jt_precmd_last ]]; then
    precmd_functions=(_jt_precmd
                      ${precmd_functions:#_jt_precmd(|_last)}
                      _jt_precmd_last)
  fi
  if [[ ${preexec_functions[-1]} != _jt_preexec ]]; then
    preexec_functions=(${preexec_functions:#_jt_preexec} _jt_preexec)
  fi
  # The end-of-line mark is written by zsh before anything else once a
  # command returns: the earliest, most exact end-of-command signal.
  setopt promptsp promptcr
  PROMPT_EOL_MARK=$_jt_eol_mark
  [[ $POSTEDIT == *"$_jt_t_mark"* ]] || POSTEDIT+=$_jt_t_mark
  if [[ -o promptpercent && $PS1 != *"$_jt_a_mark"* ]]; then
    PS1=${_jt_a_mark}${PS1}
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
() {
  local keymap
  for keymap in emacs viins vicmd; do
    builtin bindkey -M $keymap '\e[7717;1~' _jt_set_buffer 2>/dev/null
  done
}

# -- installation ------------------------------------------------------------

typeset -ga precmd_functions preexec_functions
precmd_functions=(_jt_precmd
                  ${precmd_functions:#_jt_precmd(|_last)}
                  _jt_precmd_last)
preexec_functions=(${preexec_functions:#_jt_preexec} _jt_preexec)
setopt promptsp promptcr
PROMPT_EOL_MARK=$_jt_eol_mark
[[ $POSTEDIT == *"$_jt_t_mark"* ]] || POSTEDIT+=$_jt_t_mark
_jt_zle_hooks
