# jupyterminal startup shim.
#
# zsh finds this file because the terminal starts it with ZDOTDIR pointing
# at a private directory.  Each shim runs the user's own startup file with
# ZDOTDIR set the way the user expects, then points ZDOTDIR back here so
# zsh returns for the next shim.  After the last one ZDOTDIR is restored
# for good; nothing of this is left in the environment of the session.

typeset -g _jt_shim_dir=$ZDOTDIR
if (( ${+JUPYTERMINAL_USER_ZDOTDIR} )); then
  typeset -g _jt_user_zdotdir=$JUPYTERMINAL_USER_ZDOTDIR
  typeset -g _jt_user_zdotdir_x=1
fi

# Switch ZDOTDIR to what the user's files expect.
_jt_enter() {
  if (( ${+_jt_user_zdotdir} )); then
    ZDOTDIR=$_jt_user_zdotdir
    if (( ${_jt_user_zdotdir_x:-0} )); then
      typeset -gx ZDOTDIR
    else
      typeset -g +x ZDOTDIR
    fi
  else
    unset ZDOTDIR
  fi
}

# Remember what the user's file left behind and point back at the shims.
_jt_leave() {
  if (( ${+ZDOTDIR} )); then
    typeset -g _jt_user_zdotdir=$ZDOTDIR
    if [[ ${(t)ZDOTDIR} == *export* ]]; then
      typeset -g _jt_user_zdotdir_x=1
    else
      typeset -g _jt_user_zdotdir_x=0
    fi
  else
    unset _jt_user_zdotdir
  fi
  typeset -g +x ZDOTDIR=$_jt_shim_dir
}

# The last shim has run: restore ZDOTDIR and clean up.
_jt_finish() {
  emulate -L zsh
  setopt extendedglob
  local shim=$_jt_shim_dir name home
  _jt_enter
  # System-wide startup files run between the shims, while ZDOTDIR points
  # here.  A path one of them derived from it - a history file, most
  # importantly - belongs in the user's own directory.
  home=${ZDOTDIR:-$HOME}
  for name in ${(k)parameters[(R)scalar*~*(readonly|special|local)*]}; do
    if [[ $name != (ZDOTDIR|_jt_*|JUPYTERMINAL_*) &&
          ${(P)name} == $shim/* ]]; then
      typeset -g "$name=$home/${${(P)name}#$shim/}" 2>/dev/null
    fi
  done
  unset _jt_shim_dir _jt_user_zdotdir _jt_user_zdotdir_x
  unfunction _jt_enter _jt_leave _jt_finish 2>/dev/null
}

# Prompt themes that paint a placeholder prompt before the shell is ready
# would leave it behind in the startup block.
if [[ -o interactive ]]; then
  typeset -g __p9k_instant_prompt_disabled=1
fi

_jt_enter
{
  if [[ -r ${ZDOTDIR:-$HOME}/.zshenv ]]; then
    builtin source ${ZDOTDIR:-$HOME}/.zshenv
  fi
} always {
  _jt_leave
  if [[ ! -o interactive ]]; then
    # A non-interactive shell reads no further startup files.
    unset JUPYTERMINAL_NONCE JUPYTERMINAL_DIR JUPYTERMINAL_SHELL_PID \
          JUPYTERMINAL_USER_ZDOTDIR
    _jt_finish
  elif [[ ! -o rcs ]]; then
    # The user's zshenv turned the remaining startup files off.
    builtin source $_jt_shim_dir/integration.zsh
    _jt_finish
  fi
}
