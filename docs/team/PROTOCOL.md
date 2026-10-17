# 跨会话协作协议 / Cross-session protocol

五个会话共享同一个工作目录和文件系统。它们之间**不共享对话上下文**，所以一切需要传递
的信息都必须落在这个目录的 Markdown 文件里。

```
docs/team/
├─ PROTOCOL.md     ← 本文件：规则
├─ LEDGER.md       ← 唯一需要频繁读写的文件（追加式）
└─ archive/        ← 条目过多时由 S5 归档，平时不用
```

## 硬规则

1. **LEDGER.md 是追加式的。** 只在文件末尾（或自己那一节）追加，**绝不删除或重写别人写的条目**。
   需要更正时追加一条新的，注明「更正 <时间戳> 的某条」。
2. **每条必须带会话标签和时间戳**，格式：

   ```
   ### [S2] 2026-10-18 14:32 — 标题
   ```

3. **文件所有权互不重叠。** 各自的写权限见下节。要改别人的文件，写进 LEDGER 的「请求」区，
   由拥有者执行。
4. **接口变更必须广播。** 任何人新增/重命名/删除一个被其他组件依赖的接口（PowerShell 函数、
   Python 函数、环境变量、打包清单、JSON 字段），必须在 LEDGER 的「契约」区登记一条。
5. **每次会话结束前更新自己的「状态」块**，写清：做完了什么、卡在哪、下一步。

## 文件所有权

| 会话 | 独占写入 |
|---|---|
| S1 调研 | `docs/research.md` |
| S2 Step1235 | `setup/`；`shared/QwenEnv.ps1`；`launcher/app.py`、`core.py`、`theme.py`、`icon.ico`、`qwen_launcher.spec`、`build_exe.bat`；`launcher/USER_GUIDE.md` |
| S3 工作流 | `shared/qwen_comfy.py`、`qwen_comfy_app.py`、`qwen_api.py`、`qwen_paths.py`、`qwen_sizing.py`、`qwen_launch.ps1`、`qwen_stop.ps1`、`mirrors.json`；`mixed/`、`mixed-vram/`、`turbo/`、`gguf/`、`native/` |
| S4 AI Agent | `launcher/agent.py`、`launcher/agent_panel.py` |
| S5 发布 | `_publish/`；仓库根的 `README.md`、`README.zh-CN.md`、`LICENSE`、`.gitignore`；release 产物 |

**公共只读**：所有人可以读任何文件，但不能改所有权之外的东西。

## LEDGER.md 的结构

```markdown
## 状态
### [S1] <时间戳>
（一句话现状 + 阻塞点）

## 请求
### [S3] <时间戳> → S2
（要什么、为什么、期望什么形式）

## 契约
### [S2] <时间戳>
（变了什么接口、谁受影响、需要谁配合）

## 结论
（已确认的事实，只增不改。S1 的调研结论也进这里，供 S2/S3 直接引用）
```

## 冲突处理

- **同一文件被两个会话同时需要改** → 写请求，由拥有者改。不要抢。
- **发现别人的改动引入 bug** → 写进「请求」，不要自己回滚。
- **需要重新打包 EXE** → 只有 S2 能做（它拥有 spec 和构建链）。其他人写完代码后在
  LEDGER 里请求 S2 重建。
