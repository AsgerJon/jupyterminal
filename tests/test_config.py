"""The configuration file: what it sets, and what it is told about."""

from __future__ import annotations

import tomllib

import pytest

from jupyterminal.config import (DEFAULT_CONFIG, DEFAULT_KEYS, Config,
                                 default_path, load)


def write(tmp_path, text: str) -> str:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


# -- defaults -----------------------------------------------------------------

def test_the_defaults():
    config = Config()
    assert config.font_family == ""
    assert config.font_size == 11.0
    assert config.shell == ""
    assert config.login_shell is False
    assert config.scrollback_lines == 2_000_000
    assert config.divert_background_output is True
    assert config.confirm_multiline_paste is True
    assert config.word_characters == "-_./~:@%+#=?&"
    assert config.bell == "none"
    assert config.allow_clipboard_write is True
    assert config.keys == DEFAULT_KEYS
    assert config.colors == {}
    assert config.warnings == []


def test_some_default_shortcuts():
    keys = Config().keys
    assert keys["copy"] == ["Ctrl+Shift+C"]
    assert keys["copy_command_and_output"] == ["Ctrl+Alt+Shift+C"]
    assert keys["paste"] == ["Ctrl+Shift+V"]
    assert keys["zoom_in"] == ["Ctrl+=", "Ctrl++"]
    assert keys["blocks.previous"] == ["Up"]
    assert keys["blocks.toggle_collapse"] == []
    assert len(keys) == 28


def test_every_configuration_has_its_own_shortcut_lists():
    first = Config()
    first.keys["copy"].append("F9")
    first.keys["paste"] = []
    first.colors["background"] = "#000000"
    first.warnings.append("something")
    assert DEFAULT_KEYS["copy"] == ["Ctrl+Shift+C"]
    assert DEFAULT_KEYS["paste"] == ["Ctrl+Shift+V"]
    assert Config().keys["copy"] == ["Ctrl+Shift+C"]
    assert Config().keys["paste"] == ["Ctrl+Shift+V"]
    assert Config().colors == {} and Config().warnings == []


def test_the_documented_file_gives_exactly_the_defaults(tmp_path):
    path = write(tmp_path, DEFAULT_CONFIG)
    assert load(path) == Config()
    # Every action is listed in it.
    data = tomllib.loads(DEFAULT_CONFIG)
    listed = {name for name in data["keys"] if name != "blocks"}
    listed |= {f"blocks.{name}" for name in data["keys"]["blocks"]}
    assert listed == set(DEFAULT_KEYS)


# -- where the file is --------------------------------------------------------

def test_the_file_is_under_the_xdg_config_directory(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg/conf")
    monkeypatch.setenv("HOME", "/home/someone")
    assert default_path() == "/xdg/conf/jupyterminal/config.toml"


@pytest.mark.parametrize("xdg", [None, ""])
def test_without_one_it_is_under_dot_config_at_home(monkeypatch, xdg):
    monkeypatch.setenv("HOME", "/home/someone")
    if xdg is None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", xdg)
    assert default_path() == \
        "/home/someone/.config/jupyterminal/config.toml"


def test_load_reads_that_file_when_given_no_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "jupyterminal").mkdir()
    (tmp_path / "jupyterminal" / "config.toml").write_text(
        "[font]\nsize = 15.0\n", encoding="utf-8")
    assert load().font_size == 15.0
    assert load(None).font_size == 15.0
    assert load("").font_size == 15.0


# -- files that cannot be used ------------------------------------------------

def test_a_missing_file_gives_the_defaults_without_a_word(tmp_path):
    assert load(str(tmp_path / "absent.toml")) == Config()


def test_a_file_that_is_not_toml_is_reported(tmp_path):
    text = "[font\nsize = 3\n"
    path = write(tmp_path, text)
    with pytest.raises(tomllib.TOMLDecodeError) as caught:
        tomllib.loads(text)
    config = load(path)
    assert config.warnings == [f"{path}: {caught.value}"]
    config.warnings.clear()
    assert config == Config()


def test_a_file_that_cannot_be_read_is_reported(tmp_path):
    with pytest.raises(OSError) as caught:
        open(str(tmp_path), "rb")
    config = load(str(tmp_path))
    assert config.warnings == [f"{tmp_path}: {caught.value}"]
    config.warnings.clear()
    assert config == Config()


# -- plain settings -----------------------------------------------------------

def test_every_setting_can_be_given(tmp_path):
    config = load(write(tmp_path, '''
[font]
family = "Fira Code"
size = 13.5

[shell]
program = "/bin/zsh"
login = true

[terminal]
scrollback_lines = 5000
divert_background_output = false
confirm_multiline_paste = false
word_characters = "_"
bell = "visual"
allow_clipboard_write = false
'''))
    assert config.warnings == []
    assert config.font_family == "Fira Code"
    assert config.font_size == 13.5
    assert config.shell == "/bin/zsh"
    assert config.login_shell is True
    assert config.scrollback_lines == 5000
    assert config.divert_background_output is False
    assert config.confirm_multiline_paste is False
    assert config.word_characters == "_"
    assert config.bell == "visual"
    assert config.allow_clipboard_write is False
    assert config.keys == DEFAULT_KEYS and config.colors == {}


def test_a_whole_number_is_fine_for_the_font_size(tmp_path):
    config = load(write(tmp_path, "[font]\nsize = 12\n"))
    assert config.warnings == []
    assert config.font_size == 12.0 and type(config.font_size) is float


def test_settings_of_the_wrong_type_are_reported_and_left_alone(tmp_path):
    path = write(tmp_path, '''
[font]
family = 3
size = "big"

[shell]
program = false
login = 1

[terminal]
scrollback_lines = 1.5
divert_background_output = "yes"
confirm_multiline_paste = 0
word_characters = ["a"]
bell = 7
allow_clipboard_write = "no"
''')
    config = load(path)
    assert config.warnings == [
        f"{path}: [font] family should be str",
        f"{path}: [font] size should be float",
        f"{path}: [shell] program should be str",
        f"{path}: [shell] login should be bool",
        f"{path}: [terminal] scrollback_lines should be int",
        f"{path}: [terminal] divert_background_output should be bool",
        f"{path}: [terminal] confirm_multiline_paste should be bool",
        f"{path}: [terminal] word_characters should be str",
        f"{path}: [terminal] bell should be str",
        f"{path}: [terminal] allow_clipboard_write should be bool",
    ]
    config.warnings.clear()
    assert config == Config()


def test_true_and_false_are_not_numbers(tmp_path):
    path = write(tmp_path, "[font]\nsize = true\n"
                           "[terminal]\nscrollback_lines = false\n")
    config = load(path)
    assert config.warnings == [
        f"{path}: [font] size should be float",
        f"{path}: [terminal] scrollback_lines should be int",
    ]
    assert config.font_size == 11.0
    assert config.scrollback_lines == 2_000_000


def test_a_section_that_is_no_table_is_passed_over(tmp_path):
    config = load(write(tmp_path, 'font = "mono"\nshell = 3\n'
                                  'terminal = []\nkeys = 5\n'
                                  'colors = "dark"\n'))
    assert config == Config()


def test_an_empty_file_and_empty_sections_change_nothing(tmp_path):
    assert load(write(tmp_path, "")) == Config()
    assert load(write(tmp_path, "[font]\n[shell]\n[terminal]\n[keys]\n"
                                "[keys.blocks]\n[colors]\n")) == Config()


# -- shortcuts ----------------------------------------------------------------

def test_shortcuts_can_be_rebound_and_unbound(tmp_path):
    config = load(write(tmp_path, '''
[keys]
copy = "Ctrl+C"
paste = ["Ctrl+V", "", "Shift+Ins"]
zoom_in = []
zoom_out = ""

[keys.blocks]
previous = "K"
next = ["J", "N"]
menu = []
'''))
    assert config.warnings == []
    expected = {name: list(keys) for name, keys in DEFAULT_KEYS.items()}
    expected.update({
        "copy": ["Ctrl+C"],
        "paste": ["Ctrl+V", "Shift+Ins"],
        "zoom_in": [],
        "zoom_out": [],
        "blocks.previous": ["K"],
        "blocks.next": ["J", "N"],
        "blocks.menu": [],
    })
    assert config.keys == expected
    assert DEFAULT_KEYS["copy"] == ["Ctrl+Shift+C"]


def test_shortcuts_that_make_no_sense_are_reported(tmp_path):
    path = write(tmp_path, '''
[keys]
nonsense = "X"
find_in_block = 5
filter_output = ["a", 1]
mystery = 7

[keys.blocks]
bogus = "B"
first = true
last = [["End"]]
''')
    config = load(path)
    assert config.warnings == [
        f"{path}: unknown action 'nonsense'",
        f"{path}: find_in_block should be a shortcut or a list of them",
        f"{path}: filter_output should be a shortcut or a list of them",
        f"{path}: unknown action 'mystery'",
        f"{path}: unknown action 'blocks.bogus'",
        f"{path}: blocks.first should be a shortcut or a list of them",
        f"{path}: blocks.last should be a shortcut or a list of them",
    ]
    assert config.keys == DEFAULT_KEYS


def test_blocks_must_be_a_table_of_its_own(tmp_path):
    path = write(tmp_path, '[keys]\nblocks = "Up"\n')
    config = load(path)
    assert config.warnings == [f"{path}: unknown action 'blocks'"]
    assert config.keys == DEFAULT_KEYS


def test_an_action_of_the_blocks_table_is_not_one_of_the_main_table(
        tmp_path):
    path = write(tmp_path, '[keys]\nprevious = "K"\n'
                           '[keys.blocks]\ncopy = "C"\n')
    config = load(path)
    assert config.warnings == [
        f"{path}: unknown action 'previous'",
        f"{path}: unknown action 'blocks.copy'",
    ]
    assert config.keys == DEFAULT_KEYS


# -- colors -------------------------------------------------------------------

def test_colors_are_handed_on_as_written(tmp_path):
    config = load(write(tmp_path, '''
[colors]
background = "#101010"
ansi = ["#000000", "#ff0000"]
'''))
    assert config.colors == {"background": "#101010",
                             "ansi": ["#000000", "#ff0000"]}
    assert config.warnings == []
