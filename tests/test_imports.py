import importlib

import pytest


@pytest.mark.parametrize("module_name", ["core.notary", "utils.validator"])
def test_all_runtime_modules_are_importable(module_name):
    assert importlib.import_module(module_name)
