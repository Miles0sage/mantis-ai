"""Tests for mantis.core.planner — standalone task planner."""

import pytest

from mantis.core.planner import (
    ARCHITECTURE_KEYWORDS,
    TASK_PATTERNS,
    PlannedTask,
    ExecutionPlan,
    _extract_postconditions,
    classify_task,
    _extract_file_targets,
    _split_atomic_chunks,
    _infer_complexity,
    build_execution_plan,
)


# ---------------------------------------------------------------------------
# classify_task
# ---------------------------------------------------------------------------

class TestClassifyTask:
    def test_test_writing(self):
        assert classify_task("write pytest tests for the auth module") == "test_writing"

    def test_test_mentions_without_writing_do_not_become_test_writing(self):
        assert classify_task("reply only with the number of test functions in the file") != "test_writing"

    def test_refactor(self):
        assert classify_task("refactor the database layer") == "refactor"

    def test_bug_fix(self):
        assert classify_task("fix the crash in login") == "bug_fix"

    def test_feature(self):
        assert classify_task("implement a new caching layer") == "feature"

    def test_docs(self):
        assert classify_task("document the API endpoints with docstrings") == "docs"

    def test_devops(self):
        assert classify_task("deploy using docker and kubernetes") == "devops"

    def test_unknown_prompt(self):
        assert classify_task("xyzzy foobar baz") == "unknown"

    def test_highest_count_wins(self):
        # Bare mentions of "test" should not outweigh an explicit fix verb.
        result = classify_task("test the test and fix it")
        assert result == "bug_fix"

    def test_research(self):
        assert classify_task("research and compare database options") == "research"

    def test_data(self):
        assert classify_task("write a sql migration for the schema") == "data"


# ---------------------------------------------------------------------------
# _extract_file_targets
# ---------------------------------------------------------------------------

class TestExtractFileTargets:
    def test_python_files(self):
        targets = _extract_file_targets("edit src/main.py and tests/test_main.py")
        assert targets == ["src/main.py", "tests/test_main.py"]

    def test_js_ts_files(self):
        targets = _extract_file_targets("update index.js and utils.ts and App.tsx")
        assert targets == ["index.js", "utils.ts", "App.tsx"]

    def test_config_files(self):
        targets = _extract_file_targets("modify config.json and setup.yaml")
        assert targets == ["config.json", "setup.yaml"]

    def test_no_duplicates(self):
        targets = _extract_file_targets("read foo.py then edit foo.py again")
        assert targets == ["foo.py"]

    def test_no_targets(self):
        targets = _extract_file_targets("do something cool")
        assert targets == []

    def test_markdown_and_sql(self):
        targets = _extract_file_targets("update README.md and create init.sql")
        assert targets == ["README.md", "init.sql"]


# ---------------------------------------------------------------------------
# _split_atomic_chunks
# ---------------------------------------------------------------------------

class TestSplitAtomicChunks:
    def test_single_prompt(self):
        chunks = _split_atomic_chunks("write tests")
        assert len(chunks) == 1
        assert chunks[0] == ("write tests", False)

    def test_and_then_sequential(self):
        chunks = _split_atomic_chunks("write code and then run tests")
        assert len(chunks) == 2
        assert chunks[0][1] is False  # first chunk not dependent
        assert chunks[1][1] is True   # second depends on first

    def test_then_sequential(self):
        chunks = _split_atomic_chunks("lint the code then deploy")
        assert len(chunks) == 2
        assert chunks[1][1] is True

    def test_and_parallel(self):
        chunks = _split_atomic_chunks("write tests and write docs")
        assert len(chunks) == 2
        assert chunks[0][1] is False
        assert chunks[1][1] is False

    def test_mixed_connectors(self):
        chunks = _split_atomic_chunks("write code and write tests and then deploy")
        assert len(chunks) == 3
        assert chunks[0][1] is False
        assert chunks[1][1] is False
        assert chunks[2][1] is True

    def test_reply_clause_stays_in_same_chunk(self):
        chunks = _split_atomic_chunks(
            "read tests/test_server_background.py and reply only with the number of test functions in the file"
        )
        assert len(chunks) == 1


# ---------------------------------------------------------------------------
# _infer_complexity
# ---------------------------------------------------------------------------

class TestInferComplexity:
    def test_low_complexity(self):
        assert _infer_complexity("edit a file", [], [("edit a file", False)]) == "low"

    def test_medium_by_files(self):
        assert _infer_complexity("edit", ["a.py", "b.py"], [("edit", False)]) == "medium"

    def test_high_by_many_files(self):
        assert _infer_complexity("edit", ["a.py", "b.py", "c.py"], [("x", False)]) == "high"

    def test_high_by_many_chunks(self):
        chunks = [("a", False), ("b", False), ("c", False)]
        assert _infer_complexity("do stuff", [], chunks) == "high"

    def test_high_by_architecture_keyword(self):
        assert _infer_complexity("refactor the architecture", [], [("x", False)]) == "high"


# ---------------------------------------------------------------------------
# PlannedTask / ExecutionPlan dataclasses
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_planned_task_to_dict(self):
        task = PlannedTask(title="t", prompt="p", task_type="feature")
        d = task.to_dict()
        assert d["title"] == "t"
        assert d["prompt"] == "p"
        assert d["task_type"] == "feature"
        assert d["file_targets"] == []
        assert d["postconditions"] == []
        assert d["dependencies"] == []
        assert d["estimated_scope"] == "atomic"
        assert d["needs_escalation"] is False

    def test_execution_plan_to_dict(self):
        task = PlannedTask(title="t", prompt="p", task_type="feature")
        plan = ExecutionPlan(
            task_type="feature",
            complexity="low",
            can_run_in_parallel=False,
            needs_escalation=False,
            tasks=[task],
        )
        d = plan.to_dict()
        assert d["task_type"] == "feature"
        assert len(d["tasks"]) == 1
        assert d["tasks"][0]["title"] == "t"


# ---------------------------------------------------------------------------
# build_execution_plan (integration)
# ---------------------------------------------------------------------------

class TestBuildExecutionPlan:
    def test_simple_prompt(self):
        plan = build_execution_plan("run pytest tests for auth.py")
        assert plan.task_type == "test_writing"
        assert len(plan.tasks) >= 1
        assert "auth.py" in plan.tasks[0].file_targets

    def test_multi_step_prompt(self):
        plan = build_execution_plan("write code and then run tests")
        assert len(plan.tasks) == 2

    def test_cwd_prefix(self):
        plan = build_execution_plan("fix bug", cwd="/home/user/myrepo")
        assert plan.tasks[0].prompt.startswith("[repo:myrepo]")

    def test_high_complexity_escalation(self):
        plan = build_execution_plan(
            "refactor the architecture of a.py and b.py and c.py"
        )
        assert plan.complexity == "high"
        assert plan.needs_escalation is True

    def test_parallel_flag(self):
        plan = build_execution_plan("write docs and write tests")
        # Two independent tasks, low complexity => can_run_in_parallel
        assert len(plan.tasks) == 2

    def test_dependencies_wired_for_sequential(self):
        plan = build_execution_plan("write code and then deploy")
        assert len(plan.tasks) == 2
        # Second task should depend on first
        if plan.tasks[1].parallel_group == "serial":
            assert len(plan.tasks[1].dependencies) > 0

    def test_to_dict_roundtrip(self):
        plan = build_execution_plan("implement feature in main.py")
        d = plan.to_dict()
        assert isinstance(d, dict)
        assert "tasks" in d
        assert isinstance(d["tasks"], list)

    def test_read_and_reply_prompt_stays_single_task(self):
        plan = build_execution_plan(
            "read tests/test_server_background.py and reply only with the number of test functions in the file"
        )
        assert len(plan.tasks) == 1
        assert plan.tasks[0].file_targets == ["tests/test_server_background.py"]


class TestPostconditions:
    def test_extracts_file_and_interface_postconditions(self):
        conditions = _extract_postconditions(
            "Create token_bucket.py implementing a TokenBucket class with methods __init__, allow(tokens: int = 1), available().",
            ["token_bucket.py"],
        )
        assert "file exists: token_bucket.py" in conditions
        assert "class exists: TokenBucket" in conditions
        assert "method exists: __init__" in conditions
        assert "method exists: allow" in conditions
        assert "method exists: available" in conditions

    def test_build_plan_attaches_postconditions_to_tasks(self):
        plan = build_execution_plan(
            "Create auth.ts implementing an AuthService class and createSession function."
        )
        assert len(plan.tasks) == 1
        task = plan.tasks[0]
        assert "file exists: auth.ts" in task.postconditions
        assert "class exists: AuthService" in task.postconditions
        assert "function exists: createSession" in task.postconditions
