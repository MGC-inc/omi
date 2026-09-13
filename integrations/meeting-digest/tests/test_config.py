import pytest

from meeting_digest.config import Config, ConfigError

VALID_KEY = "omi_dev_" + "a" * 32


def test_requires_an_api_key():
    with pytest.raises(ConfigError) as exc:
        Config.from_env({})

    assert "OMI_API_KEY" in str(exc.value)


def test_rejects_an_mcp_key():
    # MCP keys do not authenticate the Developer API; failing here beats a 401
    # after the first request.
    with pytest.raises(ConfigError) as exc:
        Config.from_env({"OMI_API_KEY": "omi_mcp_" + "a" * 32})

    assert "omi_dev_" in str(exc.value)


def test_redacted_config_never_exposes_the_key():
    config = Config.from_env({"OMI_API_KEY": VALID_KEY})
    redacted = config.redacted()

    assert VALID_KEY not in str(redacted)
    assert redacted["api_key"] == "omi_dev_aaaa..."


def test_defaults_are_conservative():
    config = Config.from_env({"OMI_API_KEY": VALID_KEY})

    assert config.api_base == "https://api.omi.me"
    assert config.sinks == ["markdown"]
    assert config.slack_webhook_url is None
    # Below the 25/hour transcript-read budget, leaving headroom for retries.
    assert config.max_transcript_fetches < 25


def test_trailing_slash_on_api_base_is_normalized():
    config = Config.from_env({"OMI_API_KEY": VALID_KEY, "OMI_API_BASE": "https://example.test/"})

    assert config.api_base == "https://example.test"


def test_rejects_a_non_numeric_lookback():
    with pytest.raises(ConfigError):
        Config.from_env({"OMI_API_KEY": VALID_KEY, "MD_LOOKBACK_HOURS": "yesterday"})


def test_rejects_an_out_of_range_page_size():
    with pytest.raises(ConfigError):
        Config.from_env({"OMI_API_KEY": VALID_KEY, "MD_LIST_PAGE_SIZE": "500"})


def test_empty_sink_list_is_rejected():
    with pytest.raises(ConfigError):
        Config.from_env({"OMI_API_KEY": VALID_KEY, "MD_SINKS": " , "})
