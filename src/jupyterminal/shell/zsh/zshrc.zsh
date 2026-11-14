# jupyterminal startup shim: see .zshenv next to this file.

if (( ${+functions[_jt_enter]} )); then
  _jt_enter
  {
    if [[ -r ${ZDOTDIR:-$HOME}/.zshrc ]]; then
      builtin source ${ZDOTDIR:-$HOME}/.zshrc
    fi
  } always {
    _jt_leave
    # The integration loads after the user's configuration so its hooks
    # see the final prompt and run in a known position.
    builtin source $_jt_shim_dir/integration.zsh
    # A login shell still has .zlogin to come.
    if [[ ! -o login ]]; then
      _jt_finish
    fi
  }
fi
