"""The agent's tool surface.

Two things are checked: that it is at `/mcp` (a mount would silently put it at
`/mcp/mcp`), and that it exposes exactly the verbs the design sanctions —
publishing and cancelling stay with the human.
"""

from __future__ import annotations

import json

SANCTIONED = {
    "submit_job",
    "job_status",
    "list_jobs",
    "read_results",
    "compile_paper",
    "record_result",
    "write_result_table",
    "review_my_changes",
}

# Anything that publishes, or that reaches a job this session did not create.
WITHHELD = {
    "git_push",
    "git_commit",
    "commit",
    "push",
    "sync",
    "overleaf_push",
    "scancel",
    "cancel_job",
    "write_file",
}


def _rpc(client, method: str, params: dict | None = None, _id: int = 1, sid: str | None = None):
    body: dict = {"jsonrpc": "2.0", "id": _id, "method": method}
    if params is not None:
        body["params"] = params
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if sid:
        headers["mcp-session-id"] = sid
    res = client.post("/mcp", json=body, headers=headers)
    assert res.status_code == 200, res.text
    payload = res.text
    for line in payload.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:]), res.headers.get("mcp-session-id")
    return json.loads(payload), res.headers.get("mcp-session-id")


def _init(client):
    return _rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    )


def test_the_tool_surface_is_at_slash_mcp(client) -> None:
    """The agent is configured with `.../mcp`; a mount would serve `/mcp/mcp`."""
    body, _ = _init(client)
    assert body["result"]["serverInfo"]["name"] == "galley"


def test_slash_mcp_slash_mcp_is_not_the_endpoint(client) -> None:
    res = client.post(
        "/mcp/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert res.status_code != 200


def test_exactly_the_sanctioned_tools_are_exposed(client) -> None:
    _, sid = _init(client)
    body, _ = _rpc(client, "tools/list", {}, 2, sid)
    names = {t["name"] for t in body["result"]["tools"]}
    assert names == SANCTIONED


def test_nothing_that_publishes_is_exposed(client) -> None:
    _, sid = _init(client)
    body, _ = _rpc(client, "tools/list", {}, 2, sid)
    names = {t["name"] for t in body["result"]["tools"]}
    assert not (names & WITHHELD)


def test_read_results_fails_loudly_on_an_unfetched_job(client) -> None:
    _, sid = _init(client)
    body, _ = _rpc(
        client, "tools/call", {"name": "read_results", "arguments": {"job_id": "ghost"}}, 3, sid
    )
    assert "ERROR" in body["result"]["content"][0]["text"]


def test_write_result_table_refuses_without_a_spec(client) -> None:
    _, sid = _init(client)
    body, _ = _rpc(
        client,
        "tools/call",
        {"name": "write_result_table", "arguments": {"table_key": "nosuch", "job_ids": ["x"]}},
        4,
        sid,
    )
    assert "no table spec" in body["result"]["content"][0]["text"]
