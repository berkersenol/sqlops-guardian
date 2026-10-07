"""
Tests for app/verifier.py -- the rewrite verification agent.

Design notes:

- Groq is always mocked, as everywhere else in this suite. The fixture here is
  `mock_agent` rather than conftest's `mock_groq`, because a tool-calling loop
  needs scripted *turns*: a sequence of responses, each either tool calls or a
  final answer, with the request recorded so a test can assert what the model
  was actually shown.
- The fixture database is built into tmp_path per test and passed explicitly
  via db_path, so nothing touches the configured VERIFY_DB_PATH.
- The security tests are the ones to read first. They are table-driven and
  every entry is a statement that must never execute; `test_fixture_survives_
  attacks` then re-checks the data afterwards, because a guard that returns
  the right error while still having run the statement would pass a
  rejection-only assertion.
"""

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import verify_fixture
from app.verifier import (
    QueryRejected,
    TOOL_SCHEMAS,
    Verdict,
    VerificationResult,
    assert_read_only_select,
    compare_results,
    get_schema,
    run_query,
    verify_rewrite,
)


@pytest.fixture
def fixture_db(tmp_path) -> Path:
    """A freshly built fixture database of its own."""
    return verify_fixture.build(tmp_path / "verify.db")


# --------------------------------------------------------------------------
# The read-only guard
#
# This is the security boundary, so the cases are explicit rather than
# generated. Each one is a real way to smuggle a write past a naive check.
# --------------------------------------------------------------------------

MUST_REJECT = [
    pytest.param("SELECT 1; DROP TABLE orders", id="batched-drop"),
    pytest.param("/*c*/ DELETE FROM orders", id="comment-prefixed-delete"),
    pytest.param("  \n\t DELETE FROM orders", id="whitespace-prefixed-delete"),
    # The one a root-type check misses: sqlglot parses this as a Select.
    pytest.param(
        "WITH x AS (DELETE FROM orders RETURNING id) SELECT * FROM x",
        id="cte-hidden-delete",
    ),
    pytest.param(
        "WITH x AS (INSERT INTO users VALUES (9,'z',NULL,'x','y') RETURNING id) "
        "SELECT * FROM x",
        id="cte-hidden-insert",
    ),
    pytest.param("DROP TABLE users", id="drop"),
    pytest.param("UPDATE orders SET total = 0", id="update"),
    pytest.param("INSERT INTO users VALUES (9,'z',NULL,'x','y')", id="insert"),
    pytest.param("CREATE TABLE t (a INT)", id="create"),
    pytest.param("ALTER TABLE users ADD COLUMN x INT", id="alter"),
    pytest.param("PRAGMA table_info(orders)", id="pragma"),
    pytest.param("ATTACH DATABASE '/tmp/evil.db' AS evil", id="attach"),
    pytest.param("VACUUM", id="unmodelled-command"),
    pytest.param("", id="empty"),
    pytest.param("   ", id="blank"),
    pytest.param("selekt bad syntax ((", id="unparseable"),
]

MUST_ALLOW = [
    pytest.param("SELECT 1", id="trivial"),
    pytest.param("SELECT id FROM orders WHERE total > 100", id="filtered"),
    pytest.param("SELECT a FROM (SELECT id AS a FROM orders)", id="subquery"),
    pytest.param("SELECT id FROM orders UNION SELECT id FROM orders", id="union"),
    pytest.param(
        "SELECT id FROM orders UNION ALL SELECT id FROM orders", id="union-all"
    ),
    pytest.param("SELECT id FROM users EXCEPT SELECT user_id FROM orders", id="except"),
    pytest.param(
        "SELECT user_id FROM orders INTERSECT SELECT id FROM users", id="intersect"
    ),
    pytest.param(
        "WITH r AS (SELECT * FROM orders WHERE total > 1) SELECT COUNT(*) FROM r",
        id="read-only-cte",
    ),
    pytest.param("SELECT COUNT(*), COUNT(discount) FROM products", id="aggregates"),
]


@pytest.mark.parametrize("sql", MUST_REJECT)
def test_guard_rejects(sql, fixture_db):
    with pytest.raises(QueryRejected):
        run_query(sql, fixture_db)


@pytest.mark.parametrize("sql", MUST_ALLOW)
def test_guard_allows_read_only(sql, fixture_db):
    result = run_query(sql, fixture_db)
    assert result["row_count"] >= 0
    assert isinstance(result["columns"], list)


def test_guard_returns_parsed_expression():
    """assert_read_only_select hands back the parse so callers need not redo it."""
    parsed = assert_read_only_select("SELECT id FROM orders")
    assert parsed.sql(dialect="sqlite")


@pytest.mark.parametrize("sql", MUST_REJECT)
def test_fixture_survives_attacks(sql, fixture_db):
    """The data is unchanged after every rejected statement.

    Asserting only that QueryRejected was raised would still pass if the
    statement had executed and then been reported as refused, so the state is
    re-checked rather than inferred.
    """
    with pytest.raises(QueryRejected):
        run_query(sql, fixture_db)

    conn = sqlite3.connect(fixture_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == len(
            verify_fixture.ORDERS
        )
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == len(
            verify_fixture.USERS
        )
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"users", "orders", "products", "order_items"} <= tables
    finally:
        conn.close()


def test_connection_is_read_only_independently_of_the_parser(fixture_db):
    """mode=ro is enforced by SQLite, not by our parse check.

    This is the defence-in-depth claim the module docstring makes, so it is
    asserted directly rather than trusted: a write fails on this connection
    even when the guard is not in the path at all.
    """
    conn = sqlite3.connect(f"file:{fixture_db.as_posix()}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM orders")
    finally:
        conn.close()


def test_row_limit_truncates(fixture_db, monkeypatch):
    from app import config as config_mod

    monkeypatch.setattr(config_mod.config, "VERIFY_ROW_LIMIT", 3)
    result = run_query("SELECT id FROM orders", fixture_db)
    assert result["row_count"] == 3
    assert result["truncated"] is True


def test_row_limit_is_not_applied_by_rewriting_the_sql(fixture_db):
    """A query's own LIMIT is respected; the cap does not replace it."""
    result = run_query("SELECT id FROM orders LIMIT 2", fixture_db)
    assert result["row_count"] == 2
    assert result["truncated"] is False


def test_timeout_cancels_a_runaway_query(fixture_db, monkeypatch):
    from app import config as config_mod

    monkeypatch.setattr(config_mod.config, "VERIFY_TIMEOUT_MS", 100)
    cross = ", ".join(f"orders t{i}" for i in range(10))
    with pytest.raises(QueryRejected, match="time budget"):
        run_query(f"SELECT COUNT(*) FROM {cross}", fixture_db)


def test_missing_table_is_rejected_not_raised(fixture_db):
    with pytest.raises(QueryRejected, match="no such table"):
        run_query("SELECT * FROM does_not_exist", fixture_db)


# --------------------------------------------------------------------------
# get_schema
# --------------------------------------------------------------------------

def test_get_schema_lists_tables_and_counts(fixture_db):
    schema = get_schema(fixture_db)
    by_name = {t["table"]: t for t in schema["tables"]}
    assert set(by_name) == {"users", "orders", "products", "order_items"}
    assert by_name["orders"]["row_count"] == len(verify_fixture.ORDERS)


def test_get_schema_reports_nullability(fixture_db):
    """Nullability is load-bearing: the NOT IN case is unreasonable without it."""
    orders = next(t for t in get_schema(fixture_db)["tables"] if t["table"] == "orders")
    nullable = {c["name"]: c["nullable"] for c in orders["columns"]}
    assert nullable["user_id"] is True, "the NOT IN trap depends on this"
    assert nullable["id"] is False, "INTEGER PRIMARY KEY is not nullable"

    users = next(t for t in get_schema(fixture_db)["tables"] if t["table"] == "users")
    user_nullable = {c["name"]: c["nullable"] for c in users["columns"]}
    assert user_nullable["name"] is False
    assert user_nullable["email"] is True


# --------------------------------------------------------------------------
# The fixture itself
#
# Every wrong pair in the golden set depends on specific rows. An edit that
# removes them would leave the eval passing while silently testing nothing, so
# the properties are asserted here, next to the names the golden set uses.
# --------------------------------------------------------------------------

def test_fixture_has_a_null_foreign_key(fixture_db):
    """Without this, NOT IN and NOT EXISTS agree and that case disappears."""
    rows = run_query("SELECT COUNT(*) FROM orders WHERE user_id IS NULL", fixture_db)
    assert rows["rows"][0][0] >= 1


def test_fixture_has_users_without_orders(fixture_db):
    """The other half of the NOT IN trap: something for NOT EXISTS to return."""
    rows = run_query(
        "SELECT COUNT(*) FROM users u WHERE NOT EXISTS "
        "(SELECT 1 FROM orders o WHERE o.user_id = u.id)",
        fixture_db,
    )
    assert rows["rows"][0][0] >= 1


def test_fixture_has_rows_matching_both_or_branches(fixture_db):
    """Without this, UNION and UNION ALL return the same thing."""
    rows = run_query(
        "SELECT COUNT(*) FROM orders WHERE status = 'shipped' AND total > 500",
        fixture_db,
    )
    assert rows["rows"][0][0] >= 1


def test_fixture_has_a_parent_with_multiple_children(fixture_db):
    """Without this, an EXISTS-to-JOIN rewrite does not fan out."""
    rows = run_query(
        "SELECT COUNT(*) FROM (SELECT user_id FROM orders WHERE status = 'shipped' "
        "GROUP BY user_id HAVING COUNT(*) >= 2)",
        fixture_db,
    )
    assert rows["rows"][0][0] >= 1


def test_fixture_has_genuinely_duplicate_rows(fixture_db):
    """order_items has no primary key so a set/multiset mix-up is detectable."""
    rows = run_query(
        "SELECT COUNT(*) FROM (SELECT order_id, product_id, quantity "
        "FROM order_items GROUP BY 1, 2, 3 HAVING COUNT(*) > 1)",
        fixture_db,
    )
    assert rows["rows"][0][0] >= 1


def test_build_is_idempotent(tmp_path):
    first = verify_fixture.build(tmp_path / "f.db")
    before = run_query("SELECT COUNT(*) FROM orders", first)["rows"][0][0]
    second = verify_fixture.build(tmp_path / "f.db")
    after = run_query("SELECT COUNT(*) FROM orders", second)["rows"][0][0]
    assert before == after == len(verify_fixture.ORDERS)


def test_ensure_builds_only_when_absent(tmp_path):
    path = tmp_path / "e.db"
    assert not path.exists()
    verify_fixture.ensure(path)
    assert path.is_file()
    mtime = path.stat().st_mtime_ns
    verify_fixture.ensure(path)
    assert path.stat().st_mtime_ns == mtime, "should not have been rebuilt"


# --------------------------------------------------------------------------
# compare_results -- the oracle
# --------------------------------------------------------------------------

def test_compare_identical_queries_match(fixture_db):
    sql = "SELECT id FROM orders WHERE total > 100"
    assert compare_results(sql, sql, fixture_db)["match"] is True


def test_compare_ignores_row_order(fixture_db):
    a = "SELECT id FROM orders ORDER BY id ASC"
    b = "SELECT id FROM orders ORDER BY id DESC"
    assert compare_results(a, b, fixture_db)["match"] is True


def test_compare_counts_duplicates(fixture_db):
    """The multiset property, stated as its own test.

    These two differ only in multiplicity, so a set-based comparison would
    call them equal. That mistake is what lets an EXISTS-to-JOIN rewrite pass.
    """
    a = "SELECT id FROM orders"
    b = "SELECT id FROM orders UNION ALL SELECT id FROM orders"
    result = compare_results(a, b, fixture_db)
    assert result["match"] is False
    assert "x2" in result["diff"] or "16" in result["diff"]


def test_compare_detects_column_count_change(fixture_db):
    result = compare_results(
        "SELECT id FROM orders", "SELECT id, total FROM orders", fixture_db
    )
    assert result["match"] is False
    assert "column counts" in result["diff"].lower()


def test_compare_reports_incomparable_for_a_rejected_query(fixture_db):
    result = compare_results(
        "SELECT id FROM orders", "DROP TABLE orders", fixture_db
    )
    assert result["comparable"] is False
    assert result["match"] is False
    assert "rewritten query" in result["error"]


def test_compare_distinguishes_which_side_failed(fixture_db):
    result = compare_results("DROP TABLE orders", "SELECT id FROM orders", fixture_db)
    assert result["comparable"] is False
    assert "original query" in result["error"]


def test_compare_refuses_to_conclude_a_match_on_truncated_results(
    fixture_db, monkeypatch
):
    """A truncated match is not a match: the unfetched rows could differ."""
    from app import config as config_mod

    monkeypatch.setattr(config_mod.config, "VERIFY_ROW_LIMIT", 2)
    result = compare_results(
        "SELECT id FROM orders", "SELECT id FROM orders", fixture_db
    )
    assert result["comparable"] is False
    assert "truncated" in result["error"]


def test_compare_does_not_collapse_distinct_values_of_different_types(fixture_db):
    """1, 1.0 and True must not hash to the same row key."""
    result = compare_results("SELECT 1", "SELECT 1.0", fixture_db)
    assert result["match"] is False


# --------------------------------------------------------------------------
# The golden pairs, through the deterministic path only
# --------------------------------------------------------------------------

GOLDEN = json.loads(
    (Path(__file__).resolve().parents[1] / "evals" / "golden_verifier.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize(
    "pair", [pytest.param(p, id=p["id"]) for p in GOLDEN["pairs"]]
)
def test_golden_pairs_behave_as_labelled(pair, fixture_db):
    """The three wrong pairs mismatch and the three correct ones match.

    This is the eval's claim, asserted without a network call. If the fixture
    ever stops exposing a case, this fails here rather than in a live eval run.
    """
    result = compare_results(pair["original"], pair["rewrite"], fixture_db)
    assert result["comparable"] is True
    expected_match = pair["expected_verdict"] == "equivalent_on_test_data"
    assert result["match"] is expected_match


# --------------------------------------------------------------------------
# A scripted agent loop
# --------------------------------------------------------------------------

def _tool_call(call_id: str, name: str, **args):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _turn(content: str = "", tool_calls=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(total_tokens=42),
    )


@pytest.fixture
def mock_agent(monkeypatch):
    """Script the model's turns and record what it was sent.

    mock_agent.turns = [...]   -- responses, consumed in order
    mock_agent.requests        -- the create() kwargs for each round trip
    Running past the end of the script raises, so a test that expected three
    turns and got four fails loudly instead of hanging.
    """
    from app import config as config_mod
    from app import llm_analyzer

    state = SimpleNamespace(turns=[], requests=[])

    def create(**kwargs):
        # Snapshot `messages`: the loop appends to one list across turns, so
        # recording the object itself would make every request alias the final
        # state and a test could never see what turn 2 was actually sent.
        state.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        if not state.turns:
            raise AssertionError("the agent made more calls than the script allows")
        return state.turns.pop(0)

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(config_mod.config, "GROQ_API_KEY", "test-key-not-real")
    monkeypatch.setattr(llm_analyzer, "_get_client", lambda: client)
    monkeypatch.setattr(llm_analyzer, "_client", None)
    return state


EQUIVALENT_PAIR = (
    "SELECT id, status FROM orders WHERE status = 'shipped' OR status = 'pending'",
    "SELECT id, status FROM orders WHERE status IN ('shipped', 'pending')",
)
UNION_ALL_PAIR = (
    "SELECT id FROM orders WHERE status = 'shipped' OR total > 500",
    "SELECT id FROM orders WHERE status = 'shipped' "
    "UNION ALL SELECT id FROM orders WHERE total > 500",
)


# ---- the deterministic short-circuit ----

def test_mismatch_returns_not_equivalent_without_calling_groq(
    fixture_db, mock_agent
):
    """The headline behaviour: a proof needs no LLM, and none is contacted.

    mock_agent's script is empty, so any round trip would raise. That is the
    assertion -- not just that decided_without_llm is True, but that the
    network was never reached.
    """
    result = verify_rewrite(*UNION_ALL_PAIR, db_path=fixture_db)
    assert result.verdict is Verdict.NOT_EQUIVALENT
    assert result.decided_without_llm is True
    assert result.steps_taken == 1
    assert mock_agent.requests == []
    assert "Proven by execution" in result.evidence
    assert "6" in result.evidence and "8" in result.evidence


def test_mismatch_verdict_is_identical_with_no_api_key(fixture_db, monkeypatch):
    from app import config as config_mod

    monkeypatch.setattr(config_mod.config, "GROQ_API_KEY", "")
    result = verify_rewrite(*UNION_ALL_PAIR, db_path=fixture_db)
    assert result.verdict is Verdict.NOT_EQUIVALENT
    assert result.decided_without_llm is True


def test_unrunnable_query_is_undetermined_not_not_equivalent(fixture_db, mock_agent):
    """"We could not check" must never be reported as "we proved it wrong"."""
    result = verify_rewrite(
        "SELECT id FROM orders", "DROP TABLE orders", db_path=fixture_db
    )
    assert result.verdict is Verdict.UNDETERMINED
    assert result.decided_without_llm is True
    assert mock_agent.requests == []


def test_no_api_key_on_a_match_reports_the_gap(fixture_db, monkeypatch):
    from app import config as config_mod

    monkeypatch.setattr(config_mod.config, "GROQ_API_KEY", "")
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)
    assert result.verdict is Verdict.EQUIVALENT_ON_TEST_DATA
    assert result.probe_status == "skipped"
    assert "unknown whether this data exercises" in result.evidence


# ---- the probing loop ----

def test_agent_loop_probes_then_submits(fixture_db, mock_agent):
    mock_agent.turns = [
        _turn(tool_calls=[_tool_call("c1", "get_schema")]),
        _turn(tool_calls=[
            _tool_call("c2", "run_query",
                       sql="SELECT COUNT(*) FROM orders WHERE status IS NULL")
        ]),
        _turn(tool_calls=[
            _tool_call("c3", "submit_verdict",
                       verdict="equivalent_on_test_data",
                       evidence="1 NULL status row, so the NULL path is exercised.")
        ]),
    ]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)

    assert result.verdict is Verdict.EQUIVALENT_ON_TEST_DATA
    assert result.probe_status == "ok"
    assert result.decided_without_llm is False
    assert "NULL path is exercised" in result.evidence
    # Step 0 is the deterministic comparison; the rest are the agent's.
    assert [c.tool for c in result.tool_calls] == [
        "compare_results", "get_schema", "run_query", "submit_verdict"
    ]
    assert result.steps_taken == 4


def test_every_tool_call_and_result_is_logged(fixture_db, mock_agent):
    mock_agent.turns = [
        _turn(tool_calls=[
            _tool_call("c1", "run_query", sql="SELECT COUNT(*) FROM users")
        ]),
        _turn(tool_calls=[
            _tool_call("c2", "submit_verdict",
                       verdict="equivalent_on_test_data", evidence="checked")
        ]),
    ]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)

    probe = next(c for c in result.tool_calls if c.tool == "run_query")
    assert probe.args == {"sql": "SELECT COUNT(*) FROM users"}
    assert probe.ok is True
    assert "5" in probe.result, "the result, not just the call, is recorded"


def test_tool_result_is_fed_back_to_the_model(fixture_db, mock_agent):
    """The model only knows what the tool messages told it."""
    mock_agent.turns = [
        _turn(tool_calls=[_tool_call("c1", "get_schema")]),
        _turn(tool_calls=[
            _tool_call("c2", "submit_verdict",
                       verdict="equivalent_on_test_data", evidence="ok")
        ]),
    ]
    verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)

    second = mock_agent.requests[1]["messages"]
    tool_messages = [m for m in second if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "c1"
    assert "orders" in tool_messages[0]["content"]
    # The assistant turn carrying the call must precede its result, or the
    # provider rejects the exchange as unpaired.
    roles = [m["role"] for m in second]
    assert roles.index("assistant") < roles.index("tool")


def test_rejected_tool_call_is_reported_to_the_model_not_raised(
    fixture_db, mock_agent
):
    """A bad tool call costs a step, not the run."""
    mock_agent.turns = [
        _turn(tool_calls=[_tool_call("c1", "run_query", sql="DROP TABLE orders")]),
        _turn(tool_calls=[
            _tool_call("c2", "submit_verdict",
                       verdict="equivalent_on_test_data", evidence="recovered")
        ]),
    ]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)

    assert result.verdict is Verdict.EQUIVALENT_ON_TEST_DATA
    rejected = next(c for c in result.tool_calls if c.tool == "run_query")
    assert rejected.ok is False
    assert "not executed" in rejected.result
    # And the model was told, so it could recover.
    tool_messages = [
        m for m in mock_agent.requests[1]["messages"] if m["role"] == "tool"
    ]
    assert any("not executed" in m["content"] for m in tool_messages)


def test_unknown_tool_name_is_reported_not_raised(fixture_db, mock_agent):
    mock_agent.turns = [
        _turn(tool_calls=[_tool_call("c1", "definitely_not_a_tool")]),
        _turn(tool_calls=[
            _tool_call("c2", "submit_verdict",
                       verdict="equivalent_on_test_data", evidence="ok")
        ]),
    ]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)
    assert result.verdict is Verdict.EQUIVALENT_ON_TEST_DATA
    bad = next(c for c in result.tool_calls if c.tool == "definitely_not_a_tool")
    assert bad.ok is False


def test_malformed_tool_arguments_do_not_crash_the_loop(fixture_db, mock_agent):
    broken = SimpleNamespace(
        id="c1", type="function",
        function=SimpleNamespace(name="run_query", arguments="{not json"),
    )
    mock_agent.turns = [
        _turn(tool_calls=[broken]),
        _turn(tool_calls=[
            _tool_call("c2", "submit_verdict",
                       verdict="equivalent_on_test_data", evidence="ok")
        ]),
    ]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)
    assert result.verdict is Verdict.EQUIVALENT_ON_TEST_DATA
    assert next(c for c in result.tool_calls if c.tool == "run_query").ok is False


# ---- the step limit ----

def test_step_limit_yields_undetermined(fixture_db, mock_agent):
    """A model that never commits gets cut off and reported as unresolved.

    The script repeats the same probe, which is the characteristic loop
    failure the cap exists for.
    """
    mock_agent.turns = [
        _turn(tool_calls=[_tool_call(f"c{i}", "get_schema")]) for i in range(6)
    ]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db, max_steps=4)

    assert result.verdict is Verdict.UNDETERMINED
    assert result.probe_status == "failed"
    assert "without reaching a conclusion" in result.evidence
    # max_steps is the budget for the whole run, phase 1 included.
    assert result.steps_taken <= 4
    assert len(mock_agent.requests) == 3


def test_steps_never_exceed_the_configured_limit(fixture_db, mock_agent):
    mock_agent.turns = [
        _turn(tool_calls=[_tool_call(f"c{i}", "get_schema")]) for i in range(10)
    ]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db, max_steps=6)
    assert result.steps_taken <= 6


def test_default_step_limit_is_six(fixture_db):
    from app.config import config

    assert config.VERIFY_MAX_STEPS == 6


# ---- guarding the oracle against the model ----

def test_model_cannot_claim_not_equivalent_without_a_mismatch(
    fixture_db, mock_agent
):
    """A hallucinated proof is downgraded, not reported.

    The deterministic comparison already matched. A model asserting
    not_equivalent without a compare_results call that actually differed is
    overruling the oracle on vibes, so the verdict is corrected and the
    reasoning preserved for a human.
    """
    mock_agent.turns = [
        _turn(tool_calls=[
            _tool_call("c1", "submit_verdict", verdict="not_equivalent",
                       evidence="I think IN behaves differently with NULLs.")
        ]),
    ]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)

    assert result.verdict is Verdict.EQUIVALENT_ON_TEST_DATA
    assert "not supported by evidence" in result.evidence
    assert "I think IN behaves differently" in result.evidence


def test_model_may_claim_not_equivalent_with_a_real_mismatch(fixture_db, mock_agent):
    """The downgrade is evidence-based, not a blanket veto."""
    mock_agent.turns = [
        _turn(tool_calls=[
            _tool_call("c1", "compare_results",
                       sql_a="SELECT id FROM orders",
                       sql_b="SELECT id FROM orders UNION ALL SELECT id FROM orders")
        ]),
        _turn(tool_calls=[
            _tool_call("c2", "submit_verdict", verdict="not_equivalent",
                       evidence="A variant comparison returned different rows.")
        ]),
    ]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)
    assert result.verdict is Verdict.NOT_EQUIVALENT


def test_unrecognised_verdict_string_is_undetermined(fixture_db, mock_agent):
    mock_agent.turns = [
        _turn(tool_calls=[
            _tool_call("c1", "submit_verdict", verdict="probably fine",
                       evidence="hmm")
        ]),
    ]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)
    assert result.verdict is Verdict.UNDETERMINED
    assert result.probe_status == "failed"


def test_verdict_string_is_normalised(fixture_db, mock_agent):
    mock_agent.turns = [
        _turn(tool_calls=[
            _tool_call("c1", "submit_verdict", verdict="  EQUIVALENT_ON_TEST_DATA ",
                       evidence="ok")
        ]),
    ]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)
    assert result.verdict is Verdict.EQUIVALENT_ON_TEST_DATA


# ---- answering in content rather than through the tool ----

def test_content_json_answer_is_still_accepted(fixture_db, mock_agent):
    """The fallback path, for a model that ignores submit_verdict."""
    mock_agent.turns = [
        _turn(content=json.dumps({
            "verdict": "equivalent_on_test_data",
            "evidence": "Answered in prose instead.",
        }))
    ]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)
    assert result.verdict is Verdict.EQUIVALENT_ON_TEST_DATA
    assert "Answered in prose" in result.evidence


def test_content_answer_wrapped_in_fences_is_accepted(fixture_db, mock_agent):
    mock_agent.turns = [
        _turn(content='```json\n{"verdict": "equivalent_on_test_data", '
                      '"evidence": "fenced"}\n```')
    ]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)
    assert result.verdict is Verdict.EQUIVALENT_ON_TEST_DATA


def test_unparseable_content_answer_is_undetermined(fixture_db, mock_agent):
    mock_agent.turns = [_turn(content="They look about the same to me!")]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)
    assert result.verdict is Verdict.UNDETERMINED
    assert result.probe_status == "failed"


# ---- failure reporting ----

def test_groq_failure_keeps_the_match_but_flags_the_probe(fixture_db, mock_agent):
    """A broken integration must not look like a clean pass.

    This is the regression test for the first live eval run, which scored 6/6
    while Groq was returning 400 on two pairs. The verdict stays
    EQUIVALENT_ON_TEST_DATA, because the results genuinely did match -- but
    probe_status says the agent never actually ran.
    """
    def boom(**kwargs):
        raise RuntimeError("Error code: 400 - tool call validation failed")

    mock_agent.requests = []
    from app import llm_analyzer

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=boom))
    )
    llm_analyzer._get_client = lambda: client

    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)
    assert result.verdict is Verdict.EQUIVALENT_ON_TEST_DATA
    assert result.probe_status == "failed"
    assert "400" in result.probe_error


# ---- what the model is shown ----

def test_the_model_is_sent_the_tools_and_both_queries(fixture_db, mock_agent):
    mock_agent.turns = [
        _turn(tool_calls=[
            _tool_call("c1", "submit_verdict",
                       verdict="equivalent_on_test_data", evidence="ok")
        ]),
    ]
    verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db)

    request = mock_agent.requests[0]
    names = {t["function"]["name"] for t in request["tools"]}
    assert names == {"get_schema", "run_query", "compare_results", "submit_verdict"}
    user = request["messages"][1]["content"]
    assert EQUIVALENT_PAIR[0] in user and EQUIVALENT_PAIR[1] in user
    # It must know the comparison already ran, or it wastes a step repeating it.
    assert "matched" in user.lower()
    assert request["temperature"] == 0.0


def test_tool_schemas_are_well_formed():
    for schema in TOOL_SCHEMAS:
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] and fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        for required in params.get("required", []):
            assert required in params["properties"]


def test_submit_verdict_schema_enumerates_exactly_the_verdicts():
    fn = next(s["function"] for s in TOOL_SCHEMAS
              if s["function"]["name"] == "submit_verdict")
    assert set(fn["parameters"]["properties"]["verdict"]["enum"]) == {
        v.value for v in Verdict
    }


# ---- the result model ----

def test_result_serialises_for_the_mcp_layer(fixture_db, mock_agent):
    result = verify_rewrite(*UNION_ALL_PAIR, db_path=fixture_db)
    payload = result.model_dump(mode="json")
    assert payload["verdict"] == "not_equivalent"
    assert isinstance(payload["evidence"], str)
    assert isinstance(payload["steps_taken"], int)
    assert isinstance(payload["tool_calls"], list)
    assert json.dumps(payload), "must be JSON-serialisable for MCP"


def test_result_rejects_an_invalid_verdict():
    with pytest.raises(Exception):
        VerificationResult(verdict="maybe", evidence="", steps_taken=1)


# ---- the agent is told how much budget it has left ----

def test_each_turn_states_the_remaining_budget(fixture_db, mock_agent):
    """A limit the agent cannot see is a limit it cannot plan around.

    A live eval run lost a pair by spending four probes characterising the
    data and being cut off mid-investigation, so the remaining count is now
    stated every turn.
    """
    mock_agent.turns = [
        _turn(tool_calls=[_tool_call("c1", "get_schema")]),
        _turn(tool_calls=[
            _tool_call("c2", "submit_verdict",
                       verdict="equivalent_on_test_data", evidence="ok")
        ]),
    ]
    verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db, max_steps=6)

    first = mock_agent.requests[0]["messages"][-1]["content"]
    second = mock_agent.requests[1]["messages"][-1]["content"]
    assert "4 step(s) left" in first, "6 total, minus phase 1, minus this turn"
    assert "3 step(s) left" in second


def test_final_turn_forces_a_verdict(fixture_db, mock_agent):
    """On the last step the model is made to answer rather than cut off.

    The budget is unchanged; what changes is that running out now produces a
    conclusion drawn from what the agent had, instead of nothing.
    """
    mock_agent.turns = [
        _turn(tool_calls=[_tool_call("c1", "get_schema")]),
        _turn(tool_calls=[
            _tool_call("c2", "submit_verdict",
                       verdict="equivalent_on_test_data", evidence="forced")
        ]),
    ]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db, max_steps=3)

    final_request = mock_agent.requests[-1]
    assert final_request["tool_choice"] == {
        "type": "function", "function": {"name": "submit_verdict"},
    }
    assert "final step" in final_request["messages"][-1]["content"]
    assert result.verdict is Verdict.EQUIVALENT_ON_TEST_DATA


def test_non_final_turns_leave_the_tool_choice_open(fixture_db, mock_agent):
    mock_agent.turns = [
        _turn(tool_calls=[_tool_call("c1", "get_schema")]),
        _turn(tool_calls=[
            _tool_call("c2", "submit_verdict",
                       verdict="equivalent_on_test_data", evidence="ok")
        ]),
    ]
    verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db, max_steps=6)
    assert mock_agent.requests[0]["tool_choice"] == "auto"


def test_step_limit_still_applies_when_the_model_ignores_the_forced_call(
    fixture_db, mock_agent
):
    """Forcing the call is a nudge, not a guarantee; the cap still holds."""
    mock_agent.turns = [
        _turn(tool_calls=[_tool_call(f"c{i}", "get_schema")]) for i in range(6)
    ]
    result = verify_rewrite(*EQUIVALENT_PAIR, db_path=fixture_db, max_steps=4)
    assert result.verdict is Verdict.UNDETERMINED
    assert result.steps_taken <= 4
