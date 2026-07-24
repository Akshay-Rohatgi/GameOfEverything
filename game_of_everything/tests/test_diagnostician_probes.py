"""Tests for the adaptive diagnostician probe system."""

import pytest

from goe.retry.probes import (
    Probe,
    ProbeContext,
    _atom_mentions,
    execute_probes,
    select_probes,
)


class TestAtomMentions:
    def test_exact_match(self):
        assert _atom_mentions(["sqli_union", "xss_stored"], {"sqli"})

    def test_partial_match(self):
        assert _atom_mentions(["weak_service_password"], {"service"})

    def test_no_match(self):
        assert not _atom_mentions(["sqli_union"], {"suid"})

    def test_case_insensitive(self):
        assert _atom_mentions(["SSH_Weak_Creds"], {"ssh"})

    def test_multiple_atoms(self):
        assert _atom_mentions(["foo", "suid_vim", "bar"], {"suid"})


class TestSelectProbesExpress:
    def test_express_with_sqli_and_db(self):
        ctx = ProbeContext(
            runtime="express",
            atoms=["sqli_union"],
            has_db=True,
            db_type="sqlite3",
            app_dir="/opt/webapp",
            port=3000,
            source_files=["app.js", "package.json"],
            failed_step="sqli_test",
            system_deps=[],
        )

        probes = select_probes(ctx)

        # Should include universal probes
        assert any(p.id == "processes" for p in probes)
        assert any(p.id == "listening_ports" for p in probes)

        # Should include web runtime probes
        assert any(p.id == "webapp_log" for p in probes)
        assert any(p.id == "app_healthcheck" for p in probes)

        # Should include database probes
        assert any(p.id == "sqlite_tables" for p in probes)

        # Should NOT include ubuntu probes
        assert not any(p.id == "users_and_groups" for p in probes)

    def test_express_with_xss_admin_bot(self):
        ctx = ProbeContext(
            runtime="express",
            atoms=["xss_admin_bot"],
            has_db=False,
            db_type=None,
            app_dir="/opt/webapp",
            port=3000,
            source_files=["app.js"],
            failed_step=None,
            system_deps=[],
        )

        probes = select_probes(ctx)

        # Should include adminbot_log because of xss + bot keywords
        assert any(p.id == "adminbot_log" for p in probes)


class TestSelectProbesUbuntu:
    def test_ubuntu_with_ssh_atom(self):
        ctx = ProbeContext(
            runtime="ubuntu",
            atoms=["weak_service_password"],
            has_db=False,
            db_type=None,
            app_dir="",
            port=None,
            source_files=[],
            failed_step=None,
            system_deps=["openssh-server"],
        )

        probes = select_probes(ctx)

        # Should include universal probes
        assert any(p.id == "processes" for p in probes)

        # Should include ubuntu probes
        assert any(p.id == "users_and_groups" for p in probes)

        # Should include sshd_config (openssh in system_deps)
        assert any(p.id == "sshd_config" for p in probes)

        # Should NOT include webapp probes
        assert not any(p.id == "webapp_log" for p in probes)
        assert not any(p.id == "app_healthcheck" for p in probes)

    def test_ubuntu_with_suid_atom(self):
        ctx = ProbeContext(
            runtime="ubuntu",
            atoms=["set_suid_vim"],
            has_db=False,
            db_type=None,
            app_dir="",
            port=None,
            source_files=[],
            failed_step=None,
            system_deps=[],
        )

        probes = select_probes(ctx)

        # Should include suid_binaries probe
        assert any(p.id == "suid_binaries" for p in probes)

    def test_ubuntu_with_capability_atom(self):
        ctx = ProbeContext(
            runtime="ubuntu",
            atoms=["cap_dac_override_vim"],
            has_db=False,
            db_type=None,
            app_dir="",
            port=None,
            source_files=[],
            failed_step=None,
            system_deps=[],
        )

        probes = select_probes(ctx)

        # Should include capabilities probe
        assert any(p.id == "capabilities" for p in probes)


class TestSelectProbesFlask:
    def test_flask_with_mysql(self):
        ctx = ProbeContext(
            runtime="flask",
            atoms=["sqli_blind"],
            has_db=True,
            db_type="mysql",
            app_dir="/opt/webapp",
            port=5000,
            source_files=["app.py", "requirements.txt"],
            failed_step=None,
            system_deps=[],
        )

        probes = select_probes(ctx)

        # Should include mysql_status instead of sqlite probes
        assert any(p.id == "mysql_status" for p in probes)
        assert not any(p.id == "sqlite_tables" for p in probes)


class TestSelectProbesApache:
    def test_apache_php(self):
        ctx = ProbeContext(
            runtime="apache_php",
            atoms=["lfi"],
            has_db=False,
            db_type=None,
            app_dir="/var/www/html",
            port=80,
            source_files=["index.php"],
            failed_step=None,
            system_deps=[],
        )

        probes = select_probes(ctx)

        # Should include apache_error_log
        assert any(p.id == "apache_error_log" for p in probes)


class TestSelectProbesMaxBudget:
    def test_respects_max_10_probes(self):
        # Create a context that would match many probes
        ctx = ProbeContext(
            runtime="ubuntu",
            atoms=[
                "suid_vim",
                "cap_dac_override",
                "sudo_no_passwd",
                "samba_insecure",
                "cron_job_hijack",
                "writable_systemd_service",
            ],
            has_db=False,
            db_type=None,
            app_dir="",
            port=None,
            source_files=[],
            failed_step=None,
            system_deps=["openssh-server"],
        )

        probes = select_probes(ctx)

        # Should be capped at 10
        assert len(probes) <= 10

        # Should prioritize by priority value (lower = more important)
        # Universal probes have priority 1, so they should be included
        assert any(p.id == "processes" for p in probes)
        assert any(p.id == "listening_ports" for p in probes)


class TestExecuteProbes:
    def test_execute_with_mock_env(self):
        """Test probe execution with a mock environment."""
        from unittest.mock import MagicMock

        env = MagicMock()
        env.exec_in.return_value = (0, "test output", "")

        ctx = ProbeContext(
            runtime="express",
            atoms=[],
            has_db=False,
            db_type=None,
            app_dir="/opt/webapp",
            port=3000,
            source_files=[],
            failed_step=None,
            system_deps=[],
        )

        probes = [
            Probe(
                id="test_probe",
                label="Test Probe",
                command="echo test",
                relevance=lambda ctx: True,
                priority=1,
            )
        ]

        results = execute_probes(env, probes, ctx)

        # Should return list of (label, output) tuples
        assert len(results) == 1
        assert results[0][0] == "Test Probe"
        assert results[0][1] == "test output"

        # Should have called exec_in
        env.exec_in.assert_called_once()

    def test_execute_with_template_substitution(self):
        """Test that {app_dir} and {port} are substituted."""
        from unittest.mock import MagicMock

        env = MagicMock()
        env.exec_in.return_value = (0, "ok", "")

        ctx = ProbeContext(
            runtime="express",
            atoms=[],
            has_db=False,
            db_type=None,
            app_dir="/custom/path",
            port=8080,
            source_files=[],
            failed_step=None,
            system_deps=[],
        )

        probes = [
            Probe(
                id="test",
                label="Test",
                command="curl http://localhost:{port}/ && ls {app_dir}/",
                relevance=lambda ctx: True,
            )
        ]

        execute_probes(env, probes, ctx)

        # Check the actual command that was executed
        call_args = env.exec_in.call_args[0]
        actual_command = call_args[1]
        assert "8080" in actual_command
        assert "/custom/path" in actual_command

    def test_execute_truncates_long_output(self):
        """Test that output is truncated to max_output."""
        from unittest.mock import MagicMock

        env = MagicMock()
        long_output = "x" * 2000
        env.exec_in.return_value = (0, long_output, "")

        ctx = ProbeContext(
            runtime="ubuntu",
            atoms=[],
            has_db=False,
            db_type=None,
            app_dir="",
            port=None,
            source_files=[],
            failed_step=None,
            system_deps=[],
        )

        probes = [
            Probe(
                id="test",
                label="Test",
                command="echo test",
                relevance=lambda ctx: True,
                max_output=100,
            )
        ]

        results = execute_probes(env, probes, ctx)

        # Should be truncated
        assert len(results[0][1]) <= 117  # 100 + "\n... (truncated)"
        assert "truncated" in results[0][1]
