from model_router.classify import Classification, classify
from tests.helpers import default_config

CONFIG = default_config()


def run(description="", prompt="", subagent_type="general-purpose"):
    return classify(
        {"description": description, "prompt": prompt, "subagent_type": subagent_type},
        CONFIG,
    )


def test_agent_type_wins_over_keywords():
    assert run("Review the auth module", subagent_type="Explore") == Classification(
        "explore", "agent_type", None
    )


def test_keyword_in_description():
    assert run("Review the auth module") == Classification("review", "keyword", None)


def test_keyword_in_prompt_when_description_has_none():
    assert run("Auth module", "Find where tokens are stored").category == "explore"


def test_description_is_checked_before_prompt():
    assert run("Implement the cache", "then review it").category == "implement"


def test_categories_are_tested_in_a_fixed_order():
    assert run("debug and review the handler").category == "review"


def test_keywords_match_whole_words_only():
    assert run("Prefix the address list").category == "default"


def test_phrase_keywords_match():
    assert run("Tell me the root cause").category == "debug"


def test_prompt_is_scanned_for_500_characters_only():
    assert run("", ("x " * 260) + "review").category == "default"


def test_tag_sets_override_and_keeps_the_category():
    result = run("Cache work", "[route:opus] Implement the cache")
    assert result == Classification("implement", "tag", "opus")


def test_keep_tag():
    assert run("Cache work", "Implement it [route:keep]").override == "keep"


def test_non_string_fields_do_not_crash():
    result = classify({"description": 5, "prompt": None, "subagent_type": ["x"]}, CONFIG)
    assert result == Classification("default", "default", None)


def test_missing_fields_default():
    assert classify({}, CONFIG) == Classification("default", "default", None)
