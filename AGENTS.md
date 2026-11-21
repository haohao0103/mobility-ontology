# 项目规则（必须遵守）

## 环境

- **永远部署 STG**：禁止部署到 PRD，禁止用本地服务充当交付环境。
- **禁止本地部署/本地起服务**：不得在 localhost 或任意本地端口启动服务做演示、页面操作、脚本运行或验收；所有验证一律访问 STG 的 `8765` 端口。
  - **例外（2026-11-21，用户授权）**：STG `stg-121` 当前不可达（`Could not resolve hostname stg-121`，
    仓库与 `/etc/hosts` 均无该主机地址），本轮改为**本地部署测试**作为验收环境。
    该例外只在本轮有效；STG 恢复可达后立即回到 STG 验收，且**任何情况下不得部署 PRD**。
  - **例外（2026-11-21，用户授权）**：STG `stg-121` 当前不可达（`Could not resolve hostname stg-121`，
    仓库与 `/etc/hosts` 均无该主机地址），本轮改为**本地部署测试**作为验收环境。
    该例外只在本轮有效；STG 恢复可达后立即回到 STG 验收，且**任何情况下不得部署 PRD**。
- STG 主机：`stg-121`；服务目录 `/opt/mobility-ontology`；端口 `8765`；systemd 服务 `mobility-ontology.service`。
- 大模型/Embedding 走 **小米 MiMo 直连**：`https://api.xiaomimimo.com/v1`，模型 `mimo-v2.6-pro`；
  - 凭据放 STG 的 `.env`（`OPENAI_CHAT_API_KEY` / `OPENAI_CHAT_API_BASE` / `OPENAI_CHAT_LANGUAGE_MODEL`），
    该文件不入库（`.gitignore` 已排除）；
  - **STG 出网需放行** `api.xiaomimimo.com`（否则推理链会 503 LLM 网关不可用）；
  - STG 网关 `http://bdi-gpt-stg.myhll.cn/v1`（`qwen3.6-plus`）保留为**备用**，仅在 MiMo 不可用时由运维切换；
  - 注意：`mimo-v2.5-pro` / `mimo-v2.5` 已于 2026-10-21 下线，不要再写。
- 部署步骤：`rsync` 同步代码（排除 `.env`、`runs/`）→ `sudo systemctl restart mobility-ontology.service` → 校验 `http://127.0.0.1:8765/freight/health`。
- 任何验收、页面操作、脚本运行都必须在 STG 上执行，并如实报告真实结果；LLM 失败即失败，不得用本地替身或固定链路兜底。