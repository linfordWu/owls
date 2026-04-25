# OWLS Architecture — Agent Team Edition

> Last updated: 2026-04-22
> Owner: @architect

## 1. High-Level Module Map

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              User Interfaces                                  │
├─────────────┬─────────────┬─────────────┬─────────────────────────────────────┤
│   CLI       │   Web       │  Gateway    │   SSH (agent-shell)                 │
│  (cli.py)   │  (web/)     │ (gateway.py)│  (owls_cli/agent_shell.py)       │
└──────┬──────┴──────┬──────┴──────┬──────┴─────────────────┬───────────────────┘
       │             │             │                        │
       └─────────────┴─────────────┴────────────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │   AIAgent.chat()  │  ← run_agent.py
                    └─────────┬─────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
    ┌─────▼─────┐      ┌─────▼─────┐      ┌─────▼─────┐
    │  Planner  │      │  Context  │      │  Memory   │
    │(planner.py)│     │(prompt_*) │      │(memory_*) │
    └─────┬─────┘      └───────────┘      └───────────┘
          │
    ┌─────▼─────┐
    │ Executor  │ ← PlanStateMachine.execute()
    └─────┬─────┘
          │
    ┌─────▼─────┐      ┌──────────────────────────────┐
    │ Verifier  │─────►│  CheckpointManager.restore() │
    └─────┬─────┘      └──────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Tool Execution Layer                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   AgentShell.run()                                                          │
│        │                                                                    │
│        ▼                                                                    │
│   ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐        │
│   │ InterceptorChain │───►│ PolicyInterceptor│───►│ApprovalInterceptor│      │
│   │(interceptor_chain)│   │(sandbox_profile) │   │  (approval.py)   │       │
│   └─────────────────┘    └─────────────────┘    └─────────────────┘        │
│        │                              │                    │                │
│        ▼                              ▼                    ▼                │
│   ┌────────────────────────────────────────────────────────────────────┐   │
│   │                     SandboxPolicy.apply()                          │   │
│   │  inspect-ro → Landlock RO  │  diag-net → unshare+iptables          │   │
│   │  mutate-config → Landlock RW (limited) │  full-mutate → checkpoint │   │
│   └────────────────────────────────────────────────────────────────────┘   │
│        │                                                                    │
│        ▼                                                                    │
│   ┌────────────────────────────────────────────────────────────────────┐   │
│   │                     OutputRedirector.run()                         │   │
│   │   mode = display+log (default)  →  stdout+audit_logger             │   │
│   └────────────────────────────────────────────────────────────────────┘   │
│        │                                                                    │
│        ▼                                                                    │
│   ┌────────────────────────────────────────────────────────────────────┐   │
│   │                     TerminalTool / Registry.dispatch()             │   │
│   └────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Audit & Observability                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   AuditLogger.log_event() ──► ~/.owls/audit/audit_events.YYYY-MM-DD.jsonl│
│                                                                             │
│   FastAPI endpoints:                                                        │
│     GET /api/audit?start=&end=&event_type=&session_id=&risk_level=          │
│     WebSocket /ws/approvals  → push pending approvals to frontend           │
│     WebSocket /ws/shell      → bidirectional terminal stream                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2. Call Chains

### 2.1 AgentShell → Execution

```
AgentShell.run()
  ├── read user input
  ├── AIAgent.chat(input)  →  tool_calls JSON
  ├── build ToolContext
  ├── InterceptorChain.intercept(ctx)
  │     ├── PolicyInterceptor    → checks sandbox_profile vs tool capabilities
  │     ├── ApprovalInterceptor  → calls approval.check_all_command_guards()
  │     └── ValidationInterceptor → checks previous verifier_command result
  ├── if Action.type != "proceed":
  │     ├── freeze  → FrozenTaskStore.add() + wait for user
  │     └── rollback → CheckpointManager.restore() + abort downstream nodes
  ├── SandboxPolicy.apply(profile, task_id)
  │     └── on failure → fallback to DockerEnvironment
  ├── OutputRedirector.run(cmd, cwd, env)
  │     └── subprocess.Popen + select() + audit_logger.log_event()
  └── TerminalTool / Registry.dispatch()
```

### 2.2 Planner → Executor → Verifier → Checkpoint

```
Planner.generate_plan(user_message, available_tools)
  ├── heuristic: complex task? ("并且"/"然后" or >3 expected tools)
  ├── call_llm() → structured JSON → list[PlanNode]
  └── TodoStore.write(plan)

PlanStateMachine.execute(agent)
  ├── topological sort of PlanNode[]
  ├── for each ready batch:
  │     ├── asyncio.gather / ThreadPoolExecutor → parallel execution
  │     ├── per node:
  │     │     ├── run tool calls
  │     │     ├── run verifier_command (exit 0 = success)
  │     │     ├── on failure:
  │     │     │     ├── retry (up to max_retries)
  │     │     │     ├── still fail → CheckpointManager.restore()
  │     │     │     └── mark "rolled_back", abort downstream
  │     │     └── on success → mark "success", unblock dependents
  │     └── TodoStore.update(status)
  └── ExecutionResult

Post-execution (run_agent.py):
  ├── experience_store.record(...)
  ├── reflection_engine.reflect(experience)
  └── if 50 successful experiences:
        reflection_engine.consolidate_fragments() → prompt fragment files
```

## 3. Audit Hook Points

| # | File | Function | Event Type | Notes |
|---|------|----------|-----------|-------|
| 1 | `model_tools.py` | `handle_function_call()` start | `tool_call` | Log tool name + args |
| 2 | `model_tools.py` | `handle_function_call()` end | `tool_result` | Log result summary |
| 3 | `tools/approval.py` | After approval decision | `approval_decision` | Log approved/denied + pattern_key |
| 4 | `tools/terminal_tool.py` | Before command execution | `command_execution` | Log command + cwd + env_type |
| 5 | `tools/terminal_tool.py` | After command execution | `command_result` | Log exit code + truncated output |
| 6 | `tools/checkpoint_manager.py` | After `ensure_checkpoint()` | `checkpoint_create` | Log commit hash |
| 7 | `tools/checkpoint_manager.py` | After `restore()` | `checkpoint_restore` | Log restored commit hash |
| 8 | `tools/interceptor_chain.py` | On freeze/rollback | `policy_violation` | Log reason + suggested_fix |
| 9 | `owls_cli/main.py` | WebSocket connect | `session_start` | Log session_id + user + IP |
| 10 | `owls_cli/main.py` | WebSocket disconnect | `session_end` | Log session_id |
| 11 | `owls_cli/main.py` | `/api/*` auth success | `auth_success` | Log user + endpoint |
| 12 | `owls_cli/main.py` | `/api/*` auth failure | `auth_failure` | Log IP + attempted endpoint |

## 4. Data Flow — Web Frontend

```
Browser
  ├── GET /api/audit  → AuditLogger.query() → list[AuditEvent]
  ├── GET /api/plans  → PlanStateMachine.serialize() → list[PlanNode]
  ├── POST /api/plans/{id}/retry → PlanStateMachine.retry_node(id)
  ├── POST /api/plans/{id}/skip  → PlanStateMachine.skip_node(id)
  ├── WS  /ws/shell    → AgentShell PTY bidirectional stream
  └── WS  /ws/approvals → approval events pushed by gateway notify callback
```

## 5. Sandbox Fallback Chain

```
apply_sandbox_profile(profile, task_id)
  ├── inspect-ro:
  │     ├── try: landlock_create_ruleset(RO)
  │     └── except: return False → caller uses DockerEnvironment fallback
  ├── diag-net:
  │     ├── try: os.unshare(CLONE_NEWNET) + iptables allow-list
  │     └── except: return False → caller uses DockerEnvironment fallback
  ├── mutate-config:
  │     ├── try: landlock_create_ruleset(RW on /etc, /opt, OWLS_HOME)
  │     └── except: return False → caller uses DockerEnvironment fallback
  └── full-mutate:
        ├── always: CheckpointManager.ensure_checkpoint()
        └── no Landlock needed
```

## 6. File Ownership Matrix

| File | Owner | Readers | Edits by others |
|------|-------|---------|-----------------|
| `agent/interfaces.py` | @architect | all | **NO** — request PR review |
| `agent/planner.py` | @agenteng | @frontend (for /api/plans) | via PR only |
| `agent/experience_store.py` | @agenteng | @agenteng | via PR only |
| `agent/reflection_engine.py` | @agenteng | @agenteng | via PR only |
| `tools/audit_logger.py` | @security | all (for log_event calls) | via PR only |
| `tools/interceptor_chain.py` | @security | @syseng | via PR only |
| `tools/sandbox_policy.py` | @syseng | @security | via PR only |
| `tools/output_redirector.py` | @syseng | @syseng | via PR only |
| `owls_cli/agent_shell.py` | @syseng | @security (for intercept hook) | via PR only |
| `gateway/bastion_proxy.py` | @syseng | @syseng | via PR only |
| `web/src/pages/*.tsx` | @frontend | @frontend | via PR only |
| `owls_cli/main.py` | shared | all | coordinated changes only |
| `model_tools.py` | shared | all | coordinated changes only |
| `run_agent.py` | shared | all | **minimal** — protect prompt caching |
