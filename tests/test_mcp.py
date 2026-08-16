"""The workbench as tools an agent can call.

Two kinds of test here. The protocol ones check that a client can talk to it at
all. The others check the promises in the tool descriptions are true, because a
description is the only thing a model has to go on and one that lies is worse
than no server: `propose` must write nothing, `commit` must be the only call that
touches the filesystem, and neither the root nor the check command may be chosen
by the caller.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)                       # the engine
sys.path.insert(0, os.path.join(ROOT_DIR, "domains"))  # what is built on it

from workbench import mcp, observe


def write(root, path, text):
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with io.open(full, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def read(root, path):
    full = os.path.join(root, path)
    if not os.path.exists(full):
        return None
    with io.open(full, encoding="utf-8", newline="") as fh:
        return fh.read()


class McpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "repo")
        os.makedirs(self.root)
        write(self.root, "app.py", "print('one')\n")
        write(self.root, "README.md", "# repo\n")
        self.ctx = mcp.Context(
            db=os.path.join(self.tmp, "wb.db"),
            root=self.root,
            check_command="python3 -c \"print('fine')\"",
        )
        store = self.ctx.store()
        observe.observe(store, self.root, at=1000)
        store.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tool(self, name, **args):
        """Call a tool the way a client would, and unwrap the JSON it returns."""
        reply = mcp.handle(self.ctx, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args},
        })
        result = reply["result"]
        return json.loads(result["content"][0]["text"]), result.get("isError", False)


# ------------------------------------------------------------------- protocol


class TestProtocol(McpCase):
    def test_a_client_can_shake_hands(self):
        reply = mcp.handle(self.ctx, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
        })
        r = reply["result"]
        self.assertEqual(r["protocolVersion"], "2024-11-05")  # the client's, echoed
        self.assertIn("tools", r["capabilities"])
        self.assertEqual(r["serverInfo"]["name"], "workbench")

    def test_an_unknown_protocol_version_gets_ours_rather_than_silence(self):
        reply = mcp.handle(self.ctx, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "1999-01-01"},
        })
        self.assertEqual(reply["result"]["protocolVersion"], mcp.PREFERRED)

    def test_notifications_get_no_reply(self):
        self.assertIsNone(mcp.handle(self.ctx, {
            "jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_every_tool_is_listed_with_a_schema(self):
        reply = mcp.handle(self.ctx, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = reply["result"]["tools"]
        self.assertEqual(len(tools), len(mcp.TOOLS))
        for t in tools:
            self.assertTrue(t["description"].strip())
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_an_unknown_method_is_a_protocol_error(self):
        reply = mcp.handle(self.ctx, {"jsonrpc": "2.0", "id": 3, "method": "nope"})
        self.assertEqual(reply["error"]["code"], -32601)

    def test_the_loop_reads_lines_and_survives_rubbish(self):
        lines = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            "{ not json",
            "",
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ])
        out = io.StringIO()
        mcp.serve(self.ctx, stdin=io.StringIO(lines), stdout=out)
        replies = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
        self.assertEqual(len(replies), 3)
        self.assertEqual(replies[1]["error"]["code"], -32700)
        self.assertIn("tools", replies[2]["result"])


# ---------------------------------------------------- the promises it makes


class TestThePromises(McpCase):
    def test_propose_writes_nothing(self):
        out, err = self.tool("workbench_propose",
                             edits=[{"path": "app.py", "content": "print('two')\n"}])
        self.assertFalse(err)
        self.assertEqual(read(self.root, "app.py"), "print('one')\n")
        self.assertIn("apply", [p["id"] for p in out["plans"]])
        self.assertIn("Nothing has been written", out["note"])

    def test_the_whole_loop_through_the_tools(self):
        proposed, _ = self.tool(
            "workbench_propose",
            edits=[{"path": "app.py", "content": "print('two')\n"},
                   {"path": "lib/new.py", "content": "NEW = True\n"}],
            deletes=["README.md"],
        )
        branch = next(p["branch"] for p in proposed["plans"] if p["id"] == "apply")

        receipt, err = self.tool("workbench_commit", branch=branch)
        self.assertFalse(err)
        self.assertEqual(read(self.root, "app.py"), "print('two')\n")
        self.assertEqual(read(self.root, "lib/new.py"), "NEW = True\n")
        self.assertIsNone(read(self.root, "README.md"))
        self.assertIn("can be undone", receipt["note"])

        ran, err = self.tool("workbench_run_checks")
        self.assertFalse(err)
        self.assertTrue(ran["ok"])

        undone, err = self.tool("workbench_undo", commit_id=receipt["commit_id"])
        self.assertFalse(err)
        self.assertTrue(undone["restored"])
        self.assertTrue(undone["disk_matches"])
        self.assertEqual(read(self.root, "app.py"), "print('one')\n")
        self.assertEqual(read(self.root, "README.md"), "# repo\n")
        self.assertIsNone(read(self.root, "lib/new.py"))

    def test_status_reports_drift_so_an_agent_can_see_it_coming(self):
        write(self.root, "app.py", "print('a human was here')\n")
        out, _ = self.tool("workbench_status")
        self.assertEqual([d["path"] for d in out["drift"]], ["app.py"])

    def test_a_refusal_comes_back_as_a_readable_error_not_a_crash(self):
        proposed, _ = self.tool("workbench_propose",
                                edits=[{"path": "app.py", "content": "print('two')\n"}])
        branch = next(p["branch"] for p in proposed["plans"] if p["id"] == "apply")
        write(self.root, "app.py", "print('a human was here')\n")

        out, err = self.tool("workbench_commit", branch=branch)
        self.assertTrue(err)
        self.assertIn("changed on disk", out["error"])
        self.assertEqual(read(self.root, "app.py"), "print('a human was here')\n")

    def test_committing_something_that_was_never_proposed_is_refused(self):
        out, err = self.tool("workbench_commit", branch="trunk")
        self.assertTrue(err)
        self.assertIn("not a proposal", out["error"])


# --------------------------------------------------------------- the fences


class TestWhatTheAgentCannotDo(McpCase):
    def test_an_edit_cannot_escape_the_root(self):
        for bad in ("../escape.txt", "/etc/passwd", "lib/../../escape.txt"):
            out, err = self.tool("workbench_propose",
                                 edits=[{"path": bad, "content": "x\n"}])
            self.assertTrue(err, bad)
            self.assertIn("path", out["error"].lower())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.txt")))

    def test_no_tool_takes_a_root(self):
        """The directory is chosen when the server starts, and never again."""
        for name, spec in mcp.TOOLS.items():
            props = spec["schema"].get("properties", {})
            for forbidden in ("root", "cwd", "directory", "db"):
                self.assertNotIn(forbidden, props, f"{name} exposes {forbidden}")

    def test_an_agent_cannot_force_an_undo_over_somebody_elses_work(self):
        """The override exists for a human at a terminal, not for a model."""
        self.assertNotIn("force", mcp.TOOLS["workbench_undo"]["schema"]["properties"])

        proposed, _ = self.tool("workbench_propose",
                                edits=[{"path": "app.py", "content": "print('agent')\n"}])
        branch = next(p["branch"] for p in proposed["plans"] if p["id"] == "apply")
        receipt, _ = self.tool("workbench_commit", branch=branch)
        write(self.root, "app.py", "print('a human improved this')\n")

        out, err = self.tool("workbench_undo", commit_id=receipt["commit_id"], force=True)
        self.assertTrue(err)
        self.assertIn("changed since this commit", out["error"])
        self.assertEqual(read(self.root, "app.py"), "print('a human improved this')\n")

    def test_run_checks_is_not_a_shell(self):
        props = mcp.TOOLS["workbench_run_checks"]["schema"].get("properties", {})
        self.assertEqual(props, {})
        # And an argument smuggled in anyway is simply ignored.
        out, err = self.tool("workbench_run_checks", command="echo pwned")
        self.assertFalse(err)
        self.assertTrue(out["ok"])

    def test_without_a_configured_command_there_is_nothing_to_run(self):
        self.ctx.check_command = None
        out, err = self.tool("workbench_run_checks")
        self.assertTrue(err)
        self.assertIn("started without a check command", out["error"])

    def test_deleting_a_file_the_workbench_does_not_manage_is_refused(self):
        write(self.root, "untracked.txt", "not managed\n")
        out, err = self.tool("workbench_propose", deletes=["untracked.txt"])
        self.assertTrue(err)
        self.assertIn("not a file this workbench is managing", out["error"])
        self.assertEqual(read(self.root, "untracked.txt"), "not managed\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
