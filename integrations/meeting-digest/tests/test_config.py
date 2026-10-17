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


def test_sales_clip_phrases_default_when_unset():
    from meeting_digest.curate import DEFAULT_SALES_CLIP_PHRASES

    config = Config.from_env({"OMI_API_KEY": VALID_KEY})

    assert config.sales_clip_phrases == list(DEFAULT_SALES_CLIP_PHRASES)


def test_sales_clip_phrases_are_configurable():
    config = Config.from_env({"OMI_API_KEY": VALID_KEY, "MD_SALES_CLIP_PHRASES": "顧客の声, 商談ログ"})

    assert config.sales_clip_phrases == ["顧客の声", "商談ログ"]


def test_notion_tasks_database_id_accepts_a_pasted_url():
    config = Config.from_env(
        {
            "OMI_API_KEY": VALID_KEY,
            "MD_NOTION_TASKS_DATABASE_ID": "https://www.notion.so/ws/" + "a" * 32 + "?v=1",
        }
    )

    assert config.notion_tasks_database_id == "a" * 32


def test_an_invalid_notion_tasks_database_id_names_its_own_variable():
    with pytest.raises(ConfigError) as exc:
        Config.from_env({"OMI_API_KEY": VALID_KEY, "MD_NOTION_TASKS_DATABASE_ID": "not-an-id"})

    assert "MD_NOTION_TASKS_DATABASE_ID" in str(exc.value)


def test_notion_tasks_token_falls_back_to_the_main_token_only_at_sink_build_time():
    # Config itself keeps them distinct; build_sinks (tested separately) is
    # where the fallback to MD_NOTION_TOKEN happens.
    config = Config.from_env({"OMI_API_KEY": VALID_KEY, "MD_NOTION_TOKEN": "ntn_main"})

    assert config.notion_tasks_token is None


def test_slack_tasks_board_url_defaults_to_none():
    config = Config.from_env({"OMI_API_KEY": VALID_KEY})

    assert config.slack_tasks_board_url is None
