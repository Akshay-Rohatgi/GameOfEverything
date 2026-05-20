"""Phase 0.1 — Data model tests. No Docker, no LLM."""
import pytest
from pydantic import ValidationError

from goe.models.system import NetworkConfig, System
from goe.models.entity import AppSpec, Requirement, Entity
from goe.models.edge import EdgeType, ParamValue, Edge
from goe.models.procedure import (
    Procedure, Step, Session, SessionAuth,
    HttpRequestAction, ExecAttackerAction, ExecTargetAction,
    ListenAction, SleepAction, NavigateAction, ClickAction,
    FillAction, FillAndSubmitAction, EvaluateAction,
    WaitForAction, WaitCondition, UploadAction, ExtractAction,
    StatusAssertion, ExitCodeAssertion, StdoutContainsAssertion,
    BodyContainsAssertion, SelectorVisibleAssertion, UrlContainsAssertion,
    AllAssertion, ReceivedContainsAssertion,
)
from goe.models.artifacts import DBSetup, BuildArtifact
from goe.models.report import EntityStatus, EntityResult, ChainTestStatus, ChainTestResult, BuildReport


# ---------------------------------------------------------------------------
# system.py
# ---------------------------------------------------------------------------

class TestNetworkConfig:
    def test_basic(self): # Test if the NetworkConfig can be created with basic parameters
        nc = NetworkConfig(hostname="webserver", exposed_ports=[80, 443], internal_ports=[3306])
        assert nc.hostname == "webserver"
        assert nc.exposed_ports == [80, 443]

    def test_round_trip(self): # Test that serializing and deserializing gives the same object
        nc = NetworkConfig(hostname="db", exposed_ports=[], internal_ports=[5432])
        assert NetworkConfig.model_validate(nc.model_dump()) == nc 

    def test_missing_hostname(self): # Test that an instance without a hostname raises a validation error
        with pytest.raises(ValidationError):
            NetworkConfig(exposed_ports=[80], internal_ports=[])  # type: ignore


class TestSystem:
    def test_basic(self): # Test if the System can be created with basic parameters
        s = System(
            id="webserver",
            os="ubuntu_22.04",
            services=["nginx", "node"],
            network=NetworkConfig(hostname="webserver", exposed_ports=[80], internal_ports=[]),
        )
        assert s.id == "webserver"

    def test_round_trip(self): # Test that serializing and deserializing gives the same object
        s = System(
            id="db",
            os="ubuntu_22.04",
            services=["postgresql"],
            network=NetworkConfig(hostname="db", exposed_ports=[], internal_ports=[5432]),
        )
        assert System.model_validate(s.model_dump()) == s


# ---------------------------------------------------------------------------
# entity.py
# ---------------------------------------------------------------------------

class TestAppSpec:
    def test_basic(self): # Test if the AppSpec can be created with basic parameters``
        a = AppSpec(runtime="express", vulnerabilities=["sqli_union"], goal="credential_theft")
        assert a.runtime == "express"

    def test_round_trip(self): # Test that serializing and deserializing gives the same object
        a = AppSpec(runtime="flask", vulnerabilities=["xss_stored"], goal="session_theft")
        assert AppSpec.model_validate(a.model_dump()) == a


class TestRequirement:
    def test_required_edge(self): # Test that a Requirement is not optional by default
        r = Requirement(edge_id="op_to_app")
        assert r.optional is False

    def test_optional_edge(self):
        r = Requirement(edge_id="bonus_edge", optional=True)
        assert r.optional is True


class TestEntity:
    def test_minimal(self): # Test that an Entity can be created with minimal parameters
        e = Entity( # missing optional app_spec and atoms
            id="vuln_app",
            description="App with SQLi",
            system_id="webserver",
            requires=[Requirement(edge_id="op_to_app")],
            provides=["app_to_db_creds"],
        )
        assert e.app_spec is None
        assert e.atoms == []

    def test_with_app_spec(self): # Test that an Entity can be created with an AppSpec
        e = Entity(
            id="vuln_app",
            description="App with SQLi",
            system_id="webserver",
            requires=[Requirement(edge_id="op_to_app")],
            provides=["app_to_db_creds"],
            app_spec=AppSpec(runtime="express", vulnerabilities=["sqli_union"], goal="cred_theft"),
        )
        assert e.app_spec is not None

    def test_round_trip(self): # Test that serializing and deserializing gives the same object, including nested AppSpec and Requirement
        e = Entity(
            id="e1",
            description="desc",
            system_id="s1",
            requires=[Requirement(edge_id="r1")],
            provides=["p1"],
            atoms=["exposed_env_vars"],
        )
        assert Entity.model_validate(e.model_dump()) == e


# ---------------------------------------------------------------------------
# edge.py
# ---------------------------------------------------------------------------

class TestParamValue:
    def test_structural_only(self): # Test that a ParamValue can be created with only a structural value
        p = ParamValue(structural="webapp_port")
        assert p.concrete is None

    def test_both_phases(self): # Test that a ParamValue can be created with both structural and concrete values, and that the concrete value is accessible
        p = ParamValue(structural="webapp_port", concrete="3000")
        assert p.concrete == "3000"

    def test_round_trip(self): # Test that serializing and deserializing gives the same object, including both structural and concrete values
        p = ParamValue(structural="path", concrete="/app/.env")
        assert ParamValue.model_validate(p.model_dump()) == p


class TestEdge:
    def test_basic(self): # Test that an Edge can be created with basic parameters, including an EdgeType and ParamValues, and that the type and params are accessible
        e = Edge(
            id="op_to_app",
            from_entity="operator",
            to_entity="vuln_app",
            type=EdgeType.network_reach,
            params={
                "host": ParamValue(structural="webserver", concrete="webserver"),
                "port": ParamValue(structural="http_port", concrete="80"),
            },
        )
        assert e.type == EdgeType.network_reach
        assert e.params["port"].concrete == "80"

    def test_terminal_edge(self): # Test that an Edge can be created with to_entity=None to represent a terminal edge, and that to_entity is None
        e = Edge(
            id="final_shell",
            from_entity="privesc",
            to_entity=None,
            type=EdgeType.shell_as,
            params={"user": ParamValue(structural="root"), "host": ParamValue(structural="target")},
        )
        assert e.to_entity is None

    def test_all_edge_types(self): # Test that an Edge can be created for each EdgeType, and that the type is correctly set
        for et in EdgeType:
            e = Edge(
                id=f"e_{et.value}",
                from_entity="op",
                to_entity="ent",
                type=et,
                params={},
            )
            assert e.type == et

    def test_round_trip(self): # Test that serializing and deserializing gives the same object, including nested ParamValues
        e = Edge(
            id="creds",
            from_entity="a",
            to_entity="b",
            type=EdgeType.creds_for,
            params={"user": ParamValue(structural="dbadmin")},
        )
        assert Edge.model_validate(e.model_dump()) == e


# ---------------------------------------------------------------------------
# procedure.py — actions
# ---------------------------------------------------------------------------

class TestActions:
    def test_http_request(self):
        a = HttpRequestAction(type="http_request", method="POST", url="http://target/api")
        assert a.type == "http_request"
        assert a.body is None

    def test_http_request_with_body(self):
        a = HttpRequestAction(
            type="http_request", method="POST", url="http://t/p",
            headers={"Content-Type": "application/json"},
            body='{"x": 1}',
        )
        assert a.headers["Content-Type"] == "application/json"

    def test_exec_attacker(self):
        a = ExecAttackerAction(type="exec_attacker", command="whoami")
        assert a.command == "whoami"

    def test_exec_target(self):
        a = ExecTargetAction(type="exec_target", command="cat /etc/passwd")
        assert a.command == "cat /etc/passwd"

    def test_listen(self):
        a = ListenAction(type="listen", port=9999, duration=10)
        assert a.port == 9999

    def test_sleep(self):
        a = SleepAction(type="sleep", seconds=5)
        assert a.seconds == 5

    def test_navigate(self):
        a = NavigateAction(type="navigate", path="/login")
        assert a.path == "/login"

    def test_click(self):
        a = ClickAction(type="click", selector="button[type=submit]")
        assert a.selector == "button[type=submit]"

    def test_fill(self):
        a = FillAction(type="fill", fields={"#user": "admin"})
        assert a.fields["#user"] == "admin"

    def test_fill_and_submit(self):
        a = FillAndSubmitAction(type="fill_and_submit", fields={"#q": "test"})
        assert a.submit is None

    def test_evaluate(self):
        a = EvaluateAction(type="evaluate", script="return document.title")
        assert "document" in a.script

    def test_wait_for(self):
        a = WaitForAction(type="wait_for", condition=WaitCondition(type="selector", value=".ready"))
        assert a.condition.type == "selector"

    def test_upload(self):
        a = UploadAction(type="upload", selector="input[type=file]", filename="x.php", file_content="<?php")
        assert a.filename == "x.php"

    def test_extract(self):
        a = ExtractAction(type="extract", selector=".email", attribute="textContent")
        assert a.attribute == "textContent"


# ---------------------------------------------------------------------------
# procedure.py — assertions
# ---------------------------------------------------------------------------

class TestAssertions:
    def test_status(self):
        a = StatusAssertion(status=200)
        assert a.status == 200

    def test_exit_code(self):
        a = ExitCodeAssertion(exit_code=0)
        assert a.exit_code == 0

    def test_stdout_contains(self):
        a = StdoutContainsAssertion(stdout_contains="hello")
        assert a.stdout_contains == "hello"

    def test_body_contains(self):
        a = BodyContainsAssertion(body_contains="<html")
        assert a.body_contains == "<html"

    def test_selector_visible(self):
        a = SelectorVisibleAssertion(selector_visible=".dashboard")
        assert a.selector_visible == ".dashboard"

    def test_url_contains(self):
        a = UrlContainsAssertion(url_contains="/dashboard")
        assert a.url_contains == "/dashboard"

    def test_received_contains(self):
        a = ReceivedContainsAssertion(received_contains="session_id=")
        assert a.received_contains == "session_id="

    def test_all_assertion(self):
        a = AllAssertion(all=[StatusAssertion(status=200), BodyContainsAssertion(body_contains="ok")])
        assert len(a.all) == 2

    def test_all_assertion_nested(self):
        inner = AllAssertion(all=[StatusAssertion(status=201)])
        outer = AllAssertion(all=[inner, UrlContainsAssertion(url_contains="/post/")])
        assert len(outer.all) == 2


# ---------------------------------------------------------------------------
# procedure.py — Session + Step + Procedure
# ---------------------------------------------------------------------------

class TestSession:
    def test_minimal(self):
        s = Session(id="attacker_browser", base_url="http://target:3000")
        assert s.type == "browser"
        assert s.auth is None

    def test_with_auth(self):
        s = Session(
            id="admin_browser",
            base_url="http://target:3000",
            auth=SessionAuth(
                login_url="/login",
                username_field="#user",
                password_field="#pass",
                username="admin",
                password="admin123",
                success_indicator="/dashboard",
            ),
        )
        assert s.auth is not None
        assert s.auth.username == "admin"


class TestStep:
    def test_exec_step(self):
        step = Step(
            step_id="check_whoami",
            action=ExecAttackerAction(type="exec_attacker", command="whoami"),
            expect=StdoutContainsAssertion(stdout_contains="root"),
        )
        assert step.timeout == 10
        assert step.outputs == {}

    def test_browser_step(self):
        step = Step(
            step_id="go_login",
            session="attacker_browser",
            action=NavigateAction(type="navigate", path="/login"),
            expect=SelectorVisibleAssertion(selector_visible="form"),
        )
        assert step.session == "attacker_browser"

    def test_no_assertion(self):
        step = Step(
            step_id="start_listener",
            action=ExecAttackerAction(type="exec_attacker", command="ncat -lk 9999 &"),
        )
        assert step.expect is None

    def test_with_outputs(self):
        step = Step(
            step_id="capture",
            action=ExecAttackerAction(type="exec_attacker", command="cat /tmp/stolen.txt"),
            expect=StdoutContainsAssertion(stdout_contains="session_id="),
            outputs={"stolen_cookie": 'regex("session_id=([^&\\s]+)")'},
        )
        assert "stolen_cookie" in step.outputs


class TestProcedure:
    def test_minimal(self):
        p = Procedure(procedure=[
            Step(
                step_id="check",
                action=ExecAttackerAction(type="exec_attacker", command="whoami"),
            )
        ])
        assert p.sessions == []
        assert len(p.procedure) == 1

    def test_with_sessions(self):
        p = Procedure(
            sessions=[Session(id="s1", base_url="http://target:3000")],
            procedure=[
                Step(
                    step_id="nav",
                    session="s1",
                    action=NavigateAction(type="navigate", path="/"),
                )
            ],
        )
        assert len(p.sessions) == 1

    def test_round_trip(self):
        p = Procedure(procedure=[
            Step(
                step_id="s1",
                action=HttpRequestAction(type="http_request", method="GET", url="http://t/"),
                expect=StatusAssertion(status=200),
            )
        ])
        p2 = Procedure.model_validate(p.model_dump())
        assert len(p2.procedure) == 1
        assert p2.procedure[0].step_id == "s1"


# ---------------------------------------------------------------------------
# artifacts.py
# ---------------------------------------------------------------------------

class TestBuildArtifact:
    def test_minimal(self):
        a = BuildArtifact(
            source_files={"app.js": "const express = require('express');"},
            primary_source="app.js",
            port=3000,
        )
        assert a.db_setup is None
        assert a.extra_deps == []

    def test_with_db(self):
        a = BuildArtifact(
            source_files={"app.py": "from flask import Flask"},
            primary_source="app.py",
            port=5000,
            db_setup=DBSetup(
                db_type="mysql",
                schema_sql="CREATE TABLE users (id INT);",
                seed_sql="INSERT INTO users VALUES (1);",
            ),
        )
        assert a.db_setup is not None
        assert a.db_setup.db_type == "mysql"

    def test_round_trip(self):
        a = BuildArtifact(
            source_files={"app.js": ""},
            primary_source="app.js",
            port=3000,
            extra_deps=["bcrypt"],
        )
        assert BuildArtifact.model_validate(a.model_dump()) == a


# ---------------------------------------------------------------------------
# report.py
# ---------------------------------------------------------------------------

class TestBuildReport:
    def test_all_passed(self):
        r = BuildReport(
            entities=[
                EntityResult(id="e1", status=EntityStatus.PASSED),
                EntityResult(id="e2", status=EntityStatus.PASSED, attempts=3),
            ]
        )
        assert r.chain_test is None
        assert all(e.status == EntityStatus.PASSED for e in r.entities)

    def test_with_failures(self):
        r = BuildReport(
            entities=[
                EntityResult(id="e1", status=EntityStatus.FAILED, attempts=5, failure_reason="design flaw"),
                EntityResult(id="e2", status=EntityStatus.SKIPPED, skip_reason="depends on e1"),
            ],
            chain_test=ChainTestResult(status=ChainTestStatus.SKIPPED, reason="incomplete graph"),
        )
        assert r.chain_test is not None
        assert r.chain_test.status == ChainTestStatus.SKIPPED

    def test_round_trip(self):
        r = BuildReport(
            entities=[EntityResult(id="e1", status=EntityStatus.PASSED)],
            chain_test=ChainTestResult(status=ChainTestStatus.PASSED),
        )
        assert BuildReport.model_validate(r.model_dump()) == r
