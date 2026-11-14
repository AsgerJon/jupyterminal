# jupyterminal startup shim: see .zshenv next to this file.

if (( ${+functions[_jt_enter]} )); then
  _jt_enter
  {
    if [[ -r ${ZDOTDIR:-$HOME}/.zlogin ]]; then
      builtin source ${ZDOTDIR:-$HOME}/.zlogin
    fi
  } always {
    _jt_leave
    _jt_finish
  }
fi
