import logging

import pytest


@pytest.fixture(autouse=True)
def fresh_bot_logger():
    """A test that sets up logging must not leave handlers on files or streams that are gone."""
    yield
    logger = logging.getLogger("bot")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
