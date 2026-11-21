# 来源：W3C SHACL Test Suite（随 pyshacl 0.40.2 sdist 分发）

- 套件：SHACL Test Suite (core + sparql)
- 获取路径：`pip download pyshacl --no-binary :all: --no-deps` → `pyshacl-0.40.2.tar.gz`
  → `test/resources/sht_tests/`
- 原始出处：https://github.com/w3c/data-shapes （本机网络 GitHub 不可达，
  经 PyPI 镜像的 pyshacl sdist 取得同一份 W3C 套件）
- 许可：W3C Software and Document License
- 复现：`python3 scripts/benchmark/shacl_suite.py`（默认读本目录）
