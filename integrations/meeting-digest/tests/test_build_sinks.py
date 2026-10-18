import pytest

from meeting_digest.config import Config, ConfigError
from meeting_digest.sinks import NotionSalesSink, NotionTasksSink, build_sinks

VALID_KEY = "omi_dev_" + "a" * 32
VALID_DB_ID = "a" * 32


def _config(**overrides) -> Config:
    defaults = dict(api_key=VALID_KEY, sinks=["notion_tasks"])
    defaults.update(overrides)
    return Config(**defaults)


# --- notion_tasks ------------------------------------------------------------


def test_notion_tasks_requires_a_token():
    with pytest.raises(ConfigError) as exc:
        build_sinks(_config(notion_tasks_database_id=VALID_DB_ID))

    assert "notion_tasks" in str(exc.value)


def test_notion_tasks_requires_a_database_id():
    with pytest.raises(ConfigError) as exc:
        build_sinks(_config(notion_tasks_token="ntn_tasks"))

    assert "MD_NOTION_TASKS_DATABASE_ID" in str(exc.value)


def test_notion_tasks_falls_back_to_the_main_notion_token():
    sinks = build_sinks(_config(notion_token="ntn_main", notion_tasks_database_id=VALID_DB_ID))

    assert isinstance(sinks[0], NotionTasksSink)


def test_notion_tasks_prefers_its_own_token_over_the_main_one():
    sinks = build_sinks(
        _config(notion_token="ntn_main", notion_tasks_token="ntn_tasks", notion_tasks_database_id=VALID_DB_ID)
    )

    assert sinks[0]._headers["Authorization"] == "Bearer ntn_tasks"


# --- notion_sales ------------------------------------------------------------


def test_notion_sales_requires_a_token():
    with pytest.raises(ConfigError) as exc:
        build_sinks(_config(sinks=["notion_sales"], notion_sales_database_id=VALID_DB_ID))

    assert "notion_sales" in str(exc.value)


def test_notion_sales_requires_a_database_id():
    with pytest.raises(ConfigError) as exc:
        build_sinks(_config(sinks=["notion_sales"], notion_sales_token="ntn_sales"))

    assert "MD_NOTION_SALES_DATABASE_ID" in str(exc.value)


def test_notion_sales_builds_with_its_own_token():
    sinks = build_sinks(
        _config(sinks=["notion_sales"], notion_sales_token="ntn_sales", notion_sales_database_id=VALID_DB_ID)
    )

    assert isinstance(sinks[0], NotionSalesSink)


# --- unknown sink -------------------------------------------------------------


def test_an_unknown_sink_name_lists_the_known_ones():
    with pytest.raises(ConfigError) as exc:
        build_sinks(_config(sinks=["bogus"]))

    assert "notion_tasks" in str(exc.value)
    assert "notion_sales" in str(exc.value)
