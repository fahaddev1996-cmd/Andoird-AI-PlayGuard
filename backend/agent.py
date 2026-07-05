"""
PlayGuard AI — Autonomous ReAct Agent

This is the core of the agentic system. Given a goal (make this app
Play Store ready), the agent:
  1. Reasons about what to do next
  2. Calls an MCP tool
  3. Observes the result
  4. Reasons again → repeat until goal achieved

Pattern: ReAct (Reason → Act → Observe → Reason → ...)
Tools are called via MCP — the agent doesn't contain analysis logic itself.
"""

import sys, os, json, re, asyncio
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MCP_SERVER_SCRIPT = str(Path(__file__).parent / "mcp_server" / "server.py")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")


# ════════════════════════════════════════════════════════════════
# MCP SESSION HELPER
# ════════════════════════════════════════════════════════════════

class MCPSession:
    """Persistent MCP client session — spawns the server once, reuses it."""

    def __init__(self):
        self._session = None
        self._cm = None
        self._read = None
        self._write = None

    async def start(self):
        params = StdioServerParameters(command=sys.executable, args=[MCP_SERVER_SCRIPT])
        self._cm = stdio_client(params)
        self._read, self._write = await self._cm.__aenter__()
        self._session = ClientSession(self._read, self._write)
        await self._session.__aenter__()
        await self._session.initialize()

    async def call_tool(self, name: str, args: dict) -> dict:
        result = await self._session.call_tool(name, args)
        text = result.content[0].text
        return json.loads(text)

    async def list_tools(self) -> list:
        result = await self._session.list_tools()
        return [{"name": t.name, "description": t.description} for t in result.tools]

    async def close(self):
        if self._session:
            await self._session.__aexit__(None, None, None)
        if self._cm:
            await self._cm.__aexit__(None, None, None)


# ════════════════════════════════════════════════════════════════
# OPENAI REASONING CALL
# ════════════════════════════════════════════════════════════════

async def call_llm(messages: list) -> str:
    """Call GPT-4o for agent reasoning. Returns the assistant message text."""
    if not OPENAI_API_KEY:
        return json.dumps({
            "thought": "OPENAI_API_KEY not set — using rule-based fallback",
            "action": "complete",
            "action_input": {},
            "final_answer": None
        })
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o",
                "max_tokens": 1000,
                "temperature": 0.2,
                "messages": messages,
            },
        )
        data = resp.json()
        return data["choices"][0]["message"]["content"]


# ════════════════════════════════════════════════════════════════
# REACT AGENT LOOP
# ════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are PlayGuard AI — an autonomous agent that helps developers get their Android apps approved on Google Play Store.

You have access to these MCP tools:
- scan_apk(filepath): Scan APK/AAB for policy violations. ALWAYS call this first.
- check_permissions(permissions): Deep-analyze permission risks and Data Safety requirements.
- generate_store_listing(app_name, package_name, features): Draft a policy-compliant store listing.
- build_data_safety_checklist(permissions, has_ads, has_payments, has_account): Build the Data Safety form checklist.
- generate_fix_plan(findings): Convert findings into a prioritized fix plan with effort estimates.

AGENT RULES:
1. Always start with scan_apk to understand the app's state.
2. If there are permissions, call check_permissions next.
3. Call generate_fix_plan to produce actionable fixes from findings.
4. Call build_data_safety_checklist to produce the compliance checklist.
5. Optionally call generate_store_listing if store listing help is requested.
6. Stop when you have a complete picture — do NOT keep calling tools unnecessarily.
7. Maximum 8 tool calls per session.

Respond ONLY with valid JSON (no markdown) in this exact format:
{
  "thought": "Your reasoning about what to do next",
  "action": "tool_name OR complete",
  "action_input": { "param": "value" },
  "observation_summary": "Summary of what you learned from the last tool result (null on first turn)"
}

When all needed tools have been called, set action to "complete" and action_input to {}.
"""


async def run_agent(filepath: str, app_features: str = "", has_ads: bool = False,
                    has_payments: bool = False, has_account: bool = True) -> dict:
    """
    Main agent entry point.
    Runs the ReAct loop until the agent decides it's done (action=complete)
    or hits the max step limit.
    Returns the full agent trace + consolidated report.
    """
    mcp = MCPSession()
    await mcp.start()

    steps = []
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    scan_result = None
    perm_result = None
    fix_result = None
    checklist_result = None
    listing_result = None
    max_steps = 8

    # Initial user goal
    user_goal = (
        f"Analyze the Android app at filepath: {filepath}. "
        f"App features: {app_features or 'not specified'}. "
        f"Has ads: {has_ads}. Has payments/IAP: {has_payments}. Has user accounts: {has_account}. "
        "Goal: produce a complete Play Store submission readiness report. "
        "Call the appropriate tools in the right order and stop when done."
    )
    messages.append({"role": "user", "content": user_goal})

    for step_num in range(1, max_steps + 1):
        # ── Reason ────────────────────────────────────────────
        if OPENAI_API_KEY:
            llm_response = await call_llm(messages)
            try:
                clean = re.sub(r'^```json\s*', '', llm_response.strip())
                clean = re.sub(r'\s*```$', '', clean.strip())
                agent_decision = json.loads(clean)
            except Exception:
                agent_decision = _rule_based_next_step(
                    step_num, scan_result, perm_result, fix_result, checklist_result,
                    filepath=filepath, has_ads=has_ads, has_payments=has_payments, has_account=has_account
                )
        else:
            llm_response = ""
            agent_decision = _rule_based_next_step(
                step_num, scan_result, perm_result, fix_result, checklist_result,
                filepath=filepath, has_ads=has_ads, has_payments=has_payments, has_account=has_account
            )

        thought = agent_decision.get("thought", "")
        action = agent_decision.get("action", "complete")
        action_input = agent_decision.get("action_input", {})

        step_log = {
            "step": step_num,
            "thought": thought,
            "action": action,
            "action_input": action_input,
            "observation": None,
            "error": None,
        }

        if action == "complete" or step_num == max_steps:
            step_log["action"] = "complete"
            steps.append(step_log)
            break

        # ── Act ───────────────────────────────────────────────
        try:
            # Inject filepath if tool needs it and it's missing
            if action == "scan_apk" and "filepath" not in action_input:
                action_input["filepath"] = filepath

            observation = await mcp.call_tool(action, action_input)
            step_log["observation"] = observation

            # Cache key results
            if action == "scan_apk":
                scan_result = observation
            elif action == "check_permissions":
                perm_result = observation
            elif action == "generate_fix_plan":
                fix_result = observation
            elif action == "build_data_safety_checklist":
                checklist_result = observation
            elif action == "generate_store_listing":
                listing_result = observation

        except Exception as e:
            step_log["error"] = str(e)
            observation = {"error": str(e)}

        steps.append(step_log)

        # ── Feed observation back to agent ────────────────────
        obs_text = json.dumps(observation, indent=2)[:800]
        messages.append({"role": "assistant", "content": llm_response})
        messages.append({"role": "user", "content": f"Tool result:\n{obs_text}\n\nContinue."})

    await mcp.close()

    # ── Build final consolidated report ───────────────────────
    return _build_report(steps, scan_result, perm_result, fix_result, checklist_result, listing_result, filepath)


def _rule_based_next_step(step, scan, perm, fix, checklist,
                          filepath="", has_ads=False, has_payments=False, has_account=True):
    """Fallback when LLM unavailable — deterministic tool sequence."""
    if step == 1 or scan is None:
        return {"thought": "Start by scanning the APK for policy violations",
                "action": "scan_apk",
                "action_input": {"filepath": filepath},
                "observation_summary": None}
    if perm is None and scan and scan.get("permissions"):
        return {"thought": "Analyze the permissions found by the scan",
                "action": "check_permissions",
                "action_input": {"permissions": scan["permissions"]},
                "observation_summary": None}
    if fix is None and scan:
        return {"thought": "Convert findings into a prioritized fix plan",
                "action": "generate_fix_plan",
                "action_input": {"findings": scan.get("findings", [])},
                "observation_summary": None}
    if checklist is None:
        perms = scan.get("permissions", []) if scan else []
        return {"thought": "Build the Data Safety form checklist",
                "action": "build_data_safety_checklist",
                "action_input": {
                    "permissions": perms,
                    "has_ads": has_ads,
                    "has_payments": has_payments,
                    "has_account": has_account,
                },
                "observation_summary": None}
    return {"thought": "All tools called — report is complete",
            "action": "complete", "action_input": {}, "observation_summary": None}


def _build_report(steps, scan, perm, fix, checklist, listing, filepath) -> dict:
    """Merge all tool outputs into a single structured report."""
    score = scan.get("score", 0) if scan else 0
    if score >= 85:
        verdict, verdict_class = "Likely to Pass", "pass"
    elif score >= 65:
        verdict, verdict_class = "Review Recommended", "warn"
    elif score >= 40:
        verdict, verdict_class = "High Rejection Risk", "danger"
    else:
        verdict, verdict_class = "Likely to be Rejected", "critical"

    findings = scan.get("findings", []) if scan else []

    return {
        "success": True,
        "agent_steps": len(steps),
        "agent_trace": steps,
        "score": score,
        "verdict": verdict,
        "verdict_class": verdict_class,
        "app_info": {
            "filepath": filepath,
            "package_name": scan.get("package_name") if scan else None,
            "app_name": scan.get("app_name") if scan else None,
            "version_name": scan.get("version_name") if scan else None,
            "target_sdk": scan.get("target_sdk") if scan else None,
            "min_sdk": scan.get("min_sdk") if scan else None,
            "format": "AAB" if (scan and scan.get("is_aab")) else "APK",
            "is_signed": scan.get("is_signed") if scan else False,
            "total_permissions": len(scan.get("permissions", [])) if scan else 0,
        },
        "counts": {
            "critical": sum(1 for f in findings if f["severity"] == "CRITICAL"),
            "high": sum(1 for f in findings if f["severity"] == "HIGH"),
            "medium": sum(1 for f in findings if f["severity"] == "MEDIUM"),
            "passed": sum(1 for f in findings if f["severity"] == "PASS"),
        },
        "findings": findings,
        "permission_analysis": perm,
        "fix_plan": fix,
        "data_safety_checklist": checklist,
        "store_listing_draft": listing,
    }
