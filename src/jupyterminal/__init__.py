"""jupyterminal: a terminal emulator whose session is a list of blocks.

A block is one command plus everything it printed, kept as a unit that can
be selected, copied and navigated to.

* :mod:`jupyterminal.term` - terminal emulation (parser, screen model);
* :mod:`jupyterminal.shell` - the pseudo-terminal and the zsh integration
  that reports where commands start and end;
* :mod:`jupyterminal.session` - blocks, and the router that fills them;
* :mod:`jupyterminal.ui` - the Qt user interface.
"""

__version__ = '0.0.0'
