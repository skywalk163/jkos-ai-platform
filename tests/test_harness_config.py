"""M16 智能中枢 - HarnessConfig 配置解析测试

覆盖：默认关闭、环境变量解析、密钥脱敏（日志/接口绝不回显 api_key）。
"""
import logging
import os

from jkos_core.harness.config import HarnessConfig


class TestDefaults:
    def test_disabled_by_default(self, monkeypatch):
        """未配置任何环境变量时默认关闭 —— 保证既有功能零回归"""
        for name in ("JKOS_HARNESS_ENABLED", "DSH_HOME", "JKOS_HARNESS_DSH_BIN",
                     "JKOS_HARNESS_WORKSPACE", "DEEPSEEK_API_KEY"):
            monkeypatch.delenv(name, raising=False)

        config = HarnessConfig.from_env()

        assert config.enabled is False
        assert config.dsh_home == "/data/dsh/harness-home"
        assert config.dsh_bin is None  # None => SDK 自带 bundled runtime
        assert config.workspace == os.path.join("/data/dsh/harness-home", "workspace")
        assert config.provider == "deepseek-official"
        assert config.sdk_source == "pypi"

    def test_enabled_via_env(self, monkeypatch):
        monkeypatch.setenv("JKOS_HARNESS_ENABLED", "true")

        assert HarnessConfig.from_env().enabled is True

    def test_invalid_bool_falls_back_with_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("JKOS_HARNESS_ENABLED", "maybe")
        caplog.set_level(logging.WARNING, logger="dsh.harness")

        assert HarnessConfig.from_env().enabled is False
        assert "值非法" in caplog.text


class TestEnvParsing:
    def test_freebsd_dsh_bin_and_workspace(self, monkeypatch, tmp_path):
        """FreeBSD 路径：显式 dsh_bin 指向源码构建 launcher"""
        monkeypatch.setenv("JKOS_HARNESS_ENABLED", "true")
        monkeypatch.setenv("DSH_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("JKOS_HARNESS_DSH_BIN", "/data/dsh/harness/dsh-jkos.sh")

        config = HarnessConfig.from_env()

        assert config.dsh_bin == "/data/dsh/harness/dsh-jkos.sh"
        # 未显式给 workspace 时派生自 dsh_home
        assert config.workspace == str(tmp_path / "home" / "workspace")

    def test_explicit_workspace_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JKOS_HARNESS_WORKSPACE", str(tmp_path / "custom"))

        assert HarnessConfig.from_env().workspace == str(tmp_path / "custom")

    def test_sdk_source_normalized(self, monkeypatch):
        monkeypatch.setenv("JKOS_HARNESS_SDK_SOURCE", "  LOCAL ")

        assert HarnessConfig.from_env().sdk_source == "local"

    def test_timeouts(self, monkeypatch):
        monkeypatch.setenv("JKOS_HARNESS_INIT_TIMEOUT", "45")
        monkeypatch.setenv("JKOS_HARNESS_REQUEST_TIMEOUT", "120.5")

        config = HarnessConfig.from_env()

        assert config.initialize_timeout == 45.0
        assert config.request_timeout == 120.5

    def test_invalid_timeout_falls_back(self, monkeypatch, caplog):
        monkeypatch.setenv("JKOS_HARNESS_INIT_TIMEOUT", "abc")
        caplog.set_level(logging.WARNING, logger="dsh.harness")

        assert HarnessConfig.from_env().initialize_timeout == 30.0
        assert "不是数字" in caplog.text


class TestSecretRedaction:
    def test_redacted_hides_api_key(self):
        """redacted() 用于对外返回，必须遮蔽密钥"""
        config = HarnessConfig(api_key="sk-secret-value")

        data = config.redacted()

        assert data["api_key"] == "***"
        assert "sk-secret-value" not in str(data)

    def test_redacted_keeps_empty_key_empty(self):
        assert HarnessConfig(api_key="").redacted()["api_key"] == ""

    def test_redacted_hides_ui_token(self):
        """Web UI 令牌同属敏感信息"""
        config = HarnessConfig(ui_token="tok-secret")

        data = config.redacted()

        assert data["ui_token"] == "***"
        assert "tok-secret" not in str(data)

    def test_describe_has_no_secret(self):
        """describe() 用于日志，必须不含密钥"""
        config = HarnessConfig(api_key="sk-secret-value", dsh_bin="/opt/dsh")

        text = config.describe()

        assert "sk-secret-value" not in text
        assert "runtime=/opt/dsh" in text

    def test_describe_marks_bundled_runtime(self):
        assert "runtime=bundled" in HarnessConfig().describe()