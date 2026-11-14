# jupyterminal startup shim: see .zshenv next to this file.

if (( ${+functions[_jt_enter]} )); then
  _jt_enter
  {
    if [[ -r ${ZDOTDIR:-$HOME}/.zprofile ]]; then
      builtin source ${ZDOTDIR:-$HOME}/.zprofile
    fi
  } always {
    _jt_leave
  }
fi
