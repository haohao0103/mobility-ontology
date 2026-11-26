# 项目规则（必须遵守）

## 环境

- **永远部署 STG**：禁止部署到 PRD，禁止用本地服务充当交付环境。
- **禁止本地部署/本地起服务**：不得在 localhost 或任意本地端口启动服务做演示、页面操作、脚本运行或验收；所有验证一律访问 STG 的 `8765` 端口。
- STG 主机：`stg-121`；服务目录 `/opt/mobility-ontology`；端口 `8765`；systemd 服务 `mobility-ontology.service`。
- **STG 环境固定使用 STG 网关**：`http://bdi-gpt-stg.myhll.cn/v1`，模型 `qwen3.6-plus`；
  - 凭据放 STG 的 `.env`（`OPENAI_CHAT_API_KEY` / `OPENAI_CHAT_API_BASE` / `OPENAI_CHAT_LANGUAGE_MODEL`），
    该文件不入库（`.gitignore` 已排除）；
  - STG 的正式大模型出口就是这个网关，不是备用，不需要也不会切到 MiMo；
  - 排查 STG 大模型问题时不要用「MiMo 直连」作为判断标准。
- **只有本地部署才走小米 MiMo 直连**：`https://api.xiaomimimo.com/v1`，模型 `mimo-v2.6-pro`；
  - 注意：`mimo-v2.5-pro` / `mimo-v2.5` 已于 2026-10-21 下线，不要再写。
- 部署步骤：`rsync` 同步代码（排除 `.env`、`runs/`、`logs/`、`.venv/`）→ `sudo systemctl restart mobility-ontology.service` → 校验 `http://127.0.0.1:8765/freight/health`。
- 任何验收、页面操作、脚本运行都必须在 STG 上执行，并如实报告真实结果；LLM 失败即失败，不得用本地替身或固定链路兜底。