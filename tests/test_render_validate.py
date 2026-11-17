"""The strict check every live render passes before it replaces the answer."""

from lesmots.lexrender.render import validate

ANSWER = ("## Setup\n\n- Run `pip install lesmots`.\n- See https://example.com/docs for more.\n\n"
          "If the cache is down, the service uses the database instead.")


def test_a_light_rewrite_passes():
    reply = ANSWER.replace("uses the database instead",
                           "will {{fall-back-to|fall back to}} the database")
    assert validate(ANSWER, reply) is None


def test_changed_code_fails():
    assert validate(ANSWER, ANSWER.replace("pip install lesmots", "pip install lesmot"))


def test_a_marker_inside_code_fails():
    reply = ANSWER.replace("`pip install lesmots`", "`pip {{install-x|install}} lesmots`")
    assert validate(ANSWER, reply) == "the model changed code or a URL"


def test_changed_url_fails():
    assert validate(ANSWER, ANSWER.replace("example.com/docs", "example.com/doc"))


def test_lost_structure_fails():
    reply = ANSWER.replace("## Setup\n\n", "Setup: ")
    assert validate(ANSWER, reply) == "the model changed the Markdown structure"


def test_much_longer_reply_fails():
    assert validate(ANSWER, ANSWER + "\n\n" + "Extra commentary. " * 30)


def test_truncated_reply_fails():
    assert validate(ANSWER, ANSWER[:40])
