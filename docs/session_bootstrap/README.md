# 会话初始化提示词

把每个文件的内容**整段复制**到一个新会话的开头。各会话不共享对话上下文，
所有跨会话信息都通过 `docs/team/LEDGER.md` 传递。

| 会话 | 文件 | 负责 |
|---|---|---|
| S1 | [S1_research.md](S1_research.md) | 模型变体调研与硬件适配评估 |
| S2 | [S2_steps1235.md](S2_steps1235.md) | Step 1/2/3/5：环境、ComfyUI、模型下载、卸载、GUI 外壳、EXE 打包 |
| S3 | [S3_workflow.md](S3_workflow.md) | Step 4 生图工作流：图构建、采样、放置、多策略、HTTP API |
| S4 | [S4_agent.md](S4_agent.md) | Step 4 AI Agent：Provider SDK、读图探测、规划、画廊 |
| S5 | [S5_publish.md](S5_publish.md) | GitHub 同步、文档、release |

## 通信方式

三个（其实五个）会话都通过两个文件协作：

```
docs/team/PROTOCOL.md   规则（只读，除非要改规则）
docs/team/LEDGER.md     账本（追加式）：状态 / 请求 / 契约 / 结论
```

- **S1 的调研结论**进 LEDGER 的「结论」区，供 S2（下载哪些文件）和 S3（工作流怎么配）直接引用
- **S2 拥有打包权**。S3/S4 新增需要进 EXE 的脚本时，在「请求」区通知 S2 更新
  `BUNDLED_FILES` + spec
- **S2 拥有 `launcher/app.py`**。S4 不直接改它，接入点的改动写请求
- **S5 只发布不开发**。它从源码树同步到 `_publish/` 仓库，EXE 由 S2 提供

## 建议的启动顺序

1. **S1 先跑**——它产出的变体清单和硬件分档结论会被 S2、S3 直接采用
2. **S2 和 S3 可并行**——文件所有权不重叠
3. **S4 可以在 S3 定下 `/api/generate` 契约后开始**（契约已存在于 LEDGER，可直接开）
4. **S5 最后**——等其他会话的改动稳定后同步发布

## 每一轮的开始动作

新会话第一句建议：**"先读 docs/team/PROTOCOL.md 和 docs/team/LEDGER.md，然后告诉我
你理解到的当前状态和你的下一步。"** 这样能立刻确认它有没有正确加载上下文。
