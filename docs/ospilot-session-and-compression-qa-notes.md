# OWLS 会话与上下文压缩 Q&A 笔记

## 1. OWLS 的上下文压缩机制是什么

结论：
- OWLS 使用阈值触发的上下文压缩，而不是固定条数截断。
- 主流程是 头尾保护 + 中段结构化摘要 + 工具调用配对修复。
- 压缩目标是保留可继续执行所需信息，而不是仅追求最小 token。

关键实现：
- 压缩器初始化与阈值计算：context_length、threshold_tokens、tail_token_budget
- 位置：[agent/context_compressor.py](../agent/context_compressor.py#L86)
- 位置：[agent/context_compressor.py](../agent/context_compressor.py#L91)
- 位置：[agent/context_compressor.py](../agent/context_compressor.py#L96)

- 压缩主入口：
- 位置：[agent/context_compressor.py](../agent/context_compressor.py#L545)

- 结构化摘要模板包含 Goal、Progress、Next Steps、Critical Context 等：
- 位置：[agent/context_compressor.py](../agent/context_compressor.py#L276)
- 位置：[agent/context_compressor.py](../agent/context_compressor.py#L282)
- 位置：[agent/context_compressor.py](../agent/context_compressor.py#L296)
- 位置：[agent/context_compressor.py](../agent/context_compressor.py#L299)


## 2. 会进行多次迭代压缩吗

结论：会。至少有三层迭代机制。

1) Preflight 压缩可循环多次
- 在正式请求前，如果历史过大，会做最多 3 次 preflight 压缩。
- 位置：[run_agent.py](../run_agent.py#L6374)

2) 运行期报错触发压缩重试
- 遇到 413 或 context length 类错误时，会进入压缩重试，默认上限 3 次。
- 位置：[run_agent.py](../run_agent.py#L6584)
- 位置：[run_agent.py](../run_agent.py#L7187)
- 位置：[run_agent.py](../run_agent.py#L7289)

3) 摘要本身增量迭代
- 有 previous_summary 时，不是重写摘要，而是合并新增回合。
- 位置：[agent/context_compressor.py](../agent/context_compressor.py#L264)
- 位置：[agent/context_compressor.py](../agent/context_compressor.py#L361)


## 3. 压缩效果目标是什么

结论：
- 目标是让会话在上下文窗口内可持续运行，避免重复劳动与关键信息丢失。
- 摘要预算是动态分配，不是固定长度。

预算相关常量：
- 最小摘要 token：2000
- 摘要比例：20%
- 上限：12000
- 位置：[agent/context_compressor.py](../agent/context_compressor.py#L38)
- 位置：[agent/context_compressor.py](../agent/context_compressor.py#L40)
- 位置：[agent/context_compressor.py](../agent/context_compressor.py#L42)


## 4. 会话内存 buffer 会有多大

结论：
- 没有一个固定 MB 上限。
- 主要由模型 context_length 和 threshold 控制。
- 达到阈值后触发压缩。

关键位置：
- 阈值配置默认 50%：
- 位置：[run_agent.py](../run_agent.py#L1154)

- 阈值计算：
- 位置：[agent/context_compressor.py](../agent/context_compressor.py#L91)

补充保护：
- 超大工具输出会截断，避免单条消息把上下文打爆。
- 位置：[run_agent.py](../run_agent.py#L5615)
- 位置：[tools/terminal_tool.py](../tools/terminal_tool.py#L1151)


## 5. 完整会话与摘要会一起保留吗

结论：
- 在活跃会话上下文中，中段通常被摘要替换，不会完整历史和摘要都同时塞在同一份活跃消息里。
- 但完整历史会通过旧会话在持久化层保留。

关键位置：
- 摘要插入到压缩后的 messages：
- 位置：[agent/context_compressor.py](../agent/context_compressor.py#L640)

- 压缩时结束旧会话并创建新会话：
- 位置：[run_agent.py](../run_agent.py#L5329)
- 位置：[run_agent.py](../run_agent.py#L5334)


## 6. 会话退出后，摘要信息会保留到 DB 吗

结论：会。

原因：
- 摘要先进入消息列表，然后退出时统一走持久化。
- 持久化路径是 persist_session -> flush_messages_to_session_db。

关键位置：
- 退出前统一持久化：
- 位置：[run_agent.py](../run_agent.py#L8148)

- DB flush：
- 位置：[run_agent.py](../run_agent.py#L1826)
- 位置：[run_agent.py](../run_agent.py#L1847)

- 追加到 SQLite：
- 位置：[run_agent.py](../run_agent.py#L1856)


## 7. 会话元信息重开是什么意思

结论：
- 元信息重开是恢复会话状态或映射，不是重写历史内容。

CLI 场景：
- 将会话 ended_at 和 end_reason 清空，重新标记为活跃。
- 位置：[cli.py](../cli.py#L2244)
- 位置：[cli.py](../cli.py#L3079)

Gateway 场景：
- 将当前 session_key 指向目标 session_id，并结束旧会话元信息。
- 位置：[gateway/session.py](../gateway/session.py#L856)


## 8. 会话恢复流程图文件

已产出 SVG：
- [owls-session-recovery-flow.svg](../owls-session-recovery-flow.svg)

图内容覆盖：
- 恢复入口
- 历史加载来源优先级
- 恢复后执行与预压缩分支
- session_id 迁移
- DB/磁盘/内存持久化闭环


## 9. 快速结论（适合复述）

- OWLS 的压缩是可持续执行导向，不是简单裁剪。
- 支持多层次迭代压缩，并且摘要会增量更新。
- 恢复会话时优先重建历史与系统提示一致性。
- 摘要会随会话持久化进入 DB。
- 元信息重开是状态层动作，不是内容层重写。


## 10. 今日补充：多代理与会话/上下文存储

结论：
- OWLS 存在多代理能力，形态是父代理通过 delegation 工具拉起子代理执行任务。
- 子代理上下文与父代理隔离，父代理只接收子代理总结，不接收中间推理细节。
- 子代理会独立持久化会话（独立 session_id），不会自动并入父会话历史。

关键位置：
- 子代理工具与限制说明（含上下文隔离语义）：
- 位置：[tools/delegate_tool.py](../tools/delegate_tool.py#L1)

- 子代理创建（共享 session_db 连接，但子代理实例独立）：
- 位置：[tools/delegate_tool.py](../tools/delegate_tool.py#L203)

- 子代理运行入口：
- 位置：[tools/delegate_tool.py](../tools/delegate_tool.py#L274)

- 会话持久化主路径：
- 位置：[run_agent.py](../run_agent.py#L1813)
- 位置：[run_agent.py](../run_agent.py#L8148)


## 11. 今日补充：skill 触发、创建与迭代优化

结论：
- skill 的创建/优化决策主要由系统提示词与工具 schema 的语义引导触发。
- 真正落地由 skill_manage 工具执行（create/patch/edit/write_file 等）。
- 优先 patch 的小步迭代策略有助于低风险持续优化。
- 修改成功后会清理技能提示缓存，后续回合可立即使用新版本。

关键位置：
- skill_manage 主入口：
- 位置：[tools/skill_manager_tool.py](../tools/skill_manager_tool.py#L528)

- schema 中的创建/更新语义指导：
- 位置：[tools/skill_manager_tool.py](../tools/skill_manager_tool.py#L593)

- 成功后清理 skills prompt cache：
- 位置：[tools/skill_manager_tool.py](../tools/skill_manager_tool.py#L580)

- 相关测试（创建与迭代 patch）：
- 位置：[tests/tools/test_skill_manager_tool.py](../tests/tools/test_skill_manager_tool.py#L415)
- 位置：[tests/tools/test_skill_manager_tool.py](../tests/tools/test_skill_manager_tool.py#L443)


## 12. 今日补充：后台异步触发为何不阻塞主流程

结论：
- /reset 与 /resume 在切换会话时会发起后台 flush（fire-and-forget）。
- 耗时逻辑通过 run_in_executor 放到线程池执行，不阻塞网关事件循环。
- 后台异常被捕获与隔离，不会中断主聊天链路。
- 除命令触发外，会话过期 watcher 也会主动触发 flush。

关键位置：
- /reset 触发后台 flush：
- 位置：[gateway/run.py](../gateway/run.py#L2982)

- /resume 切会话前触发后台 flush：
- 位置：[gateway/run.py](../gateway/run.py#L4477)

- 异步入口与线程池下沉：
- 位置：[gateway/run.py](../gateway/run.py#L738)
- 位置：[gateway/run.py](../gateway/run.py#L745)

- 会话过期 watcher 主动触发：
- 位置：[gateway/run.py](../gateway/run.py#L1265)
- 位置：[gateway/run.py](../gateway/run.py#L1284)


## 13. 今日补充：为什么会话存储选数据库而非纯 JSON

结论：
- 主要原因是需要可检索、并发稳定、结构化治理与恢复一致性。
- SQLite + FTS5 支持跨会话全文检索，且可承载会话元数据与消息索引。
- 当前方案是数据库为主、JSONL 兼容过渡，避免迁移期间历史截断。

关键位置：
- 数据库设计与 FTS5：
- 位置：[owls_state.py](../owls_state.py#L1)
- 位置：[owls_state.py](../owls_state.py#L82)

- 恢复时 DB/JSONL 比较并取更完整源：
- 位置：[gateway/session.py](../gateway/session.py#L982)

- 去重写入与一致性控制：
- 位置：[run_agent.py](../run_agent.py#L1826)


## 14. 今日新增图示产物

- skill 后台异步沉淀时序图（中文）：
- 文件：[docs/skill_async_flush_sequence.svg](skill_async_flush_sequence.svg)


## 15. 今日补充：编译仓库后“下一步动作提醒”是如何实现的

结论：
- 不是单独的“提醒器”模块，而是“工具执行结果 + 模型总结”联合实现。
- 对长任务（尤其后台进程）通过 watcher 持续回传状态，模型再基于最新状态给出下一步建议。

关键位置：
- 后台 watcher 入口与恢复：
- 位置：[gateway/run.py](../gateway/run.py#L1244)
- 位置：[gateway/run.py](../gateway/run.py#L2704)

- 进程状态通知逻辑：
- 位置：[gateway/run.py](../gateway/run.py#L5149)


## 16. 今日补充：skill 创建是否“必须用户确认”

结论：
- 代码层面不是强制确认；当前是 schema 语义建议“先确认”。
- 即：模型被建议与用户确认，但工具接口本身没有硬性 confirm 参数门槛。

关键位置：
- skill_manage 分发（无强制确认字段）：
- 位置：[tools/skill_manager_tool.py](../tools/skill_manager_tool.py#L528)

- schema 里的指导语句（建议确认）：
- 位置：[tools/skill_manager_tool.py](../tools/skill_manager_tool.py#L610)


## 17. 今日补充：skill 迭代优化触发机制是否来自提示词语义

结论：
- 是。触发主要来自系统提示词/工具 schema 的语义引导。
- 一旦模型决策执行，落地由 skill_manage 工具完成；成功后清缓存使其即时生效。

关键位置：
- schema 触发语义：
- 位置：[tools/skill_manager_tool.py](../tools/skill_manager_tool.py#L593)

- 执行入口：
- 位置：[tools/skill_manager_tool.py](../tools/skill_manager_tool.py#L528)

- 生效机制（缓存清理）：
- 位置：[tools/skill_manager_tool.py](../tools/skill_manager_tool.py#L580)


## 18. 今日补充：/reset、/resume 何时触发以及其他触发场景

结论：
- /reset（或 /new）在用户发起会话重置时触发，先异步 flush，再重置会话。
- /resume 在会话切换时触发，切换前会对当前会话异步 flush。
- 除命令外，会话过期 watcher 也会主动触发 flush。

关键位置：
- /reset 处理：
- 位置：[gateway/run.py](../gateway/run.py#L2982)

- /resume 处理：
- 位置：[gateway/run.py](../gateway/run.py#L4477)

- 会话过期 watcher：
- 位置：[gateway/run.py](../gateway/run.py#L1265)


## 19. 今日新增图示补充说明

结论：
- 图中的“临时 Agent”是专门用于后台 flush 的独立 AIAgent 实例。
- 图上写“/reset /resume”是触发示例，不是唯一触发条件（过期 watcher 也会触发）。

关键位置：
- 临时 agent 创建：
- 位置：[gateway/run.py](../gateway/run.py#L661)

- flush 提示词（仅用 memory 与 skill_manage）：
- 位置：[gateway/run.py](../gateway/run.py#L719)


## 20. 今日新增图示产物（补充）

- 上下文压缩机制架构图：
- 文件：[owls-context-compression-architecture.svg](../owls-context-compression-architecture.svg)

- 会话恢复流程图：
- 文件：[owls-session-recovery-flow.svg](../owls-session-recovery-flow.svg)
