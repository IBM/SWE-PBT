from typing import Any, cast
from unittest.mock import Mock, patch

from tddbench.harness.constants import SWEbenchInstance
from tddbench.harness.docker_build import build_env_images, get_env_configs_to_build
from tddbench.harness.test_spec import make_test_spec


def make_instance(**overrides: Any) -> SWEbenchInstance:
    instance = {
        "instance_id": "pgmpy__pgmpy-3137",
        "repo": "pgmpy/pgmpy",
        "version": "unknown",
        "base_commit": "da98466c0a79cb416562e450d85699aedc10f422",
        "problem_statement": "test",
        "hints_text": "",
        "test_patch": "",
        "install_config": {
            "python": "3.13",
            "install": "pip install -e .",
            "test_cmd": "pytest -xvs",
            "packages": "",
            "pip_packages": [],
            "pre_install": [],
        },
    }
    instance.update(overrides)
    return cast(SWEbenchInstance, instance)


def test_make_test_spec_prefers_benchmark_image():
    benchmark_image = "swerebench/sweb.eval.x86_64.pgmpy_1776_pgmpy-3137:latest"
    instance = make_instance(docker_image=benchmark_image)

    spec = make_test_spec(instance)

    assert spec.benchmark_image == benchmark_image
    assert spec.env_image_key == benchmark_image


def test_make_test_spec_falls_back_to_image_name():
    benchmark_image = "swerebench/sweb.eval.x86_64.pgmpy_1776_pgmpy-3137:latest"
    instance = make_instance(image_name=benchmark_image)

    spec = make_test_spec(instance)

    assert spec.benchmark_image == benchmark_image
    assert spec.env_image_key == benchmark_image


def test_get_env_configs_to_build_marks_benchmark_image_for_pull_only():
    spec = make_test_spec(
        make_instance(
            docker_image="swerebench/sweb.eval.x86_64.pgmpy_1776_pgmpy-3137:latest"
        )
    )
    client = Mock()
    client.images.get.side_effect = Exception("missing")


    class _ImageNotFound(Exception):
        pass


    with patch("tddbench.harness.docker_build.ImageNotFound", _ImageNotFound):
        client.images.get.side_effect = _ImageNotFound("missing")
        configs = get_env_configs_to_build(client, [spec])

    assert spec.env_image_key in configs
    assert configs[spec.env_image_key]["pull_only"] is True
    assert configs[spec.env_image_key]["platform"] == spec.platform


def test_build_env_images_pulls_benchmark_image_without_building_base():
    spec = make_test_spec(
        make_instance(
            docker_image="swerebench/sweb.eval.x86_64.pgmpy_1776_pgmpy-3137:latest"
        )
    )
    client = Mock()
    client.images.get.side_effect = [Exception("missing")]

    pulled = []

    def fake_pull(image_name, platform=None):
        pulled.append((image_name, platform))
        return {"image": image_name, "platform": platform}


    client.images.pull.side_effect = fake_pull

    class _ImageNotFound(Exception):
        pass


    with patch("tddbench.harness.docker_build.ImageNotFound", _ImageNotFound), \
         patch("tddbench.harness.docker_build.build_base_images") as mock_build_base:
        client.images.get.side_effect = _ImageNotFound("missing")
        successful, failed = build_env_images(client, [spec], force_rebuild=False, max_workers=1)

    assert failed == []
    assert successful == [spec.env_image_key]
    assert pulled == [(spec.env_image_key, spec.platform)]
    mock_build_base.assert_not_called()

# Made with Bob
