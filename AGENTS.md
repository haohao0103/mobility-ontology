# 项目规则（必须遵守）

## 环境

- **永远部署 STG**：禁止部署到 PRD，禁止用本地服务充当交付环境。
- STG 主机：`stg-121`；服务目录 `/opt/mobility-ontology`；端口 `8765`；systemd 服务 `mobility-ontology.service`。
- 大模型/Embedding 只连 STG 网关：`http://bdi-gpt-stg.myhll.cn/v1`，模型 `qwen3.6-plus`。
- 部署步骤：`rsync` 同步代码（排除 `.env`、`runs/`）→ `sudo systemctl restart mobility-ontology.service` → 校验 `http://127.0.0.1:8765/freight/health`。
- 任何验收、页面操作、脚本运行都必须在 STG 上执行，并如实报告真实结果；LLM 失败即失败，不得用本地替身或固定链路兜底。