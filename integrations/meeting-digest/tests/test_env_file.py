import os
import stat

from meeting_digest.config import Config, load_env_file

VALID_KEY = "omi_dev_" + "a" * 32


def _write(tmp_path, body: str, mode: int = 0o600) -> str:
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    os.chmod(str(path), mode)
    return str(path)


def test_loads_simple_assignments(tmp_path):
    env = {}
    path = _write(tmp_path, "OMI_API_KEY={}\nMD_LOOKBACK_HOURS=6\n".format(VALID_KEY))

    assert load_env_file(path, environ=env) == 2
    assert env["OMI_API_KEY"] == VALID_KEY
    assert env["MD_LOOKBACK_HOURS"] == "6"


def test_a_real_environment_variable_wins(tmp_path):
    # So a one-off `OMI_API_KEY=... python -m meeting_digest` still overrides
    # whatever the file says.
    env = {"OMI_API_KEY": "omi_dev_from_environment"}
    path = _write(tmp_path, "OMI_API_KEY={}\n".format(VALID_KEY))

    load_env_file(path, environ=env)

    assert env["OMI_API_KEY"] == "omi_dev_from_environment"


def test_comments_and_blank_lines_are_ignored(tmp_path):
    env = {}
    path = _write(tmp_path, "# a comment\n\n  \nMD_SINKS=markdown\n")

    assert load_env_file(path, environ=env) == 1
    assert env == {"MD_SINKS": "markdown"}


def test_an_export_prefix_is_accepted(tmp_path):
    # People paste the same lines they were exporting by hand.
    env = {}
    path = _write(tmp_path, "export MD_SINKS=markdown,slack\n")

    load_env_file(path, environ=env)

    assert env["MD_SINKS"] == "markdown,slack"


def test_surrounding_quotes_are_stripped(tmp_path):
    env = {}
    path = _write(tmp_path, 'MD_SLACK_WEBHOOK_URL="https://hooks.slack.com/services/x"\n')

    load_env_file(path, environ=env)

    assert env["MD_SLACK_WEBHOOK_URL"] == "https://hooks.slack.com/services/x"


def test_a_value_containing_equals_is_kept_whole(tmp_path):
    env = {}
    path = _write(tmp_path, "MD_NOTION_DATABASE_ID=https://notion.so/x/abc?v=123\n")

    load_env_file(path, environ=env)

    assert env["MD_NOTION_DATABASE_ID"] == "https://notion.so/x/abc?v=123"


def test_a_malformed_line_is_skipped_not_fatal(tmp_path):
    env = {}
    path = _write(tmp_path, "this line has no equals sign\nMD_SINKS=markdown\n")

    assert load_env_file(path, environ=env) == 1
    assert env["MD_SINKS"] == "markdown"


def test_a_missing_file_is_a_no_op(tmp_path):
    env = {}

    assert load_env_file(str(tmp_path / "absent.env"), environ=env) == 0
    assert env == {}


def test_a_world_readable_file_warns_but_still_loads(tmp_path, caplog):
    # Refusing to run would be worse than running with a note in the log.
    env = {}
    path = _write(tmp_path, "OMI_API_KEY={}\n".format(VALID_KEY), mode=0o644)

    with caplog.at_level("WARNING"):
        loaded = load_env_file(path, environ=env)

    assert loaded == 1
    assert "chmod 600" in caplog.text


def test_a_private_file_does_not_warn(tmp_path, caplog):
    env = {}
    path = _write(tmp_path, "OMI_API_KEY={}\n".format(VALID_KEY), mode=0o600)

    with caplog.at_level("WARNING"):
        load_env_file(path, environ=env)

    assert "chmod" not in caplog.text


def test_a_loaded_file_produces_a_usable_config(tmp_path):
    env = {}
    path = _write(tmp_path, "OMI_API_KEY={}\nMD_SINKS=markdown\nMD_LOOKBACK_HOURS=6\n".format(VALID_KEY))
    load_env_file(path, environ=env)

    config = Config.from_env(env)

    assert config.api_key == VALID_KEY
    assert config.lookback_hours == 6
